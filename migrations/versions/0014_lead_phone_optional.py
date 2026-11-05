"""leads.phone becomes optional: a lead can arrive from a channel that has no phone number

Revision ID: 0014
Revises: 0013
"""
import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("leads") as batch:
        batch.alter_column("phone", existing_type=sa.String(32), nullable=True)


def downgrade() -> None:
    # Restoring NOT NULL is only safe if no lead has a NULL phone by then; if any row does,
    # this will fail at the database level rather than silently corrupting data.
    with op.batch_alter_table("leads") as batch:
        batch.alter_column("phone", existing_type=sa.String(32), nullable=False)
