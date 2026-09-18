"""
Plivo Voice REST client: outbound calls, hangup, account health.
"""

from urllib.parse import urlencode

import plivo

from app.core.config import settings


class PlivoService:

    def __init__(self):
        missing = [name for name, value in (
            ("PLIVO_AUTH_ID", settings.plivo_auth_id), ("PLIVO_AUTH_TOKEN", settings.plivo_auth_token),
            ("PLIVO_PHONE_NUMBER", settings.plivo_phone_number), ("PUBLIC_BASE_URL", settings.public_base_url),
        ) if not value]
        if missing:
            raise ValueError(f"Missing configuration: {', '.join(missing)}")
        self.client = plivo.RestClient(settings.plivo_auth_id, settings.plivo_auth_token, timeout=15)

    @staticmethod
    def caller_id() -> str:
        return "".join(c for c in settings.plivo_phone_number if c.isdigit())

    @staticmethod
    def webhook(path: str, **params) -> str:
        query = urlencode({k: v for k, v in params.items() if v is not None})
        return f"{settings.base_url}/api/plivo/{path}" + (f"?{query}" if query else "")

    def send_sms(self, to: str, text: str) -> str:
        """Send an SMS using Plivo."""
        response = self.client.messages.create(
            src=self.caller_id(),
            dst="".join(c for c in to if c.isdigit()),
            text=text
        )
        return response.message_uuid[0] if response.message_uuid else "Unknown"

    def dial(self, phone: str, session_id: str, call_id: int, max_minutes: int, detect_voicemail: bool = False,
             from_number: str | None = None, endpoint: str = "answer") -> str:
        params = {"sid": session_id, "cid": call_id}
        response = self.client.calls.create(
            from_=from_number or self.caller_id(),
            to_="".join(c for c in phone if c.isdigit()),
            answer_url=self.webhook(endpoint, **params),
            answer_method="POST",
            ring_url=self.webhook("ring", **params),
            hangup_url=self.webhook("hangup", **params),
            ring_timeout=60,
            time_limit=max(60, max_minutes * 60),
            # Answering-machine detection gives false positives on Indian networks (caller tunes,
            # carrier announcements), so it is opt-in from the Agent settings.
            **({"machine_detection": "hangup", "machine_detection_time": 5000} if detect_voicemail else {}),
        )
        return response.request_uuid

    def transfer(self, call_uuid: str, session_id: str, call_id: int | None):
        """Move a live call (A leg) to XML that dials the agent's human transfer number."""
        self.client.calls.update(call_uuid, legs="aleg", aleg_url=self.webhook("transfer", sid=session_id, cid=call_id),
                                 aleg_method="POST")

    def hangup(self, uuid: str):
        try:
            self.client.calls.delete(uuid)
        except plivo.exceptions.ResourceNotFoundError:
            # Not answered yet: cancel the outbound request instead
            self.client.calls.cancel(uuid)

    # ---------------- inbound routing ----------------

    INBOUND_APP = "psyber-voice-inbound"

    @staticmethod
    def _app_id(ref: str | None) -> str | None:
        """'/v1/Account/X/Application/123/' -> '123'."""
        parts = [p for p in (ref or "").split("/") if p]
        return parts[-1] if parts else None

    def inbound_status(self, number: str | None = None) -> dict:
        number = "".join(c for c in (number or settings.plivo_phone_number) if c.isdigit())
        num = self.client.numbers.get(number)
        app_id = self._app_id(getattr(num, "application", None))
        app = self.client.applications.get(app_id) if app_id else None
        answer_url = getattr(app, "answer_url", "") if app else ""
        expected = self.webhook("answer")
        from app.services.settings_service import SettingsService
        previous = SettingsService().get_state(f"plivo_previous_app.{number}")
        return {
            "number": "+" + number, "voice_enabled": getattr(num, "voice_enabled", None),
            "app_id": app_id, "app_name": getattr(app, "app_name", None) if app else None, "answer_url": answer_url,
            "connected": answer_url == expected, "expected_answer_url": expected,
            "previous_app": previous,
        }

    def connect_inbound(self, number: str | None = None) -> dict:
        """Point the number at this app's webhooks. The app it used before is remembered so it can be restored."""
        from app.services.settings_service import SettingsService
        number = "".join(c for c in (number or settings.plivo_phone_number) if c.isdigit())
        status = self.inbound_status(number)
        urls = dict(answer_url=self.webhook("answer"), answer_method="POST",
                    hangup_url=self.webhook("hangup"), hangup_method="POST")
        ours = next((a for a in self.client.applications.list(limit=20) if getattr(a, "app_name", "") == self.INBOUND_APP), None)
        if ours:
            self.client.applications.update(ours.app_id, **urls)
            app_id = ours.app_id
        else:
            app_id = self.client.applications.create(app_name=self.INBOUND_APP, **urls).app_id
        if status["app_id"] and status["app_id"] != app_id and not status["previous_app"]:
            SettingsService().set_state(f"plivo_previous_app.{number}", {"app_id": status["app_id"], "app_name": status["app_name"]})
        self.client.numbers.update(number, app_id=app_id)
        return self.inbound_status(number)

    def restore_inbound(self, number: str | None = None) -> dict:
        from app.services.settings_service import SettingsService
        number = "".join(c for c in (number or settings.plivo_phone_number) if c.isdigit())
        state = SettingsService()
        previous = state.get_state(f"plivo_previous_app.{number}")
        if not previous:
            raise ValueError("No previous Plivo application was saved for this number.")
        self.client.numbers.update(number, app_id=previous["app_id"])
        state.set_state(f"plivo_previous_app.{number}", None)
        return self.inbound_status(number)

    def health(self) -> dict:
        account = self.client.account.get()
        number = self.client.numbers.get(self.caller_id())
        return {"account": getattr(account, "name", None), "credits": getattr(account, "cash_credits", None),
                "number": "+" + number.number, "alias": getattr(number, "alias", None),
                "voice_enabled": getattr(number, "voice_enabled", None)}
