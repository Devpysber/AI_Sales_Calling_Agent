"""message_templates: Meta-approved WhatsApp template fields

Revision ID: 0018
Revises: 0017

Note: 0015_crm_suite creates message_templates straight from the live SQLAlchemy metadata
(checkfirst=True), so on a fresh database these columns already exist by the time this migration
runs. On a database migrated before these model fields were added, they're missing -- hence the
explicit existence check before altering.
"""
import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def _existing_columns(bind) -> set:
    return {c["name"] for c in sa.inspect(bind).get_columns("message_templates")}


def upgrade() -> None:
    bind = op.get_bind()
    existing = _existing_columns(bind)
    if "whatsapp_template_name" not in existing:
        with op.batch_alter_table("message_templates") as batch:
            batch.add_column(sa.Column("whatsapp_template_name", sa.String(120), nullable=True))
    if "whatsapp_template_language" not in existing:
        with op.batch_alter_table("message_templates") as batch:
            batch.add_column(sa.Column("whatsapp_template_language", sa.String(16), nullable=True))
    if "is_meta_approved" not in existing:
        with op.batch_alter_table("message_templates") as batch:
            batch.add_column(sa.Column("is_meta_approved", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    bind = op.get_bind()
    existing = _existing_columns(bind)
    if "is_meta_approved" in existing:
        with op.batch_alter_table("message_templates") as batch:
            batch.drop_column("is_meta_approved")
    if "whatsapp_template_language" in existing:
        with op.batch_alter_table("message_templates") as batch:
            batch.drop_column("whatsapp_template_language")
    if "whatsapp_template_name" in existing:
        with op.batch_alter_table("message_templates") as batch:
            batch.drop_column("whatsapp_template_name")
