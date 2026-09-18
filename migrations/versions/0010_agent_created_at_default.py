"""agents.created_at: give the column the default the model always assumed, and fill the rows that lost it

The column was added in 0003 without a server default, while the model declares one. Every agent
created since was written with created_at NULL, so the workspace page showed "Created —".

Revision ID: 0010
Revises: 0009
"""
import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.alter_column("created_at", server_default=sa.text("CURRENT_TIMESTAMP"))

    # The "agent.created" event carries the real moment the workspace was made; fall back to its
    # earliest call, and only then to now, so a backfilled date is never invented out of nothing.
    bind = op.get_bind()
    bind.execute(sa.text("""
        UPDATE agents SET created_at = COALESCE(
            (SELECT MIN(e.created_at) FROM events e WHERE e.agent_id = agents.id AND e.type = 'agent.created'),
            (SELECT MIN(c.created_at) FROM calls c WHERE c.agent_id = agents.id),
            CURRENT_TIMESTAMP)
        WHERE created_at IS NULL
    """))


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.alter_column("created_at", server_default=None)
