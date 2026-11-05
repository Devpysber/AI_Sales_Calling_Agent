"""Instagram channel adapter. The Instagram Messaging logic itself lives in
app.services.instagram_service (mirrors channels/whatsapp.py's relationship to whatsapp_service)."""

import asyncio

from app.services import instagram_service as _ig
from app.services.channels.base import ChannelAdapter


class InstagramAdapter(ChannelAdapter):
    def send(self, to: str, text: str, **kwargs) -> dict:
        return asyncio.run(_ig.send_instagram_message(to, text, **kwargs))

    def parse_webhook(self, payload: dict) -> list[dict]:
        return _ig.parse_webhook(payload)

    def verify(self, body: bytes, header: str | None = None) -> bool:
        return _ig.verify_signature(body, header)


send_instagram_message = _ig.send_instagram_message
parse_webhook = _ig.parse_webhook
verify_signature = _ig.verify_signature
handle_inbound = _ig.handle_inbound
human_reply = _ig.human_reply
set_ai_paused = _ig.set_ai_paused
resolve_agent = _ig.resolve_agent
CHAT_GUIDANCE = _ig.CHAT_GUIDANCE
