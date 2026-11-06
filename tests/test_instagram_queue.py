"""Instagram burst-merge queue: mirrors tests/test_whatsapp_queue.py's pattern for Instagram."""

import json

import pytest

from app.core.config import settings
from app.core import store as store_mod
from app.services import call_service, chat_agent, instagram_service as ig
from tests.test_whatsapp_queue import FakeRedisStore


@pytest.fixture
def agent_id(client, base, monkeypatch):
    aid = int(base.rsplit("/", 1)[1])
    monkeypatch.setattr(settings, "instagram_agent_id", aid)
    return aid


@pytest.fixture
def fakes(monkeypatch):
    sent, summaries = [], []
    state = {"end": False}

    def respond(agent_id, conversation_id, history, text, lead, **kw):
        return {"reply": f"echo: {text}", "end_call": state["end"]}

    async def send(to, text, agent_id=None, lead_id=None):
        sent.append((to, text))
        return {"ok": True}

    def submit_now(conv_id, agent_id, lead_id, history):
        summaries.append(conv_id)
        ig._apply_chat_outcome(conv_id, agent_id, lead_id, history)

    monkeypatch.setattr(chat_agent, "respond", respond)
    monkeypatch.setattr(ig, "send_instagram_message", send)
    monkeypatch.setattr(ig, "_submit_chat_summary", submit_now)
    monkeypatch.setattr(call_service.CallService, "apply_outcome", lambda self, cid, lid, h: {"summary": "ok"})
    return sent, summaries, state


def _fake_redis(monkeypatch):
    fake = FakeRedisStore()
    monkeypatch.setattr(store_mod, "store", fake)
    return fake


def _payload_msg(mid, sender, text, page_id="PAGE-1"):
    return {"mid": mid, "from": sender, "text": text, "page_id": page_id, "recipient_id": page_id}


def _chats_for(agent_id):
    from app.core.database import get_db
    from app.models.channel import Conversation, Message
    from sqlalchemy import select
    with get_db() as db:
        out = []
        for c in db.scalars(select(Conversation).where(Conversation.agent_id == agent_id, Conversation.channel == "instagram").order_by(Conversation.id)):
            msgs = db.scalars(select(Message).where(Message.conversation_id == c.id).order_by(Message.id)).all()
            turns = [{"role": "customer" if m.sender_type == "customer" else "assistant", "text": m.text,
                      "mid": m.external_id} for m in msgs]
            out.append((c.id, c.status, turns, c.lead_id))
        return out


def test_burst_merge_single_llm_call(monkeypatch, agent_id, fakes):
    sent, _, _ = fakes
    fake = _fake_redis(monkeypatch)
    calls = []

    def respond(aid, cid, history, text, lead, **kw):
        calls.append(text)
        return {"reply": "ok", "end_call": False}

    monkeypatch.setattr(chat_agent, "respond", respond)

    ig_id = "17800000099"
    fake.client.rpush(ig.QUEUE_KEY, json.dumps(_payload_msg("ig-b1", ig_id, "hello")))
    fake.client.rpush(ig.QUEUE_KEY, json.dumps(_payload_msg("ig-b2", ig_id, "are you there")))

    msg = json.loads(fake.client.lpop(ig.QUEUE_KEY))
    ig._process_from_queue(msg)

    assert len(calls) == 1
    assert calls[0] == "hello\nare you there"
    assert len(sent) == 1

    chats = [c for c in _chats_for(agent_id) if c[2] and c[2][0].get("mid") == "ig-b1"]
    assert chats and len(chats[0][2]) == 3  # two customer rows + one assistant reply


def test_different_contacts_not_merged(monkeypatch, agent_id, fakes):
    sent, _, _ = fakes
    fake = _fake_redis(monkeypatch)
    calls = []

    def respond(aid, cid, history, text, lead, **kw):
        calls.append(text)
        return {"reply": "ok", "end_call": False}

    monkeypatch.setattr(chat_agent, "respond", respond)
    monkeypatch.setattr(ig, "BURST_POLL_SECONDS", 0.01)

    ig_a, ig_b = "17800000100", "17800000101"
    fake.client.rpush(ig.QUEUE_KEY, json.dumps(_payload_msg("ig-c1", ig_a, "hi from a")))
    fake.client.rpush(ig.QUEUE_KEY, json.dumps(_payload_msg("ig-c2", ig_b, "hi from b")))

    msg = json.loads(fake.client.lpop(ig.QUEUE_KEY))
    ig._process_from_queue(msg)

    assert calls == ["hi from a"]

    remaining = json.loads(fake.client.lpop(ig.QUEUE_KEY))
    assert remaining["from"] == ig_b
    ig._process_from_queue(remaining)
    assert calls == ["hi from a", "hi from b"]
    assert len(sent) == 2
