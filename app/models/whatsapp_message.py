"""
WhatsApp chat log: every message a lead sent to an agent's linked number and every reply (AI or dashboard).
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.lead import iso


class WhatsAppMessage(Base):
    __tablename__ = "whatsapp_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(Integer, index=True)
    lead_id: Mapped[int | None] = mapped_column(Integer, index=True)
    phone: Mapped[str] = mapped_column(String(32), index=True)  # digits only, with country code
    chat_id: Mapped[str | None] = mapped_column(String(64), index=True)  # WhatsApp JID; the reply address of an @lid sender
    direction: Mapped[str] = mapped_column(String(8))  # in | out
    text: Mapped[str] = mapped_column(Text)
    wa_message_id: Mapped[str | None] = mapped_column(String(128), unique=True)  # dedupes bridge retries
    provider: Mapped[str | None] = mapped_column(String(32))  # native_qr | composio | meta | dashboard
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "lead_id": self.lead_id,
            "phone": self.phone,
            "direction": self.direction,
            "text": self.text,
            "provider": self.provider,
            "created_at": iso(self.created_at),
        }
