"""message_templates: real Meta template shape (category/components/status/rejection/meta id)

Revision ID: 0020
Revises: 0019
"""
import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def _existing_columns(bind) -> set:
    return {c["name"] for c in sa.inspect(bind).get_columns("message_templates")}


def upgrade() -> None:
    bind = op.get_bind()
    existing = _existing_columns(bind)
    if "category" not in existing:
        with op.batch_alter_table("message_templates") as batch:
            batch.add_column(sa.Column("category", sa.String(32), nullable=True))
    if "components_json" not in existing:
        with op.batch_alter_table("message_templates") as batch:
            batch.add_column(sa.Column("components_json", sa.Text(), nullable=True))
    if "header_format" not in existing:
        with op.batch_alter_table("message_templates") as batch:
            batch.add_column(sa.Column("header_format", sa.String(16), nullable=False, server_default="NONE"))
    if "rejected_reason" not in existing:
        with op.batch_alter_table("message_templates") as batch:
            batch.add_column(sa.Column("rejected_reason", sa.Text(), nullable=True))
    if "whatsapp_template_id" not in existing:
        with op.batch_alter_table("message_templates") as batch:
            batch.add_column(sa.Column("whatsapp_template_id", sa.String(64), nullable=True))
    if "status" not in existing:
        with op.batch_alter_table("message_templates") as batch:
            batch.add_column(sa.Column("status", sa.String(16), nullable=False, server_default="draft"))


def downgrade() -> None:
    bind = op.get_bind()
    existing = _existing_columns(bind)
    for col in ("status", "whatsapp_template_id", "rejected_reason", "header_format", "components_json", "category"):
        if col in existing:
            with op.batch_alter_table("message_templates") as batch:
                batch.drop_column(col)
