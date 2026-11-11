"""WhatsApp Cloud API channel adapter.

The WhatsApp Cloud API / Composio logic itself lives in app.services.whatsapp_cloud_service (kept
separate from app.services.whatsapp_service, which is the pre-existing wa-bridge/WhatsApp-Web
integration already live in production): existing call sites and tests (tests/test_whatsapp_channel.py,
app/api/whatsapp_cloud.py, scheduler.py) monkeypatch and import its module-level functions directly, so
it stays the source of truth rather than being physically relocated. This module is the ChannelAdapter-
shaped face onto it for the multi-channel plumbing, and re-exports the same functions so
`app.services.channels.whatsapp` is a drop-in for any new caller.
"""

import asyncio

from app.services import whatsapp_cloud_service as _wa
from app.services.channels.base import ChannelAdapter


class WhatsAppAdapter(ChannelAdapter):
    def send(self, to: str, text: str, **kwargs) -> dict:
        return asyncio.run(_wa.send_whatsapp_message(to, text, **kwargs))

    def send_media(self, to: str, media_id: str, **kwargs) -> dict:
        return _wa.send_whatsapp_document(
            to, kwargs.get("agent_id"), kwargs.get("lead_id"), media_id,
            filename=kwargs.get("filename"), caption=kwargs.get("caption"),
            phone_number_id=kwargs.get("phone_number_id"),
        )

    def parse_webhook(self, payload: dict) -> list[dict]:
        return _wa.parse_webhook(payload)

    def verify(self, body: bytes, header: str | None = None) -> bool:
        return _wa.verify_signature(body, header)


# Re-exported so other modules may import from either `channels.whatsapp` or `whatsapp_service`.
send_whatsapp_message = _wa.send_whatsapp_message
parse_webhook = _wa.parse_webhook
verify_signature = _wa.verify_signature
handle_inbound = _wa.handle_inbound
close_idle_chats = _wa.close_idle_chats
human_reply = _wa.human_reply
set_ai_paused = _wa.set_ai_paused
clean_phone = _wa.clean_phone
resolve_agent = _wa.resolve_agent
CHAT_GUIDANCE = _wa.CHAT_GUIDANCE
