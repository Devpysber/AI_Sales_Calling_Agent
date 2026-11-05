"""Inbound Instagram chat channel: webhook verification, parsing, chat lifecycle, identity merge."""

import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import get_db
from app.main import app
from app.models.channel import ContactIdentity, Conversation, Message
from app.services import call_service, chat_agent, instagram_service as ig
from app.services.identity import merge_identity


def _payload(mid="mid.1", text="Hi, price?", sender="17800000001", page_id="PAGE-1"):
    msg = {"mid": mid, "text": text}
    return {"object": "instagram", "entry": [{"id": page_id, "messaging": [
        {"sender": {"id": sender}, "recipient": {"id": page_id}, "message": msg}]}]}


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
        assert kw.get("channel") == "instagram"
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


def _chats(agent_id):
    with get_db() as db:
        out = []
        for c in db.query(Conversation).filter(Conversation.agent_id == agent_id, Conversation.channel == "instagram").order_by(Conversation.id):
            msgs = db.query(Message).filter(Message.conversation_id == c.id).order_by(Message.id).all()
            turns = [{"role": "customer" if m.sender_type == "customer" else "assistant", "text": m.text,
                      "mid": m.external_id} for m in msgs]
            out.append((c.id, c.status, turns, c.lead_id))
        return out


def test_signature(monkeypatch):
    monkeypatch.setattr(settings, "instagram_app_secret", "s3cret")
    body = b'{"a":1}'
    good = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert ig.verify_signature(body, good)
    assert not ig.verify_signature(body, "sha256=deadbeef")
    assert not ig.verify_signature(body, None)
    monkeypatch.setattr(settings, "instagram_app_secret", "")
    assert ig.verify_signature(body, None)


def test_webhook_verify_and_post(monkeypatch):
    monkeypatch.setattr(settings, "instagram_verify_token", "tok")
    monkeypatch.setattr(settings, "instagram_app_secret", "")
    anon = TestClient(app)
    ok = anon.get("/api/instagram/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "tok", "hub.challenge": "999"})
    assert ok.status_code == 200 and ok.text == "999"
    assert anon.get("/api/instagram/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "x", "hub.challenge": "1"}).status_code == 403
    seen = []
    monkeypatch.setattr(ig, "handle_inbound", lambda m: seen.append(m))
    assert anon.post("/api/instagram/webhook", json=_payload()).json() == {"ok": True}
    assert len(seen) == 1
    monkeypatch.setattr(settings, "instagram_app_secret", "s3cret")
    assert anon.post("/api/instagram/webhook", json=_payload()).status_code == 403


def test_parse_webhook():
    [m] = ig.parse_webhook(_payload())
    assert m == {"mid": "mid.1", "from": "17800000001", "text": "Hi, price?", "page_id": "PAGE-1",
                 "recipient_id": "PAGE-1"}
    # non-message event (e.g. read receipt) yields nothing
    receipt = {"entry": [{"id": "PAGE-1", "messaging": [{"sender": {"id": "x"}, "read": {"mid": "m"}}]}]}
    assert ig.parse_webhook(receipt) == []


def test_parse_webhook_attachment_fallback():
    p = _payload()
    p["entry"][0]["messaging"][0]["message"] = {"mid": "mid.2", "attachments": [{"type": "image"}]}
    [m] = ig.parse_webhook(p)
    assert m["text"] == "[attachment]"


def test_inbound_creates_phoneless_lead_and_chat(agent_id, fakes):
    sent, summaries, state = fakes
    [m] = ig.parse_webhook(_payload(mid="mid.A", sender="17800000002"))
    ig.handle_inbound(m)
    ig.handle_inbound(m)  # retry: deduped via unique (agent_id, external_id)
    chats = _chats(agent_id)
    assert len(chats) == 1 and chats[0][1] == "open"
    assert [t["role"] for t in chats[0][2]] == ["customer", "assistant"]
    assert sent == [("17800000002", "echo: Hi, price?")]
    with get_db() as db:
        ci = db.query(ContactIdentity).filter(ContactIdentity.agent_id == agent_id,
              ContactIdentity.channel == "instagram", ContactIdentity.external_id == "17800000002").first()
        assert ci is not None and ci.lead_id == chats[0][3]
        from app.models.lead import Lead
        lead = db.get(Lead, chats[0][3])
        assert lead.phone is None

    state["end"] = True
    [m2] = ig.parse_webhook(_payload(mid="mid.B", text="thanks bye", sender="17800000002"))
    ig.handle_inbound(m2)
    chats = _chats(agent_id)
    assert len(chats) == 1 and chats[0][1] == "closed" and len(chats[0][2]) == 4
    assert summaries == [chats[0][0]]


def test_attachment_gets_generic_text_passed_to_agent(agent_id, fakes):
    # First slice: attachments become the text "[attachment]" and go through the normal chat_agent
    # turn (not a fixed reply) -- full media download/transcription is deferred, same as WhatsApp's was.
    sent, _, _ = fakes
    p = _payload(mid="mid.C", sender="17800000003")
    p["entry"][0]["messaging"][0]["message"] = {"mid": "mid.C", "attachments": [{"type": "image"}]}
    [m] = ig.parse_webhook(p)
    ig.handle_inbound(m)
    assert sent and sent[0][1] == "echo: [attachment]"


def test_identity_merge_reassigns_to_phone_lead(agent_id):
    from app.services.crm_service import CRMService
    crm = CRMService(agent_id)
    phone_lead = crm.create({"phone": "+919811100099", "name": "Existing"}, actor="system")
    ig_lead = crm.create({"phone": None, "name": "IG only"}, actor="system", allow_no_phone=True)
    with get_db() as db:
        db.add(ContactIdentity(agent_id=agent_id, lead_id=ig_lead["id"], channel="instagram", external_id="ig-x"))
        conv = Conversation(agent_id=agent_id, lead_id=ig_lead["id"], channel="instagram", status="open")
        db.add(conv)
        db.flush()
        conv_id = conv.id

    merge_identity(agent_id, ig_lead["id"], phone_lead["id"])

    with get_db() as db:
        ci = db.query(ContactIdentity).filter(ContactIdentity.external_id == "ig-x").first()
        assert ci.lead_id == phone_lead["id"]
        assert db.get(Conversation, conv_id).lead_id == phone_lead["id"]
