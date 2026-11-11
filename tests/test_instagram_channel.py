"""Inbound Instagram chat channel: webhook verification, parsing, chat lifecycle, identity merge."""

import hashlib
import hmac
from datetime import datetime, timedelta, timezone

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
                 "recipient_id": "PAGE-1", "quick_reply_payload": None}
    # non-message event (e.g. read receipt) yields nothing
    receipt = {"entry": [{"id": "PAGE-1", "messaging": [{"sender": {"id": "x"}, "read": {"mid": "m"}}]}]}
    assert ig.parse_webhook(receipt) == []


def test_parse_webhook_attachment_fallback():
    p = _payload()
    p["entry"][0]["messaging"][0]["message"] = {"mid": "mid.2", "attachments": [{"type": "image"}]}
    [m] = ig.parse_webhook(p)
    assert m["text"] == "[attachment]"


def test_resolve_agent_profile_based_priority(agent_id, monkeypatch):
    """A profile-set instagram_account_id must win over the env-based instagram_agent_id fallback."""
    from app.services import agents as agent_svc
    other_agent = agent_id + 1  # not necessarily a real row; env fallback must not match it below
    agent_svc.update_profile(agent_id, {"instagram_account_id": "PAGE-OWNED"}, actor="admin")
    monkeypatch.setattr(settings, "instagram_agent_id", other_agent)
    monkeypatch.setattr(settings, "instagram_page_id", None)
    assert ig.resolve_agent("PAGE-OWNED") == agent_id
    # Unmatched page id still falls back to the env agent, same as before this change.
    assert ig.resolve_agent("SOME-OTHER-PAGE") == other_agent


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


def test_reply_send_retries_then_succeeds(agent_id, fakes, monkeypatch):
    sent, _, _ = fakes
    monkeypatch.setattr(ig, "_SEND_RETRY_BACKOFF_SECONDS", [0, 0])
    calls = []
    real_send = ig._send

    def flaky_send(to, text, agent_id, lead_id):
        calls.append(1)
        if len(calls) < 3:
            return {"ok": False, "error": "transient"}
        return real_send(to, text, agent_id, lead_id)

    monkeypatch.setattr(ig, "_send", flaky_send)
    [m] = ig.parse_webhook(_payload(mid="mid.RETRY1", sender="17800000010"))
    ig.handle_inbound(m)
    assert len(calls) == 3
    assert sent == [("17800000010", "echo: Hi, price?")]
    chats = _chats(agent_id)
    turns = [c for c in chats if c[2] and c[2][0]["mid"] == "mid.RETRY1"][0][2]
    assert [t["role"] for t in turns] == ["customer", "assistant"]


def test_reply_send_persistent_failure_no_reply_row(agent_id, fakes, monkeypatch):
    sent, _, _ = fakes
    monkeypatch.setattr(ig, "_SEND_RETRY_BACKOFF_SECONDS", [0, 0])
    calls = []

    def always_fails(to, text, agent_id, lead_id):
        calls.append(1)
        return {"ok": False, "error": "down"}

    monkeypatch.setattr(ig, "_send", always_fails)
    [m] = ig.parse_webhook(_payload(mid="mid.RETRY2", sender="17800000012"))
    ig.handle_inbound(m)
    assert len(calls) == 3
    assert sent == []
    chats = _chats(agent_id)
    turns = [c for c in chats if c[2] and c[2][0]["mid"] == "mid.RETRY2"][0][2]
    assert [t["role"] for t in turns] == ["customer"]


def test_close_idle_chats(agent_id, fakes):
    _, summaries, _ = fakes
    [m] = ig.parse_webhook(_payload(mid="mid.D", sender="17800000004"))
    ig.handle_inbound(m)
    assert ig.close_idle_chats() == 0
    old = (datetime.now(timezone.utc) - timedelta(minutes=90)).replace(tzinfo=None)
    cid = next(c for c in _chats(agent_id) if c[2] and c[2][0].get("mid") == "mid.D")[0]
    with get_db() as db:
        db.get(Conversation, cid).last_message_at = old
    assert ig.close_idle_chats() == 1
    assert summaries == [cid] and next(c for c in _chats(agent_id) if c[0] == cid)[1] == "closed"


def test_close_chat_locked_and_idempotent(agent_id, fakes):
    _, summaries, _ = fakes
    [m] = ig.parse_webhook(_payload(mid="mid.CLOSE1", sender="17800000099"))
    ig.handle_inbound(m)
    cid = next(c for c in _chats(agent_id) if c[2] and c[2][0].get("mid") == "mid.CLOSE1")[0]
    assert ig.close_chat(cid) is True
    assert summaries == [cid]
    assert next(c for c in _chats(agent_id) if c[0] == cid)[1] == "closed"
    assert ig.close_chat(cid) is False  # already closed -> no-op, no duplicate summary
    assert summaries == [cid]


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


def test_send_instagram_quick_replies_posts_quick_replies_payload(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    class Res:
        status_code = 200
        text = ""
        def json(self): return {}

    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, **k):
            captured["url"] = url
            captured["json"] = k.get("json")
            return Res()

    captured = {}
    monkeypatch.setattr(ig, "settings", SimpleNamespace(instagram_token="t", instagram_page_id="PAGE-1"))
    monkeypatch.setattr(ig.httpx, "AsyncClient", Client)
    out = asyncio.run(ig.send_instagram_quick_replies("17800000001", None, None, "Pick one", ["A", "B", "C", "D"]))
    assert out["ok"] is True
    assert "PAGE-1/messages" in captured["url"]
    payload = captured["json"]
    assert payload["recipient"] == {"id": "17800000001"}
    assert payload["message"]["text"] == "Pick one"
    qrs = payload["message"]["quick_replies"]
    assert len(qrs) == 3  # capped at 3, matching WhatsApp's send_quick_replies_tool cap
    assert qrs[0] == {"content_type": "text", "title": "A", "payload": "opt_0"}


def test_send_instagram_quick_replies_max_options_clamped_to_13(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    class Res:
        status_code = 200
        text = ""
        def json(self): return {}

    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, **k):
            captured["json"] = k.get("json")
            return Res()

    captured = {}
    monkeypatch.setattr(ig, "settings", SimpleNamespace(instagram_token="t", instagram_page_id="PAGE-1"))
    monkeypatch.setattr(ig.httpx, "AsyncClient", Client)
    opts = [f"opt{i}" for i in range(20)]
    out = asyncio.run(ig.send_instagram_quick_replies("17800000001", None, None, "Pick", opts, max_options=99))
    assert out["ok"] is True
    assert len(captured["json"]["message"]["quick_replies"]) == 13


def test_quick_replies_tool_routes_to_instagram(agent_id, monkeypatch):
    from app.services import agent_tools
    from app.services.crm_service import CRMService

    crm = CRMService(agent_id)
    lead = crm.create({"phone": None, "name": "IG only"}, actor="system", allow_no_phone=True)
    with get_db() as db:
        db.add(ContactIdentity(agent_id=agent_id, lead_id=lead["id"], channel="instagram", external_id="ig-qr"))
        conv = Conversation(agent_id=agent_id, lead_id=lead["id"], channel="instagram", status="open")
        db.add(conv)
        db.flush()
        conv_id = conv.id

    calls = []

    async def fake_send(to, agent_id, lead_id, text, options):
        calls.append((to, text, options))
        return {"ok": True}

    monkeypatch.setattr(ig, "send_instagram_quick_replies", fake_send)
    out = agent_tools.send_quick_replies_tool(agent_id, lead["id"], conv_id, "Choose:", ["A", "B"])
    assert out == "Sent."
    assert calls == [("ig-qr", "Choose:", ["A", "B"])]


def test_options_list_tool_routes_to_instagram(agent_id, monkeypatch):
    from app.services import agent_tools
    from app.services.crm_service import CRMService

    crm = CRMService(agent_id)
    lead = crm.create({"phone": None, "name": "IG list"}, actor="system", allow_no_phone=True)
    with get_db() as db:
        db.add(ContactIdentity(agent_id=agent_id, lead_id=lead["id"], channel="instagram", external_id="ig-list"))
        conv = Conversation(agent_id=agent_id, lead_id=lead["id"], channel="instagram", status="open")
        db.add(conv)
        db.flush()
        conv_id = conv.id

    calls = []

    async def fake_send(to, agent_id, lead_id, text, options, max_options=3):
        calls.append((to, text, options, max_options))
        return {"ok": True}

    monkeypatch.setattr(ig, "send_instagram_quick_replies", fake_send)
    opts = [f"opt{i}" for i in range(12)]
    out = agent_tools.send_options_list_tool(agent_id, lead["id"], conv_id, "Choose:", opts, button_label="Pick")
    assert out == "Sent."
    assert len(calls) == 1
    to, text, sent_opts, max_options = calls[0]
    assert to == "ig-list"
    assert text == "Choose:"
    assert len(sent_opts) == 10  # capped at 10 before reaching send_instagram_quick_replies
    assert max_options == 10


def test_send_instagram_message_prefers_per_agent_token_over_global(client, monkeypatch):
    """A manually-pasted per-agent Instagram token must be used over the global settings.instagram_token
    -- and an agent with no token set still falls back to it."""
    import asyncio
    from types import SimpleNamespace

    from app.services import agents

    agent_id = client.post("/api/agents", json={"name": "IG Token Agent", "profile": {"company_name": "IG Token Agent"}}).json()["id"]
    agents.update_profile(agent_id, {"instagram_business_token": "IGAA_agent_token"}, actor="test")

    captured = {}

    class Res:
        status_code = 200
        text = ""
        def json(self): return {}

    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, **k):
            captured["params"] = k.get("params")
            return Res()

    monkeypatch.setattr(ig, "settings", SimpleNamespace(instagram_token="global_tok", instagram_page_id="PAGE-1"))
    monkeypatch.setattr(ig.httpx, "AsyncClient", Client)

    out = asyncio.run(ig.send_instagram_message("17800000001", "hi", agent_id=agent_id))
    assert out["ok"] is True
    assert captured["params"]["access_token"] == "IGAA_agent_token"

    # No per-agent token set: falls back to the global settings token.
    other_agent_id = client.post("/api/agents", json={"name": "IG No Token Agent", "profile": {"company_name": "IG No Token Agent"}}).json()["id"]
    out = asyncio.run(ig.send_instagram_message("17800000001", "hi", agent_id=other_agent_id))
    assert out["ok"] is True
    assert captured["params"]["access_token"] == "global_tok"


def test_send_instagram_document_posts_file_attachment_payload(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    class Res:
        status_code = 200
        text = ""
        def json(self): return {}

    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, **k):
            captured["url"] = url
            captured["json"] = k.get("json")
            return Res()

    captured = {}
    monkeypatch.setattr(ig, "settings", SimpleNamespace(instagram_token="t", instagram_page_id="PAGE-1"))
    monkeypatch.setattr(ig.httpx, "AsyncClient", Client)
    out = asyncio.run(ig.send_instagram_document("17800000001", None, None, "https://example.com/brochure.pdf"))
    assert out["ok"] is True
    assert "PAGE-1/messages" in captured["url"]
    payload = captured["json"]
    assert payload["recipient"] == {"id": "17800000001"}
    assert payload["message"]["attachment"] == {
        "type": "file",
        "payload": {"url": "https://example.com/brochure.pdf", "is_reusable": True},
    }


def test_send_brochure_tool_routes_to_instagram(agent_id, monkeypatch):
    from app.services import agent_tools
    from app.services.crm_service import CRMService

    crm = CRMService(agent_id)
    lead = crm.create({"phone": None, "name": "IG brochure"}, actor="system", allow_no_phone=True)
    with get_db() as db:
        db.add(ContactIdentity(agent_id=agent_id, lead_id=lead["id"], channel="instagram", external_id="ig-doc"))
        conv = Conversation(agent_id=agent_id, lead_id=lead["id"], channel="instagram", status="open")
        db.add(conv)
        db.flush()
        conv_id = conv.id

    calls = []

    async def fake_send(to, agent_id, lead_id, link):
        calls.append((to, link))
        return {"ok": True}

    monkeypatch.setattr(ig, "send_instagram_document", fake_send)
    out = agent_tools.send_brochure_tool(agent_id, lead["id"], conv_id, "https://example.com/brochure.pdf")
    assert out == "Brochure sent."
    assert calls == [("ig-doc", "https://example.com/brochure.pdf")]


def test_sync_messenger_profile_posts_correct_payload(agent_id, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from app.services import agents

    agents.update_profile(agent_id, {
        "instagram_ice_breakers": ["What are your hours?", "Book an appointment"],
        "instagram_greeting": "Hi {{user_first_name}}! Thanks for reaching out.",
    })

    class Res:
        status_code = 200
        text = ""
        def json(self): return {}

    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, **k):
            captured["url"] = url
            captured["json"] = k.get("json")
            return Res()

    captured = {}
    monkeypatch.setattr(ig, "settings", SimpleNamespace(instagram_token="t", instagram_page_id="PAGE-1"))
    monkeypatch.setattr(ig.httpx, "AsyncClient", Client)
    out = asyncio.run(ig.sync_messenger_profile(agent_id))
    assert out["ok"] is True
    assert "PAGE-1/messenger_profile" in captured["url"]
    payload = captured["json"]
    assert payload["ice_breakers"] == [{"call_to_actions": [
        {"question": "What are your hours?", "payload": "ICE_BREAKER_0"},
        {"question": "Book an appointment", "payload": "ICE_BREAKER_1"},
    ]}]
    assert payload["greeting"] == {"text": "Hi {{user_first_name}}! Thanks for reaching out."}


def test_sync_messenger_profile_not_configured(agent_id, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    monkeypatch.setattr(ig, "settings", SimpleNamespace(instagram_token="", instagram_page_id=""))
    out = asyncio.run(ig.sync_messenger_profile(agent_id))
    assert out == {"ok": False, "error": "Instagram credentials not configured"}


def test_instagram_ice_breakers_validation_caps_count_and_length(agent_id):
    from app.services import agents

    profile = agents.update_profile(agent_id, {
        "instagram_ice_breakers": ["a" * 200, "q2", "q3", "q4", "q5 should be dropped"],
    })
    assert len(profile["instagram_ice_breakers"]) == 4
    assert len(profile["instagram_ice_breakers"][0]) == 80
    assert "q5 should be dropped" not in profile["instagram_ice_breakers"]


def test_messenger_profile_sync_endpoint_400_when_not_configured(agent_id, client, base, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(ig, "settings", SimpleNamespace(instagram_token="", instagram_page_id=""))
    res = client.post(f"{base}/instagram/messenger-profile/sync")
    assert res.status_code == 400


def test_adapter_send_media_delegates_to_send_instagram_document(monkeypatch):
    from app.services.channels.instagram import InstagramAdapter

    calls = []

    async def fake_send_instagram_document(to, agent_id, lead_id, link):
        calls.append((to, agent_id, lead_id, link))
        return {"ok": True}

    monkeypatch.setattr(ig, "send_instagram_document", fake_send_instagram_document)
    result = InstagramAdapter().send_media("17800000001", "https://example.com/brochure.pdf", agent_id=1, lead_id=2)
    assert result == {"ok": True}
    assert calls == [("17800000001", 1, 2, "https://example.com/brochure.pdf")]
