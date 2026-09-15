"""lead queue_position: manual ordering of the call queue

Revision ID: 0006
Revises: 0005
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("leads") as batch:
        batch.add_column(sa.Column("queue_position", sa.Integer(), nullable=True))
        batch.create_index("ix_leads_queue_position", ["queue_position"])


def downgrade() -> None:
    with op.batch_alter_table("leads") as batch:
        batch.drop_index("ix_leads_queue_position")
        batch.drop_column("queue_position")
