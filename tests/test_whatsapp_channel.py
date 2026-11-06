"""Inbound WhatsApp chat channel: webhook verification, parsing, chat lifecycle."""

import hashlib
import hmac
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import get_db
from app.main import app
from app.models.channel import Conversation, Message
from app.services import agent as agent_svc
from app.services import call_service, chat_agent, whatsapp_service as wa


def _payload(wamid="wamid.1", text="Hi, price?", wa_id="919811100001", kind="text"):
    msg = {"from": wa_id, "id": wamid, "timestamp": "1700000000", "type": kind}
    if kind == "text":
        msg["text"] = {"body": text}
    return {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"display_phone_number": "918000000099", "phone_number_id": "555"},
        "contacts": [{"profile": {"name": "Ravi"}, "wa_id": wa_id}], "messages": [msg]}}]}]}


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
        assert kw.get("channel", "whatsapp") == "whatsapp"
        return {"reply": f"echo: {text}", "end_call": state["end"]}

    async def send(to, text, agent_id=None, lead_id=None, call_id=None, phone_number_id=None):
        sent.append((to, text))
        return {"ok": True}

    # Run the post-chat outcome synchronously and record it, instead of on the background thread pool.
    def submit_now(conv_id, agent_id, lead_id, history):
        summaries.append(conv_id)
        wa._apply_chat_outcome(conv_id, agent_id, lead_id, history)

    monkeypatch.setattr(chat_agent, "respond", respond)
    monkeypatch.setattr(wa, "send_whatsapp_message", send)
    monkeypatch.setattr(wa, "_submit_chat_summary", submit_now)
    monkeypatch.setattr(call_service.CallService, "apply_outcome", lambda self, cid, lid, h: {"summary": "ok"})
    return sent, summaries, state


def _chats(agent_id):
    with get_db() as db:
        out = []
        for c in db.query(Conversation).filter(Conversation.agent_id == agent_id, Conversation.channel == "whatsapp").order_by(Conversation.id):
            msgs = db.query(Message).filter(Message.conversation_id == c.id).order_by(Message.id).all()
            turns = [{"role": "customer" if m.sender_type == "customer" else "assistant", "text": m.text,
                      "wamid": m.external_id} for m in msgs]
            out.append((c.id, c.status, turns, c.lead_id))
        return out


def test_signature(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_app_secret", "s3cret")
    body = b'{"a":1}'
    good = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert wa.verify_signature(body, good)
    assert not wa.verify_signature(body, "sha256=deadbeef")
    assert not wa.verify_signature(body, None)
    monkeypatch.setattr(settings, "whatsapp_app_secret", "")
    assert wa.verify_signature(body, None)


def test_webhook_verify_and_post(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_verify_token", "tok")
    monkeypatch.setattr(settings, "whatsapp_app_secret", "")
    anon = TestClient(app)
    ok = anon.get("/api/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "tok", "hub.challenge": "12345"})
    assert ok.status_code == 200 and ok.text == "12345"
    assert anon.get("/api/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "x", "hub.challenge": "1"}).status_code == 403
    seen = []
    monkeypatch.setattr(wa, "handle_inbound", lambda m: seen.append(m))
    assert anon.post("/api/whatsapp/webhook", json=_payload()).json() == {"ok": True}
    assert len(seen) == 1
    monkeypatch.setattr(settings, "whatsapp_app_secret", "s3cret")
    assert anon.post("/api/whatsapp/webhook", json=_payload()).status_code == 403


def test_parse_webhook():
    [m] = wa.parse_webhook(_payload())
    assert m == {"wamid": "wamid.1", "from": "919811100001", "name": "Ravi", "type": "text", "text": "Hi, price?",
                 "phone_number_id": "555", "display_phone_number": "918000000099",
                 "media_id": None, "mime_type": None, "caption": "", "filename": ""}
    statuses = {"entry": [{"changes": [{"value": {"metadata": {}, "statuses": [{"id": "x", "status": "read"}]}}]}]}
    assert wa.parse_webhook(statuses) == []


def test_inbound_creates_chat_dedupes_and_closes(agent_id, fakes):
    sent, summaries, state = fakes
    [m] = wa.parse_webhook(_payload(wamid="wamid.A", wa_id="919811100002"))
    wa.handle_inbound(m)
    wa.handle_inbound(m)  # Meta retry: deduped via the unique constraint on (agent_id, external_id)
    chats = _chats(agent_id)
    assert len(chats) == 1 and chats[0][1] == "open"
    assert [t["role"] for t in chats[0][2]] == ["customer", "assistant"]
    assert chats[0][2][0]["wamid"] == "wamid.A"
    assert sent == [("+919811100002", "echo: Hi, price?")]
    assert not call_service.CallService(agent_id).has_active(chats[0][3])

    state["end"] = True
    [m2] = wa.parse_webhook(_payload(wamid="wamid.B", text="thanks bye", wa_id="919811100002"))
    wa.handle_inbound(m2)
    chats = _chats(agent_id)
    assert len(chats) == 1 and chats[0][1] == "closed" and len(chats[0][2]) == 4
    assert summaries == [chats[0][0]]


def test_reply_send_retries_then_succeeds(agent_id, fakes, monkeypatch):
    sent, _, _ = fakes
    monkeypatch.setattr(wa, "_SEND_RETRY_BACKOFF_SECONDS", [0, 0])
    calls = []
    real_send = wa._send

    def flaky_send(to, text, agent_id, lead_id, phone_number_id=None, conv_id=None):
        calls.append(1)
        if len(calls) < 3:
            return {"ok": False, "error": "transient"}
        return real_send(to, text, agent_id, lead_id, phone_number_id, conv_id=conv_id)

    monkeypatch.setattr(wa, "_send", flaky_send)
    [m] = wa.parse_webhook(_payload(wamid="wamid.RETRY1", wa_id="919811100010"))
    wa.handle_inbound(m)
    assert len(calls) == 3
    assert sent == [("+919811100010", "echo: Hi, price?")]
    chats = _chats(agent_id)
    turns = [c for c in chats if c[2] and c[2][0]["wamid"] == "wamid.RETRY1"][0][2]
    assert [t["role"] for t in turns] == ["customer", "assistant"]


def test_reply_send_persistent_failure_no_reply_row(agent_id, fakes, monkeypatch):
    sent, _, _ = fakes
    monkeypatch.setattr(wa, "_SEND_RETRY_BACKOFF_SECONDS", [0, 0])
    calls = []

    def always_fails(to, text, agent_id, lead_id, phone_number_id=None, conv_id=None):
        calls.append(1)
        return {"ok": False, "error": "down"}

    monkeypatch.setattr(wa, "_send", always_fails)
    [m] = wa.parse_webhook(_payload(wamid="wamid.RETRY2", wa_id="919811100012"))
    wa.handle_inbound(m)
    assert len(calls) == 3
    assert sent == []
    chats = _chats(agent_id)
    turns = [c for c in chats if c[2] and c[2][0]["wamid"] == "wamid.RETRY2"][0][2]
    assert [t["role"] for t in turns] == ["customer"]


def test_non_text_gets_fixed_reply(agent_id, fakes):
    sent, _, _ = fakes
    [m] = wa.parse_webhook(_payload(wamid="wamid.C", wa_id="919811100003", kind="audio"))
    wa.handle_inbound(m)
    assert sent and sent[0][1] == wa.NON_TEXT_REPLY


def test_close_idle_chats(agent_id, fakes, monkeypatch):
    _, summaries, _ = fakes
    [m] = wa.parse_webhook(_payload(wamid="wamid.D", wa_id="919811100004"))
    wa.handle_inbound(m)
    assert wa.close_idle_chats() == 0
    old = (datetime.now(timezone.utc) - timedelta(minutes=90)).replace(tzinfo=None)
    cid = next(c for c in _chats(agent_id) if c[2] and c[2][0].get("wamid") == "wamid.D")[0]
    with get_db() as db:
        db.get(Conversation, cid).last_message_at = old
    assert wa.close_idle_chats() == 1
    assert summaries == [cid] and next(c for c in _chats(agent_id) if c[0] == cid)[1] == "closed"


def _msg(**kw):
    base = {"wamid": "w", "from": "919811100010", "name": "Ravi", "type": "text", "text": "", "phone_number_id": "555",
            "display_phone_number": "918000000099", "media_id": None, "mime_type": None, "caption": "", "filename": ""}
    return {**base, **kw}


def test_audio_is_transcribed(agent_id, fakes, monkeypatch):
    sent, _, _ = fakes
    told = []
    monkeypatch.setattr(chat_agent, "respond", lambda aid, cid, h, t, lead, **kw: told.append(t) or {"reply": "ok", "end_call": False})
    monkeypatch.setattr(wa, "download_media", lambda mid, tok: (b"x", "audio/ogg"))
    monkeypatch.setattr(wa, "transcribe_audio", lambda d, m: "kitna price hai")
    wa.handle_inbound(_msg(wamid="a1", type="audio", media_id="m1", **{"from": "919811100011"}))
    assert told == ["kitna price hai"]
    chat = next(c for c in _chats(agent_id) if c[2][0].get("wamid") == "a1")
    assert chat[2][0]["text"].endswith("kitna price hai") and chat[2][0]["text"].startswith("\U0001F3A4")
    # failed transcription falls back to the fixed line
    monkeypatch.setattr(wa, "transcribe_audio", lambda d, m: "")
    wa.handle_inbound(_msg(wamid="a2", type="audio", media_id="m2", **{"from": "919811100012"}))
    assert sent[-1][1] == wa.NON_TEXT_REPLY


def test_image_with_and_without_caption_and_reaction(agent_id, fakes, monkeypatch):
    sent, _, _ = fakes
    told = []
    monkeypatch.setattr(chat_agent, "respond", lambda aid, cid, h, t, lead, **kw: told.append(t) or {"reply": "ok", "end_call": False})
    wa.handle_inbound(_msg(wamid="i1", type="image", media_id="m", **{"from": "919811100013"}))
    wa.handle_inbound(_msg(wamid="i2", type="image", media_id="m", caption="this one?", text="this one?", **{"from": "919811100014"}))
    assert told == ["[customer sent an image]", "this one?"]
    n = len(sent)
    wa.handle_inbound(_msg(wamid="r1", type="reaction", **{"from": "919811100013"}))
    assert len(sent) == n and len(told) == 2


def test_parse_media_fields():
    p = _payload()
    p["entry"][0]["changes"][0]["value"]["messages"][0] = {"from": "1", "id": "x", "type": "document",
        "document": {"id": "d1", "mime_type": "application/pdf", "caption": "invoice", "filename": "a.pdf"}}
    [m] = wa.parse_webhook(p)
    assert (m["media_id"], m["mime_type"], m["caption"], m["text"]) == ("d1", "application/pdf", "invoice", "invoice")


def test_resolve_agent_prefers_profile_id(agent_id, client, base, monkeypatch):
    assert client.put(f"{base}/profile", json={"whatsapp_phone_number_id": "PNID-77"}).status_code == 200
    monkeypatch.setattr(settings, "whatsapp_agent_id", 0)
    assert wa.resolve_agent("PNID-77", "000") == agent_id
    assert wa.resolve_agent("other", "000") is None
    client.put(f"{base}/profile", json={"whatsapp_phone_number_id": ""})


def test_human_reply_pauses_ai_and_resume(agent_id, fakes, client, base, monkeypatch):
    sent, _, _ = fakes
    wa.handle_inbound(_msg(wamid="h1", text="hello", **{"from": "919811100020"}))
    chat = next(c for c in _chats(agent_id) if c[2][0].get("wamid") == "h1")
    url = f"{base}/whatsapp/chats/{chat[0]}"
    assert client.post(url + "/reply", json={"text": "I will take over"}).status_code == 200
    assert sent[-1][1] == "I will take over"
    detail = client.get(url).json()
    assert detail["ai_paused"] and detail["transcript"][-1]["role"] == "human"
    assert any(i["id"] == chat[0] for i in client.get(f"{base}/whatsapp/chats").json()["items"])
    calls = []
    monkeypatch.setattr(chat_agent, "respond", lambda *a, **k: calls.append(1) or {"reply": "x", "end_call": False})
    wa.handle_inbound(_msg(wamid="h2", text="still there?", **{"from": "919811100020"}))
    assert not calls
    assert client.get(url).json()["transcript"][-1]["text"] == "still there?"
    assert client.post(url + "/resume").status_code == 200
    wa.handle_inbound(_msg(wamid="h3", text="ok thanks", **{"from": "919811100020"}))
    assert calls == [1]


def test_phone_number_id_skips_composio(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    urls = []

    class Res:
        status_code = 200
        text = ""
        def json(self): return {}

    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, **k):
            urls.append(url)
            return Res()

    monkeypatch.setattr(wa, "settings", SimpleNamespace(composio_api_key="ck", whatsapp_token="t", whatsapp_phone_number_id="dflt"))
    monkeypatch.setattr(wa.httpx, "AsyncClient", Client)
    out = asyncio.run(wa.send_whatsapp_message("+919811100001", "hi", phone_number_id="555"))
    assert out["provider"] == "meta" and "555/messages" in urls[0]
    out = asyncio.run(wa.send_whatsapp_message("+919811100001", "hi"))
    assert out["provider"] == "composio"
