"""lead_score: numeric lead score (qualification + sentiment + recency decay)

Revision ID: 0022
Revises: 0021
"""
import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("lead_score", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("leads", "lead_score")
