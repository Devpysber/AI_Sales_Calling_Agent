"""
CRM records beyond leads: user accounts, companies, contacts, deals, tasks, campaigns,
automation rules and message templates. Everything except users lives in an agent workspace.
"""

import json
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.lead import iso

ROLES = ("super_admin", "org_admin", "sales_manager", "sales_exec", "ai_ops", "viewer")
ADMIN_ROLES = ("super_admin", "org_admin")


class _Dict:
    """to_dict over every column; JSON text columns (named *_json) are decoded."""
    _hidden: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        out = {}
        for col in self.__table__.columns:
            if col.name in self._hidden:
                continue
            value = getattr(self, col.name)
            if col.name.endswith("_json"):
                out[col.name[:-5]] = json.loads(value) if value else None
            elif isinstance(value, datetime):
                out[col.name] = iso(value)
            else:
                out[col.name] = value
        return out


class User(_Dict, Base):
    __tablename__ = "users"
    _hidden = ("password_hash", "token", "totp_secret")

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(120))
    organization: Mapped[str | None] = mapped_column(String(160))
    business_type: Mapped[str | None] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(32), default="viewer")
    # pending (email unverified) | awaiting_approval | active | invited | disabled
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    token: Mapped[str | None] = mapped_column(String(96), index=True)  # verify / invite / reset, single use
    token_purpose: Mapped[str | None] = mapped_column(String(16))
    token_expires: Mapped[datetime | None] = mapped_column(DateTime)
    totp_secret: Mapped[str | None] = mapped_column(String(64))
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Company(_Dict, Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(255))
    industry: Mapped[str | None] = mapped_column(String(120))
    website: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))
    city: Mapped[str | None] = mapped_column(String(128))
    owner: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class Contact(_Dict, Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(Integer, index=True)
    company_id: Mapped[int | None] = mapped_column(Integer, index=True)
    lead_id: Mapped[int | None] = mapped_column(Integer, index=True)  # the lead this customer converted from
    name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))
    title: Mapped[str | None] = mapped_column(String(120))
    tags: Mapped[str | None] = mapped_column(String(512))
    preferred_channel: Mapped[str | None] = mapped_column(String(16))  # call | email | whatsapp
    owner: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


DEAL_STAGES = ("Prospecting", "Qualified", "Proposal", "Negotiation", "Won", "Lost")


class Deal(_Dict, Base):
    __tablename__ = "deals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(Integer, index=True)
    title: Mapped[str] = mapped_column(String(255))
    stage: Mapped[str] = mapped_column(String(32), default="Prospecting", index=True)
    value: Mapped[float] = mapped_column(Float, default=0)
    probability: Mapped[int] = mapped_column(Integer, default=10)
    lead_id: Mapped[int | None] = mapped_column(Integer, index=True)
    contact_id: Mapped[int | None] = mapped_column(Integer, index=True)
    company_id: Mapped[int | None] = mapped_column(Integer, index=True)
    owner: Mapped[str | None] = mapped_column(String(120))
    expected_close: Mapped[str | None] = mapped_column(String(10))  # YYYY-MM-DD
    lost_reason: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class Task(_Dict, Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(Integer, index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16), default="todo")  # todo | call | email | meeting | follow_up
    priority: Mapped[str] = mapped_column(String(8), default="medium")
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open | done
    due_at: Mapped[str | None] = mapped_column(String(16), index=True)  # "YYYY-MM-DD HH:MM" IST
    recurrence: Mapped[str | None] = mapped_column(String(8))  # daily | weekly | monthly
    assignee: Mapped[str | None] = mapped_column(String(120))
    lead_id: Mapped[int | None] = mapped_column(Integer, index=True)
    deal_id: Mapped[int | None] = mapped_column(Integer, index=True)
    contact_id: Mapped[int | None] = mapped_column(Integer, index=True)
    created_by: Mapped[str | None] = mapped_column(String(64))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Campaign(_Dict, Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(255))
    channel: Mapped[str] = mapped_column(String(16), default="call")  # call | email | whatsapp
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | running | completed
    segment_json: Mapped[str | None] = mapped_column(Text)  # {"status": [...], "qualification": [...], "tags": [...], "source": ...}
    subject: Mapped[str | None] = mapped_column(String(255))
    message: Mapped[str | None] = mapped_column(Text)  # {name}, {company} placeholders
    stats_json: Mapped[str | None] = mapped_column(Text)  # {"targeted", "sent", "skipped", "failed"}
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AutomationRule(_Dict, Base):
    __tablename__ = "automation_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(255))
    trigger: Mapped[str] = mapped_column(String(48), index=True)  # an event type, e.g. lead.created
    conditions_json: Mapped[str | None] = mapped_column(Text)  # [{"field", "op", "value"}]
    actions_json: Mapped[str | None] = mapped_column(Text)  # [{"type", ...params}]
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    runs: Mapped[int] = mapped_column(Integer, default=0)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class MessageTemplate(_Dict, Base):
    __tablename__ = "message_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(120))
    channel: Mapped[str] = mapped_column(String(16), default="email")
    subject: Mapped[str | None] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
