import json
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.lead import iso


class Call(Base):
    __tablename__ = "calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int | None] = mapped_column(Integer, index=True)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"), index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)

    direction: Mapped[str] = mapped_column(String(16), default="outbound")
    trigger: Mapped[str] = mapped_column(String(32), default="manual")  # manual | auto_dial | retry | inbound | bulk
    from_number: Mapped[str | None] = mapped_column(String(32))
    to_number: Mapped[str | None] = mapped_column(String(32))
    # The human line this call was handed to, so the UI can name it instead of our own platform number.
    transferred_to: Mapped[str | None] = mapped_column(String(32))
    request_uuid: Mapped[str | None] = mapped_column(String(64), index=True)
    call_uuid: Mapped[str | None] = mapped_column(String(64), index=True)

    # Queued | Ringing | In Progress | Completed | No Answer | Busy | Failed | Canceled
    status: Mapped[str] = mapped_column(String(32), default="Queued", index=True)
    hangup_cause: Mapped[str | None] = mapped_column(String(128))
    duration: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)

    transcript: Mapped[str | None] = mapped_column(Text)  # JSON [{role, text, at}]
    summary: Mapped[str | None] = mapped_column(Text)
    qualification: Mapped[str | None] = mapped_column(String(32))
    sentiment: Mapped[str | None] = mapped_column(String(16))
    outcome: Mapped[str | None] = mapped_column(String(64))  # meeting_booked | interested | not_interested | callback | ...
    recording_url: Mapped[str | None] = mapped_column(String(512))
    avg_latency_ms: Mapped[float | None] = mapped_column(Float)
    # Billable usage (cost tracking): characters sent to TTS, audio seconds sent to STT, LLM requests
    tts_chars: Mapped[int | None] = mapped_column(Integer)
    stt_seconds: Mapped[float | None] = mapped_column(Float)
    llm_requests: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)

    def to_dict(self, lead_name: str | None = None, with_transcript: bool = True) -> dict:
        data = {
            "id": self.id,
            "agent_id": self.agent_id,
            "lead_id": self.lead_id,
            "lead_name": lead_name,
            "direction": self.direction,
            "trigger": self.trigger,
            "from_number": self.from_number,
            "to_number": self.to_number,
            "transferred_to": self.transferred_to,
            "call_uuid": self.call_uuid or self.request_uuid,
            "status": self.status,
            "hangup_cause": self.hangup_cause,
            "duration": self.duration,
            "error": self.error,
            "summary": self.summary,
            "qualification": self.qualification,
            "sentiment": self.sentiment,
            "outcome": self.outcome,
            "recording_url": self.recording_url,
            "avg_latency_ms": self.avg_latency_ms,
            "tts_chars": self.tts_chars, "stt_seconds": self.stt_seconds, "llm_requests": self.llm_requests,
            "created_at": iso(self.created_at),
            "answered_at": iso(self.answered_at),
            "ended_at": iso(self.ended_at),
        }
        turns = json.loads(self.transcript) if self.transcript else []
        if with_transcript:
            data["transcript"] = turns
        else:
            data["turns"] = len(turns)
        return data
