"""calls.llm_input_tokens / llm_output_tokens: what each call actually sent to and got from the LLM

Cost work needs real token counts, not the ~7k-per-request estimate. Provider usage when the stream
reports it, otherwise a character-based estimate (flagged in Analytics).

Revision ID: 0011
Revises: 0010
"""
import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("calls") as batch:
        batch.add_column(sa.Column("llm_input_tokens", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("llm_output_tokens", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("calls") as batch:
        batch.drop_column("llm_output_tokens")
        batch.drop_column("llm_input_tokens")
