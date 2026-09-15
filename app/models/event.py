"""
Activity log: everything that happens to leads, calls, documents and settings.
"""

import json
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.lead import iso


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # lead.created | lead.updated | lead.imported | call.started | call.answered | call.ended |
    # call.failed | ai.crm_update | ai.summary | meeting.booked | document.added | settings.updated | ...
    agent_id: Mapped[int | None] = mapped_column(Integer, index=True)
    type: Mapped[str] = mapped_column(String(48), index=True)
    title: Mapped[str] = mapped_column(String(255))
    detail: Mapped[str | None] = mapped_column(Text)
    data: Mapped[str | None] = mapped_column(Text)  # JSON
    actor: Mapped[str] = mapped_column(String(64), default="system")  # system | ai | scheduler | <username>
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    call_id: Mapped[int | None] = mapped_column(ForeignKey("calls.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "type": self.type,
            "title": self.title,
            "detail": self.detail,
            "data": json.loads(self.data) if self.data else None,
            "actor": self.actor,
            "lead_id": self.lead_id,
            "call_id": self.call_id,
            "created_at": iso(self.created_at),
        }
