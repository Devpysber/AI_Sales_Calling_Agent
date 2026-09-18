"""backfill 'Inbound call from None' event titles with the caller's number

An earlier bug wrote the literal "None" when a lead row existed without a name.
The rows are correct apart from their title, so name them from the call they
point at rather than leaving "None" in the audit trail.

Revision ID: 0008
Revises: 0007
"""
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

BROKEN = "Inbound call from None"


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT e.id, c.from_number FROM events e JOIN calls c ON c.id = e.call_id WHERE e.title = :t"
    ), {"t": BROKEN}).fetchall()
    for event_id, from_number in rows:
        if not from_number:
            continue
        bind.execute(sa.text("UPDATE events SET title = :title WHERE id = :id"),
                     {"title": f"Inbound call from {from_number}", "id": event_id})


def downgrade() -> None:
    # The original text carried no information, so there is nothing worth restoring.
    pass
