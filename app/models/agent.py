"""
An AI agent is an isolated workspace: its own persona, voice, phone number,
automation schedule, knowledge base, leads, calls and activity history.
"""

import json
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.lead import iso


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    color: Mapped[str] = mapped_column(String(16), default="#5b4bf5")
    # Plivo number used as caller ID and to route inbound calls; empty = PLIVO_PHONE_NUMBER
    phone_number: Mapped[str | None] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | paused
    profile: Mapped[str | None] = mapped_column(Text)     # JSON persona (see agents.PROFILE_DEFAULTS)
    automation: Mapped[str | None] = mapped_column(Text)  # JSON schedule (see agents.AUTOMATION_DEFAULTS)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "color": self.color,
            "phone_number": self.phone_number,
            "status": self.status,
            "created_at": iso(self.created_at),
        }

    def stored(self, field: str) -> dict:
        value = getattr(self, field)
        return json.loads(value) if value else {}
