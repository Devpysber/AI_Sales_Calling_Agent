"""WhatsApp chat agent: inbound webhook, shared prompt memory, and the limits that protect a linked number."""

import re

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
    return whatsapp_service._check_limits(agent_id, lead_id, to, text, reply, False, lambda: None)


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
    assert whatsapp_service.STOP_RE.search("please message band karo") and whatsapp_service.STOP_RE.search("STOP")
    assert not whatsapp_service.STOP_RE.search("booking band karo") and not whatsapp_service.STOP_RE.search("call band karo")
    assert not whatsapp_service.STOP_RE.search("stop by tomorrow at 5")
    assert dnc["do_not_call"]


def test_same_text_to_a_fourth_person_is_refused(open_gap):
    text = "Flat 20% off this week only"
    for i in range(3):
        assert _check(9103, f"91982220002{i}", text) is None
    assert _check(9103, "919822200020", text) is None            # an earlier recipient again is fine
    refused = _check(9103, "919822200029", text.upper())          # case and spacing do not make it different
    assert refused and refused["refused"] and "Personalise" in refused["error"]


def test_second_initiated_send_is_queued_not_sent(monkeypatch):
    started = []

    class FakeTimer:
        daemon = False

        def __init__(self, delay, fn):
            started.append(delay)

        def start(self):
            pass

    monkeypatch.setattr(whatsapp_service.threading, "Timer", FakeTimer)
    assert _check(9104, "919822200030", "First hello") is None
    queued = _check(9104, "919822200031", "Second hello")
    assert queued and queued["queued"] and 15 <= started[0] <= 61
    assert _check(9104, "919822200031", "A reply goes at once", reply=True) is None
