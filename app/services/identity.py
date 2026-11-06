"""Cross-channel identity merge: reassign a lead's ContactIdentity and Conversation rows onto another
lead. Generic (not Instagram-specific) so any future channel can reuse it when a contact turns out to
already exist under a different identity (e.g. an Instagram-only lead later shares a phone number that
matches an existing WhatsApp/CRM lead)."""

from sqlalchemy import update

from app.core.database import get_db
from app.models.channel import ContactIdentity, Conversation


def merge_identity(agent_id: int, from_lead_id: int, to_lead_id: int) -> None:
    """Reassign every ContactIdentity and Conversation (open or closed) owned by `from_lead_id` onto
    `to_lead_id`. Rule: `to_lead_id` always wins -- callers should pass the existing/richer CRM lead as
    `to_lead_id`. This is purely mechanical row reassignment: it does not merge or dedupe conflicting CRM
    fields (name/city/etc) on the Lead rows themselves; last-write-wins there is left to normal CRM updates.
    """
    if not from_lead_id or not to_lead_id or from_lead_id == to_lead_id:
        return
    with get_db() as db:
        db.execute(update(ContactIdentity).where(ContactIdentity.agent_id == agent_id,
                   ContactIdentity.lead_id == from_lead_id).values(lead_id=to_lead_id))
        db.execute(update(Conversation).where(Conversation.agent_id == agent_id,
                   Conversation.lead_id == from_lead_id).values(lead_id=to_lead_id))
