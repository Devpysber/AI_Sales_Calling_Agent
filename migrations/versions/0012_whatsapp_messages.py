"""whatsapp_messages: the WhatsApp chat log per lead

The WhatsApp agent replies from this history and the voice agent reads the recent part of it
before a call. wa_message_id is unique so a bridge retry cannot store (or answer) a message twice.

Revision ID: 0012
Revises: 0011
"""
import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "whatsapp_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=True),
        sa.Column("phone", sa.String(32), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("wa_message_id", sa.String(128), nullable=True, unique=True),
        sa.Column("provider", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_whatsapp_messages_agent_id", "whatsapp_messages", ["agent_id"])
    op.create_index("ix_whatsapp_messages_lead_id", "whatsapp_messages", ["lead_id"])
    op.create_index("ix_whatsapp_messages_phone", "whatsapp_messages", ["phone"])


def downgrade() -> None:
    op.drop_index("ix_whatsapp_messages_phone", "whatsapp_messages")
    op.drop_index("ix_whatsapp_messages_lead_id", "whatsapp_messages")
    op.drop_index("ix_whatsapp_messages_agent_id", "whatsapp_messages")
    op.drop_table("whatsapp_messages")
