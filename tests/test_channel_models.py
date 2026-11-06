"""Schema-level tests for the multi-channel tables (models only, no service behaviour)."""

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.core.database import get_db
from app.models import (ChannelAccount, ContactIdentity, Conversation, Lead, Message, ToolDefinition,
                        ToolRun)


@pytest.fixture(autouse=True)
def _migrated(client):
    """The client fixture boots the app, which runs Alembic on the test database."""


def _u() -> str:
    return uuid.uuid4().hex[:12]


def _lead(db) -> Lead:
    lead = Lead(phone="+9190" + str(uuid.uuid4().int)[:8], name="T")
    db.add(lead)
    db.flush()
    return lead


def _conv(db, agent_id=900, **kw) -> Conversation:
    conv = Conversation(agent_id=agent_id, channel="whatsapp", **kw)
    db.add(conv)
    db.flush()
    return conv


def test_create_each_row():
    with get_db() as db:
        lead = _lead(db)
        acct = ChannelAccount(agent_id=900, channel="whatsapp", external_id=_u())
        db.add(acct)
        db.flush()
        db.add(ContactIdentity(agent_id=900, lead_id=lead.id, channel="whatsapp", external_id=_u()))
        conv = _conv(db, lead_id=lead.id, channel_account_id=acct.id)
        msg = Message(conversation_id=conv.id, agent_id=900, direction="in", sender_type="customer", text="hi")
        db.add(msg)
        db.flush()
        db.add(ToolDefinition(agent_id=900, name=_u(), description="d", type="builtin"))
        run = ToolRun(agent_id=900, conversation_id=conv.id, message_id=msg.id, tool_name="t", status="ok")
        db.add(run)
        db.flush()
        assert acct.status == "pending" and acct.created_at and acct.updated_at
        assert conv.status == "open" and conv.ai_paused is False
        assert msg.type == "text" and msg.status == "received"
        assert run.id


def test_channel_account_unique():
    ext = _u()
    with get_db() as db:
        db.add(ChannelAccount(agent_id=901, channel="whatsapp", external_id=ext))
        db.flush()
        with pytest.raises(IntegrityError):
            db.add(ChannelAccount(agent_id=902, channel="whatsapp", external_id=ext))
            db.flush()
        db.rollback()


def test_contact_identity_unique_per_agent():
    ext = _u()
    with get_db() as db:
        lead = _lead(db)
        db.add(ContactIdentity(agent_id=903, lead_id=lead.id, channel="whatsapp", external_id=ext))
        db.flush()
        # same person at a different business is fine
        db.add(ContactIdentity(agent_id=904, lead_id=lead.id, channel="whatsapp", external_id=ext))
        db.flush()
        with pytest.raises(IntegrityError):
            db.add(ContactIdentity(agent_id=903, lead_id=lead.id, channel="whatsapp", external_id=ext))
            db.flush()
        db.rollback()


def test_message_external_id_unique_null_and_cross_agent():
    ext = _u()
    with get_db() as db:
        conv = _conv(db, agent_id=905)

        def add(agent_id, external_id):
            db.add(Message(conversation_id=conv.id, agent_id=agent_id, direction="in", sender_type="customer",
                           external_id=external_id))
            db.flush()

        add(905, None)
        add(905, None)  # NULLs never collide
        add(905, ext)
        add(906, ext)  # same id under another agent is allowed
        with pytest.raises(IntegrityError):
            add(905, ext)
        db.rollback()


def test_tool_definition_name_unique_per_agent():
    name = _u()
    with get_db() as db:
        db.add(ToolDefinition(agent_id=907, name=name, description="d", type="http"))
        db.flush()
        db.add(ToolDefinition(agent_id=908, name=name, description="d", type="http"))
        db.flush()
        with pytest.raises(IntegrityError):
            db.add(ToolDefinition(agent_id=907, name=name, description="d", type="http"))
            db.flush()
        db.rollback()


def test_lead_delete_cascades_identity_and_nulls_conversation():
    with get_db() as db:
        # FK enforcement is on for SQLite (PRAGMA foreign_keys=ON in app.core.database)
        assert db.execute(text("PRAGMA foreign_keys")).scalar() == 1
        lead = _lead(db)
        ident = ContactIdentity(agent_id=909, lead_id=lead.id, channel="whatsapp", external_id=_u())
        db.add(ident)
        conv = _conv(db, agent_id=909, lead_id=lead.id)
        ident_id, conv_id, lead_id = ident.id, conv.id, lead.id
        db.commit()
        db.execute(text("DELETE FROM leads WHERE id = :i"), {"i": lead_id})
        db.commit()
        db.expire_all()
        assert db.get(ContactIdentity, ident_id) is None
        survivor = db.get(Conversation, conv_id)
        assert survivor is not None and survivor.lead_id is None
        assert not db.scalars(select(Lead).where(Lead.id == lead_id)).first()


def test_to_dict_hides_secrets():
    with get_db() as db:
        acct = ChannelAccount(agent_id=910, channel="instagram", external_id=_u(), token_encrypted="SECRET-TOKEN",
                              config_json='{"a": 1}')
        tool = ToolDefinition(agent_id=910, name=_u(), description="d", type="http", secret_encrypted="SECRET-KEY")
        db.add_all([acct, tool])
        db.flush()
        a, t = acct.to_dict(), tool.to_dict()
        assert "token_encrypted" not in a and "SECRET-TOKEN" not in str(a)
        assert "secret_encrypted" not in t and "SECRET-KEY" not in str(t)
        assert a["config"] == {"a": 1}
