"""Channel adapters: one module per channel (WhatsApp, Instagram, web...) behind the ChannelAdapter interface."""
from app.services.channels.base import ChannelAdapter

__all__ = ["ChannelAdapter"]
