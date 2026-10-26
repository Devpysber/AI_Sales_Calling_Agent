"""WhatsApp chat agent: inbound webhook, shared prompt memory, and the limits that protect a linked number."""

import asyncio
import re
import time

import httpx
import pytest
from sqlalchemy import func, select

from app.core.database import get_db
from app.models.whatsapp_message import WhatsAppMessage
from app.services import agent, whatsapp_agent, whatsapp_service
from app.services.call_service import apply_turn_signals
from app.services.crm_service import CRMService


@pytest.fixture(autouse=True)
def no_reply(monkeypatch):
    """The inbound webhook schedules the reply as a background task: keep it (and the bridge) out of these tests."""
    async def noop(*a, **k):
        return None
    monkeypatch.setattr(whatsapp_agent, "handle_inbound", noop)


def _post(client, agent_id, digits, text, message_id, name="Rahul", secret=None):
    headers = {"X-Bridge-Secret": secret} if secret else {}
    return client.post("/api/whatsapp/inbound", headers=headers,
                       json={"agent_id": agent_id, "from": digits, "name": name, "text": text, "message_id": message_id, "timestamp": 1})


def _agent_id(base):
    return int(base.rsplit("/", 1)[1])


# ---------------- inbound webhook ----------------

def test_inbound_rejects_a_bad_secret(client, base, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "wa_bridge_secret", "right")
    assert _post(client, _agent_id(base), "919811100001", "hi", "m-bad", secret="wrong").status_code == 401
    assert _post(client, _agent_id(base), "919811100001", "hi", "m-bad").status_code == 401
    assert _post(client, _agent_id(base), "919811100001", "hi", "m-good", secret="right").status_code == 200


def test_inbound_dedupes_on_message_id(client, base):
    aid = _agent_id(base)
    assert _post(client, aid, "919811100002", "hello", "m-dup").json() == {"ok": True}
    assert _post(client, aid, "919811100002", "hello", "m-dup").json().get("duplicate") is True
    with get_db() as db:
        assert db.scalar(select(func.count(WhatsAppMessage.id)).where(WhatsAppMessage.wa_message_id == "m-dup")) == 1


def test_inbound_creates_then_matches_the_lead_by_phone(client, base):
    aid = _agent_id(base)
    _post(client, aid, "919811100003", "hi", "m-a1", name="Neha")
    lead = CRMService(aid).find_by_phone("+919811100003")
    assert lead and lead["source"] == "whatsapp" and lead["name"] == "Neha"
    _post(client, aid, "9811100003", "price?", "m-a2")  # same number written without the country code
    with get_db() as db:
        ids = set(db.scalars(select(WhatsAppMessage.lead_id).where(WhatsAppMessage.wa_message_id.in_(["m-a1", "m-a2"]))))
    assert ids == {lead["id"]}
    chat = client.get(f"{base}/leads/{lead['id']}/whatsapp").json()["items"]
    assert [m["text"] for m in chat] == ["hi", "price?"]


# ---------------- prompts ----------------

def test_call_prompt_without_whatsapp_history_is_unchanged(base):
    aid = _agent_id(base)
    system = agent._system_prompt(agent.agents.get_profile(aid), {"id": 424242, "name": "X"}, [], aid)
    assert "Recent WhatsApp chat" not in system
    assert "no re-asking what they already told you.\n\n# Company brief" in system  # the original join, byte for byte


def test_call_prompt_carries_recent_whatsapp_chat(base):
    aid = _agent_id(base)
    lead = CRMService(aid).create({"phone": "9811100004", "name": "Ravi"})
    with get_db() as db:
        for direction, text in (("in", "Need a demo"), ("out", "Sure, when?"), ("in", "x" * 300)):
            db.add(WhatsAppMessage(agent_id=aid, lead_id=lead["id"], phone="919811100004", direction=direction, text=text))
    agent._memory_cache.clear()
    system = agent._system_prompt(agent.agents.get_profile(aid), lead, [], aid)
    assert re.search(r"# Recent WhatsApp chat with this \w+ \(newest last\)", system)
    section = system[system.index("# Recent WhatsApp chat"):system.index("# Company brief")]
    assert section.index("Need a demo") < section.index("Sure, when?") < section.index("xxx")
    assert "x" * 141 not in section  # each message is cut to 140 characters


def test_whatsapp_channel_swaps_voice_rules_for_chat_rules(base):
    aid = _agent_id(base)
    messages, _ = agent.build_messages(aid, [], "hi", {"id": 1, "name": "X"}, use_embeddings=False, channel="whatsapp")
    system = messages[0]["content"]
    assert "This is a WhatsApp chat, not a phone call" in system
    assert "Hinglish in Latin script" in system and '"action"' in system
    assert '"end_call"' not in system.rsplit("# This is a WhatsApp chat", 1)[1]
    assert "Recent WhatsApp chat" not in system


# ---------------- shared CRM logic ----------------

class _FakeCRM:
    def __init__(self, lead):
        self.lead, self.updates = lead, []

    def get(self, lead_id):
        return self.lead

    def update(self, lead_id, data, **kw):
        self.updates.append(data)


def test_apply_turn_signals_maps_a_turn_onto_the_lead():
    crm = _FakeCRM({"meeting_at": None})
    result = {"crm_update": {"requirement": "2BHK in Indore", "email": "a@b.co"}, "qualification": "Warm", "intent": "pricing"}
    apply_turn_signals(crm, 5, result)
    assert crm.updates == [{"email": "a@b.co", "requirements": "2BHK in Indore", "qualification": "Warm", "status": "Interested"}]
    apply_turn_signals(crm, None, result)   # no lead: nothing happens
    assert len(crm.updates) == 1


# ---------------- reply bursts ----------------

def test_one_reply_covers_the_whole_burst_and_a_dashboard_reply_pauses_it():
    class R:
        def __init__(self, id, direction, text, provider=None):
            self.id, self.direction, self.text, self.provider = id, direction, text, provider
    rows = [R(1, "in", "hi"), R(2, "out", "hello"), R(3, "in", "price?"), R(4, "in", "and demo?")]
    history, burst = whatsapp_agent.unanswered(rows, None)
    assert [r.id for r in history] == [1, 2] and [r.id for r in burst] == [3, 4]
    assert whatsapp_agent.unanswered(rows, 4)[1] == []   # already answered up to 4


# ---------------- send limits ----------------

def _check(agent_id, to, text, reply=False, lead_id=None):
    return whatsapp_service._check_limits(agent_id, lead_id, to, text, reply, False, {"to": to, "text": text, "agent_id": agent_id})


@pytest.fixture
def open_gap(monkeypatch):
    monkeypatch.setattr(whatsapp_service, "GAP_SECONDS", (0, 0))


def test_daily_cap_on_new_conversations(open_gap, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "wa_daily_new_chats", 2)
    assert _check(9101, "919822200001", "Hello one") is None
    assert _check(9101, "919822200002", "Hello two") is None
    refused = _check(9101, "919822200003", "Hello three")
    assert refused and refused["refused"] and "daily limit of 2" in refused["error"]
    # someone who already wrote to us is not a new conversation
    with get_db() as db:
        db.add(WhatsAppMessage(agent_id=9101, lead_id=None, phone="919822200004", direction="in", text="hi"))
    assert _check(9101, "919822200004", "Hello four") is None


def test_opt_out_blocks_everything_and_dnc_blocks_initiated(open_gap):
    crm = CRMService(9102)
    lead = crm.create({"phone": "9822200010", "name": "Stopper"})
    assert _check(9102, "919822200010", "Hi again", reply=True) is None
    whatsapp_service.mark_opt_out(9102, lead["id"])
    for reply in (False, True):
        blocked = _check(9102, "919822200010", "Hi again", reply=reply)
        assert blocked and blocked["refused"] and "asked not to be messaged" in blocked["error"]
    dnc = crm.create({"phone": "9822200011", "do_not_call": True})
    assert _check(9102, "919822200011", "Offer for you")["refused"]
    assert _check(9102, "919822200011", "Thanks for writing", reply=True) is None   # answering them is still fine
    assert dnc["do_not_call"]


def test_only_a_whole_message_stop_request_opts_out():
    stop = whatsapp_service.is_stop_request
    for text in ("STOP", "Stop!", "please message band karo", "msg mat bhejo", "WhatsApp band karo 🙏", "Don't message me",
                 "stop texting me pls", "unsubscribe", "opt out", "message mat karo please"):
        assert stop(text), text
    for text in ("booking band karo", "call band karo", "stop by tomorrow at 5", "don't text me, just call me at 5",
                 "how do I unsubscribe from the newsletter but keep updates", "I will unsubscribe later"):
        assert not stop(text), text


def test_same_text_to_a_fourth_person_is_refused(open_gap):
    text = "Flat 20% off this week only"
    for i in range(3):
        assert _check(9103, f"91982220002{i}", text) is None
    assert _check(9103, "919822200020", text) is None            # an earlier recipient again is fine
    refused = _check(9103, "919822200029", text.upper())          # case and spacing do not make it different
    assert refused and refused["refused"] and "Personalise" in refused["error"]


def test_second_initiated_send_is_queued_in_the_store_and_drained(monkeypatch):
    from app.core import store
    store.delete(whatsapp_service.QUEUE_KEY)
    assert _check(9104, "919822200030", "First hello") is None
    queued = _check(9104, "919822200031", "Second hello")
    jobs = store.get_json(whatsapp_service.QUEUE_KEY)
    assert queued and queued["queued"] and len(jobs) == 1 and 15 <= jobs[0]["due"] - time.time() <= 61
    assert _check(9104, "919822200031", "A reply goes at once", reply=True) is None
    sent = []
    monkeypatch.setattr(whatsapp_service, "send_whatsapp_message_sync", lambda **kw: sent.append(kw) or {"ok": True})
    assert whatsapp_service.drain_queue() == 0 and not sent        # not due yet
    store.set_json(whatsapp_service.QUEUE_KEY, [{**jobs[0], "due": time.time() - 1}])
    assert whatsapp_service.drain_queue() == 1
    assert sent[0]["to"] == "919822200031" and sent[0]["_cleared"] and not store.get_json(whatsapp_service.QUEUE_KEY)


# ---------------- bridge send outcomes ----------------

class _Res:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body or {}
        self.content = b"{}"

    def json(self):
        return self._body


def _fake_bridge(monkeypatch, send):
    """A connected bridge whose /send does `send(kwargs)`; Composio is configured so any fall-through is visible."""
    calls = {"send": 0, "other": 0}

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return _Res(200, {"status": "connected"})

        async def post(self, url, **kw):
            if url.endswith("/send"):
                calls["send"] += 1
                return send(kw)
            calls["other"] += 1
            return _Res(200)

    from app.core.config import settings
    monkeypatch.setattr(whatsapp_service.httpx, "AsyncClient", Client)
    monkeypatch.setattr(settings, "composio_api_key", "k", raising=False)
    return calls


def _send(**kw):
    return asyncio.run(whatsapp_service.send_whatsapp_message(to="919833300001", text="hello", agent_id=9110, **kw))


def test_a_failed_bridge_send_never_falls_through_to_another_provider(monkeypatch):
    calls = _fake_bridge(monkeypatch, lambda kw: _Res(500, {"error": "boom"}))
    out = _send(reply=True)
    assert out == {"ok": False, "error": "boom"} and calls == {"send": 1, "other": 0}


def test_a_bridge_timeout_is_reported_unknown_and_not_resent(monkeypatch):
    def timeout(kw):
        assert kw["timeout"] >= 60
        raise httpx.ReadTimeout("slow")
    calls = _fake_bridge(monkeypatch, timeout)
    out = _send(reply=True)
    assert not out["ok"] and "send outcome unknown" in out["error"] and calls == {"send": 1, "other": 0}


def test_a_rate_limited_reply_is_an_error_but_an_initiated_send_is_queued(monkeypatch):
    from app.core import store
    store.delete(whatsapp_service.QUEUE_KEY)
    calls = _fake_bridge(monkeypatch, lambda kw: _Res(429, {"error": "fast"}))
    reply = _send(reply=True)
    assert reply["rate_limited"] and "rate limit" in reply["error"] and not store.get_json(whatsapp_service.QUEUE_KEY)
    assert _send(_cleared=True)["rate_limited"]
    assert _send()["queued"] and len(store.get_json(whatsapp_service.QUEUE_KEY)) == 1   # we started it: held, not lost
    assert calls["other"] == 0


def test_dashboard_send_respects_opt_out_and_reports_failure(client, base, monkeypatch):
    aid = _agent_id(base)
    lead = CRMService(aid).create({"phone": "9833300002", "name": "Quiet"})
    whatsapp_service.mark_opt_out(aid, lead["id"])
    out = asyncio.run(whatsapp_service.send_whatsapp_message(to="9833300002", text="hi", agent_id=aid, via="dashboard"))
    assert out["refused"] and "asked not to be messaged" in out["error"]
    res = client.post(f"{base}/whatsapp/send", json={"to": "9833300002", "text": "hi"})
    assert res.status_code == 409 and "asked not to be messaged" in res.json()["detail"]

    async def failing(**kw):
        return {"ok": False, "error": "WhatsApp account not connected."}
    monkeypatch.setattr(whatsapp_service, "send_whatsapp_message", failing)
    res = client.post(f"{base}/whatsapp/send", json={"to": "9833300003", "text": "hi"})
    assert res.status_code == 502 and "not connected" in res.json()["detail"]


# ---------------- reply bookkeeping ----------------

def test_a_failed_llm_or_send_leaves_the_burst_unanswered(base, monkeypatch):
    from app.core import store
    aid = _agent_id(base)
    lead = CRMService(aid).create({"phone": "9833300010", "name": "Asha"})
    with get_db() as db:
        db.add(WhatsAppMessage(agent_id=aid, lead_id=lead["id"], phone="919833300010", direction="in", text="price?"))
    key = f"wa:answered:{aid}:{lead['id']}"
    store.delete(key)

    def llm_down(*a, **k):
        raise RuntimeError("llm down")
    monkeypatch.setattr(agent, "respond", llm_down)
    with pytest.raises(RuntimeError):
        whatsapp_agent._reply(aid, lead["id"], "919833300010")
    assert store.get_json(key) is None

    monkeypatch.setattr(agent, "respond", lambda *a, **k: {"reply": "It is 50 lakh", "action": "none", "crm_update": {}, "qualification": None, "intent": None})
    monkeypatch.setattr(whatsapp_service, "send_whatsapp_message_sync", lambda **kw: {"ok": False, "error": "bridge down"})
    assert whatsapp_agent._reply(aid, lead["id"], "919833300010") is False
    assert store.get_json(key) is None

    monkeypatch.setattr(whatsapp_service, "send_whatsapp_message_sync", lambda **kw: {"ok": True})
    assert whatsapp_agent._reply(aid, lead["id"], "919833300010") is True
    assert store.get_json(key) is not None


def test_a_lock_is_released_only_by_its_owner():
    from app.core import store
    assert store.acquire_lock("t-lock", "a", 60)
    assert not store.release_lock("t-lock", "b") and not store.acquire_lock("t-lock", "b", 60)
    assert store.release_lock("t-lock", "a") and store.acquire_lock("t-lock", "b", 60)
    store.release_lock("t-lock", "b")


# ---------------- senders without a number (@lid) ----------------

def test_a_lid_sender_gets_a_numberless_lead_matched_by_chat_id(client, base):
    aid = _agent_id(base)
    body = {"agent_id": aid, "chat_id": "2345678901234@lid", "lid": True, "name": "Hidden", "text": "hello", "message_id": "m-lid1"}
    assert client.post("/api/whatsapp/inbound", json=body).json() == {"ok": True}
    client.post("/api/whatsapp/inbound", json={**body, "text": "again", "message_id": "m-lid2"})
    with get_db() as db:
        rows = db.execute(select(WhatsAppMessage.lead_id, WhatsAppMessage.chat_id, WhatsAppMessage.phone)
                          .where(WhatsAppMessage.wa_message_id.in_(["m-lid1", "m-lid2"]))).all()
    assert len({r.lead_id for r in rows}) == 1 and rows[0].lead_id and rows[0].chat_id == "2345678901234@lid"
    assert rows[0].phone == "2345678901234@lid"      # replies are addressed to the chat id
    lead = CRMService(aid).get(rows[0].lead_id)
    assert lead["phone"] == "" and "whatsapp_lid" in lead["tags"]


def test_a_lead_without_a_number_cannot_be_dialled(base):
    from app.models.lead import Lead
    from app.services.call_service import CallError, CallService
    aid = _agent_id(base)
    with get_db() as db:
        row = Lead(agent_id=aid, phone="", source="whatsapp", status="New", language="en-IN", retry_count=0)
        db.add(row)
        db.flush()
        lead_id = row.id
    with pytest.raises(CallError, match="no number"):
        CallService(aid).start(lead_id, trigger="manual", actor="team")
