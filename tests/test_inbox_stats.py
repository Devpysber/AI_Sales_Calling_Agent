"""list_agents()'s awaiting_reply stat: open WhatsApp/Instagram conversations whose latest message
is still from the customer - backs the Inbox nav badge."""

import uuid

import pytest

from app.core.database import get_db
from app.models.channel import Conversation, Message
from app.services import agents


@pytest.fixture(autouse=True)
def _migrated(client):
    """The client fixture boots the app, which runs Alembic on the test database."""


def _u() -> str:
    return uuid.uuid4().hex[:12]


def _conv(agent_id, channel, status="open"):
    with get_db() as db:
        conv = Conversation(agent_id=agent_id, channel=channel, status=status)
        db.add(conv)
        db.flush()
        return conv.id


def _msg(agent_id, conv_id, sender_type):
    with get_db() as db:
        db.add(Message(agent_id=agent_id, conversation_id=conv_id, direction="in" if sender_type == "customer" else "out",
                        sender_type=sender_type, type="text", text="hi"))


def _stats(agent_id):
    return next(a["stats"] for a in agents.list_agents() if a["id"] == agent_id)


def _agent(client) -> int:
    return client.post("/api/agents", json={"name": f"Inbox {_u()}", "profile": {"company_name": "Acme"}}).json()["id"]


def test_awaiting_reply_counts_open_chats_still_waiting_on_a_customer_message(client):
    agent_id = _agent(client)
    waiting = _conv(agent_id, "whatsapp")
    _msg(agent_id, waiting, "customer")

    answered = _conv(agent_id, "instagram")
    _msg(agent_id, answered, "customer")
    _msg(agent_id, answered, "ai")  # last message is ours: not waiting

    closed = _conv(agent_id, "whatsapp", status="closed")
    _msg(agent_id, closed, "customer")  # closed: doesn't count even though last message is the customer's

    assert _stats(agent_id)["awaiting_reply"] == 1


def test_awaiting_reply_ignores_voice_conversations(client):
    agent_id = _agent(client)
    voice = _conv(agent_id, "voice")
    _msg(agent_id, voice, "customer")
    assert _stats(agent_id)["awaiting_reply"] == 0
