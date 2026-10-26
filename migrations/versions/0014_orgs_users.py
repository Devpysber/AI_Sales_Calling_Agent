"""organizations, users, agents.organization_id, leads.owner_user_id

Real users that belong to an organisation. Existing agents and logins are adopted at startup by
app.services.user_service.ensure_bootstrap, so nothing is backfilled here.

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
    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("business_type", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("legacy_team_id", sa.String(64), nullable=True, unique=True),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_organization_id", "users", ["organization_id"])
    op.add_column("agents", sa.Column("organization_id", sa.Integer(), nullable=True))
    op.create_index("ix_agents_organization_id", "agents", ["organization_id"])
    op.add_column("leads", sa.Column("owner_user_id", sa.Integer(), nullable=True))
    op.create_index("ix_leads_owner_user_id", "leads", ["owner_user_id"])


def downgrade() -> None:
    op.drop_index("ix_leads_owner_user_id", "leads")
    op.drop_column("leads", "owner_user_id")
    op.drop_index("ix_agents_organization_id", "agents")
    op.drop_column("agents", "organization_id")
    op.drop_index("ix_users_organization_id", "users")
    op.drop_table("users")
    op.drop_table("organizations")
