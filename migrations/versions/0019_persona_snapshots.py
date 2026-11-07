"""persona_snapshots: history of an agent's prior persona `instructions` text, for rollback.

Revision ID: 0019
Revises: 0018
"""
import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "persona_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=True),
    )
    op.create_index("ix_persona_snapshots_agent_id", "persona_snapshots", ["agent_id"])


def downgrade() -> None:
    op.drop_index("ix_persona_snapshots_agent_id", "persona_snapshots")
    op.drop_table("persona_snapshots")
