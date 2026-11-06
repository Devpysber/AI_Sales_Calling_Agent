"""CRM suite: signup accounts with roles, companies, contacts, deals, tasks, campaigns, automation rules, templates

Revision ID: 0015
Revises: 0014
"""
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

TABLES = ("signup_accounts", "companies", "contacts", "deals", "tasks", "campaigns", "automation_rules", "message_templates")


def upgrade() -> None:
    from app.core.database import Base
    import app.models  # noqa: F401
    bind = op.get_bind()
    for name in TABLES:
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    for name in reversed(TABLES):
        op.drop_table(name)
