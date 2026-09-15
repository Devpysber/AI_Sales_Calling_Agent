"""agent workspaces: per-agent profile, automation, phone number and activity

Existing single-agent data (persona, automation, leads, calls, documents,
activity) is moved into a first agent so nothing is lost.

Revision ID: 0003
Revises: 868a71b832bb
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "868a71b832bb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("voice_speaker")
        batch.drop_column("default_language")
        batch.drop_column("prompt_objective")
        batch.drop_column("prompt_instructions")
        batch.drop_column("settings")
        batch.add_column(sa.Column("description", sa.Text(), nullable=True))
        batch.add_column(sa.Column("color", sa.String(16), nullable=False, server_default="#5b4bf5"))
        batch.add_column(sa.Column("phone_number", sa.String(32), nullable=True))
        batch.add_column(sa.Column("status", sa.String(16), nullable=False, server_default="active"))
        batch.add_column(sa.Column("profile", sa.Text(), nullable=True))
        batch.add_column(sa.Column("automation", sa.Text(), nullable=True))
        batch.add_column(sa.Column("created_at", sa.DateTime(), nullable=True))
        batch.create_index("ix_agents_phone_number", ["phone_number"])

    with op.batch_alter_table("events") as batch:
        batch.add_column(sa.Column("agent_id", sa.Integer(), nullable=True))
        batch.create_index("ix_events_agent_id", ["agent_id"])

    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT key, value FROM app_settings WHERE key LIKE 'agent.%' OR key LIKE 'automation.%'")).all()
    profile = {k.split(".", 1)[1]: json.loads(v) for k, v in rows if k.startswith("agent.")}
    automation = {k.split(".", 1)[1]: json.loads(v) for k, v in rows if k.startswith("automation.")}
    has_data = any(conn.execute(sa.text(f"SELECT 1 FROM {t} LIMIT 1")).first() for t in ("leads", "calls", "documents"))
    if not (profile or automation or has_data):
        return

    name = profile.get("company_name") or "Sales agent"
    conn.execute(sa.text("INSERT INTO agents (name, description, color, status, profile, automation, created_at) "
                         "VALUES (:name, :description, '#5b4bf5', 'active', :profile, :automation, CURRENT_TIMESTAMP)"),
                 {"name": name, "description": "Migrated from the single-agent setup.",
                  "profile": json.dumps(profile, ensure_ascii=False), "automation": json.dumps(automation)})
    agent_id = conn.execute(sa.text("SELECT MAX(id) FROM agents")).scalar()
    for table in ("leads", "calls", "documents", "events"):
        conn.execute(sa.text(f"UPDATE {table} SET agent_id = :id WHERE agent_id IS NULL"), {"id": agent_id})
    conn.execute(sa.text("DELETE FROM app_settings WHERE key LIKE 'state.last_run.%'"))


def downgrade() -> None:
    with op.batch_alter_table("events") as batch:
        batch.drop_index("ix_events_agent_id")
        batch.drop_column("agent_id")
    with op.batch_alter_table("agents") as batch:
        batch.drop_index("ix_agents_phone_number")
        for column in ("description", "color", "phone_number", "status", "profile", "automation", "created_at"):
            batch.drop_column(column)
        batch.add_column(sa.Column("voice_speaker", sa.String(64), nullable=True))
        batch.add_column(sa.Column("default_language", sa.String(16), nullable=True))
        batch.add_column(sa.Column("prompt_objective", sa.Text(), nullable=True))
        batch.add_column(sa.Column("prompt_instructions", sa.Text(), nullable=True))
        batch.add_column(sa.Column("settings", sa.JSON(), nullable=True))
