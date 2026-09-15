from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (Index("ix_leads_dial_queue", "status", "call_status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int | None] = mapped_column(Integer, index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    company: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str] = mapped_column(String(32), index=True)
    email: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str | None] = mapped_column(String(128))
    language: Mapped[str] = mapped_column(String(16), default="en-IN")
    source: Mapped[str | None] = mapped_column(String(64))
    tags: Mapped[str | None] = mapped_column(String(512))
    notes: Mapped[str | None] = mapped_column(Text)
    do_not_call: Mapped[bool] = mapped_column(Boolean, default=False)

    status: Mapped[str] = mapped_column(String(64), default="New", index=True)
    call_status: Mapped[str | None] = mapped_column(String(64), index=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    last_call_uuid: Mapped[str | None] = mapped_column(String(64))

    qualification: Mapped[str | None] = mapped_column(String(64), index=True)
    summary: Mapped[str | None] = mapped_column(Text)
    requirements: Mapped[str | None] = mapped_column(Text)
    objections: Mapped[str | None] = mapped_column(Text)
    follow_up_date: Mapped[str | None] = mapped_column(String(64))
    meeting_at: Mapped[str | None] = mapped_column(String(64), index=True)
    callback_at: Mapped[str | None] = mapped_column(String(32), index=True)  # "YYYY-MM-DD HH:MM" IST; dialled by the scheduler

    last_contacted_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "name": self.name,
            "company": self.company,
            "phone": self.phone,
            "email": self.email,
            "city": self.city,
            "language": self.language,
            "source": self.source,
            "tags": [t for t in (self.tags or "").split(",") if t],
            "notes": self.notes,
            "do_not_call": self.do_not_call,
            "status": self.status,
            "call_status": self.call_status,
            "retry_count": self.retry_count,
            "qualification": self.qualification,
            "summary": self.summary,
            "requirements": self.requirements,
            "objections": self.objections,
            "follow_up_date": self.follow_up_date,
            "meeting_at": self.meeting_at,
            "callback_at": self.callback_at,
            "phone_valid": not (self.phone or "").startswith("+91") or len(self.phone) == 13,
            "last_contacted_at": iso(self.last_contacted_at),
            "created_at": iso(self.created_at),
            "updated_at": iso(self.updated_at),
        }


def iso(value: datetime | None) -> str | None:
    """Naive UTC datetimes -> ISO 8601 with Z."""
    return value.isoformat(timespec="seconds") + "Z" if value else None
