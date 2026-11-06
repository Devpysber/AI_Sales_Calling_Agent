"""Channel adapter interface: what any inbound/outbound channel (WhatsApp, Instagram, web chat...) provides."""

from abc import ABC, abstractmethod


class ChannelAdapter(ABC):
    """Documented contract a channel plugs into the multi-channel platform with."""

    @abstractmethod
    def send(self, to: str, text: str, **kwargs) -> dict:
        """Send a plain text message. Returns {"ok": bool, ...}."""

    def send_media(self, to: str, media_id: str, **kwargs) -> dict:
        """Send a media message, when the channel supports it. Default: not supported."""
        return {"ok": False, "error": "media send not supported on this channel"}

    @abstractmethod
    def parse_webhook(self, payload: dict) -> list[dict]:
        """Normalize one webhook delivery into a list of inbound message dicts."""

    def verify(self, *args, **kwargs) -> bool:
        """Verify an inbound webhook's signature/token. Default: no verification."""
        return True
