"""whatsapp_flows: Meta WhatsApp Flows (native multi-screen forms), navigate-only, draft/created/published lifecycle

Revision ID: 0021
Revises: 0020
"""
import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "whatsapp_flows" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "whatsapp_flows",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("category", sa.String(32), nullable=False, server_default="LEAD_GENERATION"),
        sa.Column("screens_json", sa.Text(), nullable=False),
        sa.Column("meta_flow_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_whatsapp_flows_agent_id", "whatsapp_flows", ["agent_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if "whatsapp_flows" in sa.inspect(bind).get_table_names():
        op.drop_table("whatsapp_flows")
