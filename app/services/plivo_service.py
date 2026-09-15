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

    def dial(self, phone: str, session_id: str, call_id: int, max_minutes: int, detect_voicemail: bool = False,
             from_number: str | None = None) -> str:
        params = {"sid": session_id, "cid": call_id}
        response = self.client.calls.create(
            from_=from_number or self.caller_id(),
            to_="".join(c for c in phone if c.isdigit()),
            answer_url=self.webhook("answer", **params),
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

    def hangup(self, uuid: str):
        try:
            self.client.calls.delete(uuid)
        except plivo.exceptions.ResourceNotFoundError:
            # Not answered yet: cancel the outbound request instead
            self.client.calls.cancel(uuid)

    def health(self) -> dict:
        account = self.client.account.get()
        number = self.client.numbers.get(self.caller_id())
        return {"account": getattr(account, "name", None), "credits": getattr(account, "cash_credits", None),
                "number": "+" + number.number, "alias": getattr(number, "alias", None),
                "voice_enabled": getattr(number, "voice_enabled", None)}
