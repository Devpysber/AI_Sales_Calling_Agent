"""calls.transferred_to: the human number a call was forwarded or transferred to

Revision ID: 0007
Revises: 0006
"""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("calls") as batch:
        batch.add_column(sa.Column("transferred_to", sa.String(length=32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("calls") as batch:
        batch.drop_column("transferred_to")
