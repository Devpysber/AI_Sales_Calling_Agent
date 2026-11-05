"""CRM suite: user accounts with roles, companies, contacts, deals, tasks, campaigns, automation rules, templates

Revision ID: 0012
Revises: 0011
"""
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

TABLES = ("users", "companies", "contacts", "deals", "tasks", "campaigns", "automation_rules", "message_templates")


def upgrade() -> None:
    from app.core.database import Base
    import app.models  # noqa: F401
    bind = op.get_bind()
    for name in TABLES:
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    for name in reversed(TABLES):
        op.drop_table(name)
