"""Tests for the per-channel analytics report (app.services.analytics.channel_report)."""

import uuid
from datetime import datetime, timedelta

import pytest

from app.core.database import get_db
from app.models import Conversation, Message, ToolRun
from app.services import analytics


@pytest.fixture(autouse=True)
def _migrated(client):
    """The client fixture boots the app, which runs Alembic on the test database."""


def _u() -> str:
    return uuid.uuid4().hex[:12]


def test_empty_agent_returns_zeroed_shape():
    agent_id = 90000 + hash(_u()) % 1000
    rep = analytics.channel_report(agent_id, days=14)
    assert rep["days"] == 14
    # Voice is always reported (even zeroed); other channels only appear when they have data.
    assert rep["channels"] == [{"channel": "voice", "conversations": 0, "open": 0, "closed": 0,
                                "resolution_rate": None, "avg_first_response_seconds": None}]
    assert rep["tools"] == []


def test_channel_volume_resolution_and_tool_usage():
    agent_id = 91000 + hash(_u()) % 1000
    now = datetime.utcnow()

    with get_db() as db:
        # WhatsApp: 2 conversations, one closed one open.
        wa_closed = Conversation(agent_id=agent_id, channel="whatsapp", status="closed", created_at=now)
        wa_open = Conversation(agent_id=agent_id, channel="whatsapp", status="open", created_at=now)
        # Instagram: 1 closed conversation.
        ig_closed = Conversation(agent_id=agent_id, channel="instagram", status="closed", created_at=now)
        db.add_all([wa_closed, wa_open, ig_closed])
        db.flush()

        # First-response time fixture on wa_closed: customer at t0, ai reply 30s later.
        t0 = now
        db.add(Message(conversation_id=wa_closed.id, agent_id=agent_id, direction="in", sender_type="customer",
                       text="hi", created_at=t0))
        db.add(Message(conversation_id=wa_closed.id, agent_id=agent_id, direction="out", sender_type="ai",
                       text="hello", created_at=t0 + timedelta(seconds=30)))
        # wa_open: customer at t0, ai reply 90s later.
        db.add(Message(conversation_id=wa_open.id, agent_id=agent_id, direction="in", sender_type="customer",
                       text="hi", created_at=t0))
        db.add(Message(conversation_id=wa_open.id, agent_id=agent_id, direction="out", sender_type="ai",
                       text="hello", created_at=t0 + timedelta(seconds=90)))

        tool_name = "lookup_" + _u()
        db.add(ToolRun(agent_id=agent_id, conversation_id=wa_closed.id, tool_name=tool_name, status="ok"))
        db.add(ToolRun(agent_id=agent_id, conversation_id=wa_closed.id, tool_name=tool_name, status="ok"))
        db.add(ToolRun(agent_id=agent_id, conversation_id=wa_closed.id, tool_name=tool_name, status="error"))
        db.commit()

    rep = analytics.channel_report(agent_id, days=14)
    by_channel = {c["channel"]: c for c in rep["channels"]}

    assert by_channel["whatsapp"]["conversations"] == 2
    assert by_channel["whatsapp"]["closed"] == 1
    assert by_channel["whatsapp"]["open"] == 1
    assert by_channel["whatsapp"]["resolution_rate"] == 50.0
    # Average of 30s and 90s first-response times.
    assert by_channel["whatsapp"]["avg_first_response_seconds"] == 60

    assert by_channel["instagram"]["conversations"] == 1
    assert by_channel["instagram"]["closed"] == 1
    assert by_channel["instagram"]["resolution_rate"] == 100.0

    tools = {t["tool_name"]: t for t in rep["tools"]}
    assert tools[tool_name]["calls"] == 3
    assert tools[tool_name]["ok"] == 2
    assert tools[tool_name]["error"] == 1
    assert tools[tool_name]["success_rate"] == pytest.approx(66.7, abs=0.1)


def test_voice_channel_included_with_calls():
    from app.models.call import Call

    agent_id = 92000 + hash(_u()) % 1000
    with get_db() as db:
        db.add(Call(agent_id=agent_id, session_id=_u(), status="Completed", duration=60))
        db.add(Call(agent_id=agent_id, session_id=_u(), status="No Answer", duration=0))
        db.commit()

    rep = analytics.channel_report(agent_id, days=14)
    by_channel = {c["channel"]: c for c in rep["channels"]}
    assert by_channel["voice"]["conversations"] == 2
    assert by_channel["voice"]["closed"] == 2
    assert by_channel["voice"]["resolution_rate"] == 100.0
