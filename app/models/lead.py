from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# The one source of truth for qualification -> lead_score baseline (call_service._lead_score() reuses
# this instead of its own copy) and for decaying a stored score by how stale contact has gone.
QUALIFICATION_SCORE_BASELINE = {"Hot": 85, "Warm": 55, "Cold": 20}


def effective_lead_score(score: int | None, last_contacted_at: datetime | None) -> int | None:
    if score is None:
        return None
    if last_contacted_at is None:
        return score
    days_since_last_contact = (datetime.utcnow() - last_contacted_at).days
    return max(0, score - min(50, days_since_last_contact // 3))


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (Index("ix_leads_dial_queue", "status", "call_status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int | None] = mapped_column(Integer, index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    company: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32), index=True)
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
    queue_position: Mapped[int | None] = mapped_column(Integer, index=True)  # order in the call queue (lower first)
    last_call_uuid: Mapped[str | None] = mapped_column(String(64))
    owner_user_id: Mapped[int | None] = mapped_column(Integer, index=True)  # the sales rep this lead is assigned to

    qualification: Mapped[str | None] = mapped_column(String(64), index=True)
    lead_score: Mapped[int | None] = mapped_column(Integer)
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
            "queue_position": self.queue_position,
            "owner_user_id": self.owner_user_id,
            "qualification": self.qualification,
            "lead_score": self.lead_score,
            "lead_score_effective": self._lead_score_effective(),
            "lead_score_sentiment_trend": self._lead_score_sentiment_trend(),
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

    def _lead_score_effective(self) -> int | None:
        return effective_lead_score(self.lead_score, self.last_contacted_at)

    def _lead_score_sentiment_trend(self) -> int:
        """How far lead_score has moved from the plain qualification baseline (sentiment across calls),
        clamped to +-5 - the frontend's leadScore() adds this instead of reconstructing the baseline itself."""
        baseline = QUALIFICATION_SCORE_BASELINE.get(self.qualification or "")
        if self.lead_score is None or not baseline:
            return 0
        return max(-5, min(5, round((self.lead_score - baseline) / 2)))


def iso(value: datetime | None) -> str | None:
    """Naive UTC datetimes -> ISO 8601 with Z."""
    return value.isoformat(timespec="seconds") + "Z" if value else None
