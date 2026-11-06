"""Durable/ordered WhatsApp inbound queue: Redis path (simulated), burst-merge, no-Redis fallback."""

import json
import threading
import time

import pytest

from app.core.config import settings
from app.core import store as store_mod
from app.services import call_service, chat_agent, whatsapp_cloud_service as wa


@pytest.fixture
def agent_id(client, base, monkeypatch):
    aid = int(base.rsplit("/", 1)[1])
    monkeypatch.setattr(settings, "whatsapp_agent_id", aid)
    return aid


@pytest.fixture
def fakes(monkeypatch):
    sent, summaries = [], []
    state = {"end": False}

    def respond(agent_id, conversation_id, history, text, lead, **kw):
        return {"reply": f"echo: {text}", "end_call": state["end"]}

    async def send(to, text, agent_id=None, lead_id=None, call_id=None, phone_number_id=None):
        sent.append((to, text))
        return {"ok": True}

    def submit_now(conv_id, agent_id, lead_id, history):
        summaries.append(conv_id)
        wa._apply_chat_outcome(conv_id, agent_id, lead_id, history)

    monkeypatch.setattr(chat_agent, "respond", respond)
    monkeypatch.setattr(wa, "send_whatsapp_message", send)
    monkeypatch.setattr(wa, "_submit_chat_summary", submit_now)
    monkeypatch.setattr(call_service.CallService, "apply_outcome", lambda self, cid, lid, h: {"summary": "ok"})
    return sent, summaries, state


class FakeRedisClient:
    """Minimal list-backed stand-in for the bits of redis-py whatsapp_service uses."""

    def __init__(self):
        self._lists: dict[str, list[bytes]] = {}
        self._kv: dict[str, bytes] = {}
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            return self._kv.get(key)

    def set(self, key, value, ex=None, nx=False):
        with self._lock:
            if nx and key in self._kv:
                return False
            self._kv[key] = value.encode() if isinstance(value, str) else value
            return True

    def delete(self, key):
        with self._lock:
            self._kv.pop(key, None)

    def rpush(self, key, value):
        with self._lock:
            self._lists.setdefault(key, []).append(value.encode() if isinstance(value, str) else value)

    def lpush(self, key, *values):
        with self._lock:
            lst = self._lists.setdefault(key, [])
            for v in values:
                lst.insert(0, v.encode() if isinstance(v, str) else v)

    def lpop(self, key):
        with self._lock:
            lst = self._lists.get(key) or []
            return lst.pop(0) if lst else None

    def blpop(self, key, timeout=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            v = self.lpop(key)
            if v is not None:
                return (key, v)
            time.sleep(0.02)
        return None


class FakeRedisStore(store_mod.RedisStore):
    def __init__(self):
        self.client = FakeRedisClient()


def _fake_redis(monkeypatch):
    fake = FakeRedisStore()
    monkeypatch.setattr(store_mod, "store", fake)
    return fake


def _payload_msg(wamid, wa_id, text, phone_number_id="555"):
    return {"media_id": None, "mime_type": None, "caption": "", "filename": "", "wamid": wamid, "from": wa_id,
            "name": "Ravi", "type": "text", "text": text, "phone_number_id": phone_number_id,
            "display_phone_number": "918000000099"}


def test_fallback_no_redis_processes_inline(monkeypatch, agent_id):
    """MemoryStore (today's test default): enqueue_inbound must process synchronously, unchanged."""
    assert not isinstance(store_mod.store, store_mod.RedisStore)
    seen = []
    monkeypatch.setattr(wa, "handle_inbound", lambda m: seen.append(m))
    msg = _payload_msg("f1", "919800000001", "hi")
    wa.enqueue_inbound(msg)
    assert seen == [msg]


def test_lock_contention_retries_then_requeues_in_order(monkeypatch, agent_id, fakes):
    """A second consumer racing for a contact's lock while the first already holds it must not process
    (or reorder) that contact's message: it retries, and failing that, requeues at the front."""
    sent, _, _ = fakes
    fake = _fake_redis(monkeypatch)
    assert isinstance(store_mod.store, store_mod.RedisStore)
    monkeypatch.setattr(wa, "LOCK_ACQUIRE_ATTEMPTS", 2)
    monkeypatch.setattr(wa, "LOCK_ACQUIRE_WAIT", 0.05)

    wa_id = "919800000002"
    msg1 = _payload_msg("q1", wa_id, "first")
    name = wa._lock_name(agent_id, wa_id)
    assert store_mod.acquire_lock(name, "external-holder", 30)  # simulate a worker already mid-turn

    # The second "worker" races for the lock while it's held: must give up and requeue, not process.
    wa._process_from_queue(msg1)
    assert sent == []
    assert json.loads(fake.client.lpop(wa.QUEUE_KEY)) == msg1  # requeued, not dropped or reordered

    store_mod.delete("lock:" + name)  # the first worker finishes its turn
    wa._process_from_queue(msg1)
    assert [t for _, t in sent] == ["echo: first"]


def test_two_messages_processed_in_order(monkeypatch, agent_id, fakes):
    sent, _, _ = fakes
    fake = _fake_redis(monkeypatch)
    monkeypatch.setattr(wa, "BURST_POLL_SECONDS", 0.01)  # isolate ordering from burst-merge

    wa_id = "919800000005"
    wa.enqueue_inbound(_payload_msg("o1", wa_id, "first"))
    wa._process_from_queue(json.loads(fake.client.lpop(wa.QUEUE_KEY)))
    wa.enqueue_inbound(_payload_msg("o2", wa_id, "second"))
    wa._process_from_queue(json.loads(fake.client.lpop(wa.QUEUE_KEY)))

    assert [t for _, t in sent] == ["echo: first", "echo: second"]


def test_burst_merge_single_llm_call(monkeypatch, agent_id, fakes):
    sent, _, _ = fakes
    fake = _fake_redis(monkeypatch)
    calls = []

    def respond(aid, cid, history, text, lead, **kw):
        calls.append(text)
        return {"reply": "ok", "end_call": False}

    monkeypatch.setattr(chat_agent, "respond", respond)

    wa_id = "919800000003"
    fake.client.rpush(wa.QUEUE_KEY, json.dumps(_payload_msg("b1", wa_id, "hello")))
    fake.client.rpush(wa.QUEUE_KEY, json.dumps(_payload_msg("b2", wa_id, "are you there")))

    msg = json.loads(fake.client.lpop(wa.QUEUE_KEY))
    wa._process_from_queue(msg)

    assert len(calls) == 1
    assert calls[0] == "hello\nare you there"
    assert len(sent) == 1

    chats = [c for c in _chats_for(agent_id) if c[2] and c[2][0].get("wamid") == "b1"]
    assert chats and len(chats[0][2]) == 3  # two customer rows + one assistant reply


def _chats_for(agent_id):
    from app.core.database import get_db
    from app.models.channel import Conversation, Message
    from sqlalchemy import select
    with get_db() as db:
        out = []
        for c in db.scalars(select(Conversation).where(Conversation.agent_id == agent_id, Conversation.channel == "whatsapp").order_by(Conversation.id)):
            msgs = db.scalars(select(Message).where(Message.conversation_id == c.id).order_by(Message.id)).all()
            turns = [{"role": "customer" if m.sender_type == "customer" else "assistant", "text": m.text,
                      "wamid": m.external_id} for m in msgs]
            out.append((c.id, c.status, turns, c.lead_id))
        return out
