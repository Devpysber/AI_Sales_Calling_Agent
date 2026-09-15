"""lead callback_at: exact time the customer asked to be called back

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("leads") as batch:
        batch.add_column(sa.Column("callback_at", sa.String(32), nullable=True))
        batch.create_index("ix_leads_callback_at", ["callback_at"])


def downgrade() -> None:
    with op.batch_alter_table("leads") as batch:
        batch.drop_index("ix_leads_callback_at")
        batch.drop_column("callback_at")
