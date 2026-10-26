"""whatsapp_messages.chat_id: the WhatsApp JID a message came from

Some senders (@lid ids) have no phone number the bridge can resolve. Their chat id is the only
address we can reply to, and the only way to recognise them on their next message.

Revision ID: 0013
Revises: 0012
"""
import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("whatsapp_messages", sa.Column("chat_id", sa.String(64), nullable=True))
    op.create_index("ix_whatsapp_messages_chat_id", "whatsapp_messages", ["chat_id"])


def downgrade() -> None:
    op.drop_index("ix_whatsapp_messages_chat_id", "whatsapp_messages")
    op.drop_column("whatsapp_messages", "chat_id")
