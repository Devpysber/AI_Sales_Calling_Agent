"""Multi-channel messaging: channel accounts, contact identities, conversations, messages, tools and tool runs."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.crm import _Dict


class ChannelAccount(_Dict, Base):
    __tablename__ = "channel_accounts"
    __table_args__ = (UniqueConstraint("channel", "external_id", name="uq_channel_accounts_channel_external"),)
    _hidden = ("token_encrypted",)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(Integer, index=True)
    channel: Mapped[str] = mapped_column(String(16))  # whatsapp | instagram | web
    external_id: Mapped[str] = mapped_column(String(128))  # WhatsApp phone_number_id / Instagram account id
    display_name: Mapped[str | None] = mapped_column(String(255))
    token_encrypted: Mapped[str | None] = mapped_column(Text)
    config_json: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | connected | error | disabled
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class ContactIdentity(_Dict, Base):
    __tablename__ = "contact_identities"
    __table_args__ = (
        UniqueConstraint("agent_id", "channel", "external_id", name="uq_contact_identities_agent_channel_external"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(Integer, index=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(16))
    external_id: Mapped[str] = mapped_column(String(128))
    display_name: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Conversation(_Dict, Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_agent_status_last_message", "agent_id", "status", "last_message_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(Integer, index=True)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"), index=True)
    channel: Mapped[str] = mapped_column(String(16))
    channel_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("channel_accounts.id", ondelete="SET NULL"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open | closed
    ai_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    assigned_to: Mapped[str | None] = mapped_column(String(64))
    summary: Mapped[str | None] = mapped_column(Text)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    last_customer_at: Mapped[datetime | None] = mapped_column(DateTime)  # drives Meta's 24-hour reply window
    call_id: Mapped[int | None] = mapped_column(ForeignKey("calls.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)


class Message(_Dict, Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("agent_id", "external_id", name="uq_messages_agent_external"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[int] = mapped_column(Integer, index=True)
    direction: Mapped[str] = mapped_column(String(8))  # in | out
    sender_type: Mapped[str] = mapped_column(String(16))  # customer | ai | human | system
    sender_name: Mapped[str | None] = mapped_column(String(120))
    # text | audio | image | document | video | location | interactive | other
    type: Mapped[str] = mapped_column(String(16), default="text")
    text: Mapped[str | None] = mapped_column(Text)
    media_json: Mapped[str | None] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(String(128))
    # received | queued | sent | delivered | read | failed
    status: Mapped[str] = mapped_column(String(16), default="received")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class ToolDefinition(_Dict, Base):
    __tablename__ = "tool_definitions"
    __table_args__ = (UniqueConstraint("agent_id", "name", name="uq_tool_definitions_agent_name"),)
    _hidden = ("secret_encrypted",)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(16))  # builtin | http | connector | mcp
    parameters_json: Mapped[str | None] = mapped_column(Text)
    config_json: Mapped[str | None] = mapped_column(Text)
    secret_encrypted: Mapped[str | None] = mapped_column(Text)
    risk: Mapped[str] = mapped_column(String(8), default="read")  # read | write | money
    channels_json: Mapped[str | None] = mapped_column(Text)  # null means all channels
    needs_confirmation: Mapped[bool] = mapped_column(Boolean, default=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=10)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class ToolRun(_Dict, Base):
    __tablename__ = "tool_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(Integer, index=True)
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), index=True)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    tool_name: Mapped[str] = mapped_column(String(64), index=True)
    arguments_json: Mapped[str | None] = mapped_column(Text)
    result: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))  # ok | error | denied | timeout
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
