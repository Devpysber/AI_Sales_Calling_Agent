"""call usage: billable TTS characters, speech-recognition seconds and LLM requests per call

Revision ID: 0005
Revises: 0004
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("calls") as batch:
        batch.add_column(sa.Column("tts_chars", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("stt_seconds", sa.Float(), nullable=True))
        batch.add_column(sa.Column("llm_requests", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("calls") as batch:
        batch.drop_column("llm_requests")
        batch.drop_column("stt_seconds")
        batch.drop_column("tts_chars")
