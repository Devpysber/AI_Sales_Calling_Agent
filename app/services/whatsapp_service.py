"""
WhatsApp & Post-Call Messaging Dispatcher.
Supports:
1. Composio WhatsApp connector (for agents configured with Composio integration).
2. WhatsApp Cloud API / Direct WhatsApp Webhook fallback.
3. Post-call customer confirmations and internal hot-lead team alerts.
"""

import asyncio
import hashlib
import hmac
import logging
import re
import threading
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.database import get_db
from app.models.agent import Agent
from app.models.channel import Conversation, Message
from app.services import events

log = logging.getLogger(__name__)

def is_whatsapp_configured() -> bool:
    return bool(getattr(settings, "composio_api_key", None) or getattr(settings, "whatsapp_token", None))

def _profile_phone_id(agent_id: int | None) -> str:
    if not agent_id:
        return ""
    try:
        from app.services import agents
        return str((agents.get_profile(agent_id) or {}).get("whatsapp_phone_number_id") or "").strip()
    except Exception:
        return ""


def clean_phone(phone: str) -> str:
    """Normalize phone to E.164 without plus, e.g. 919876543210."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 10:
        return "91" + digits
    return digits

async def send_whatsapp_message(
    to: str,
    text: str,
    agent_id: int | None = None,
    lead_id: int | None = None,
    call_id: int | None = None,
    phone_number_id: str | None = None,
) -> dict:
    """
    Dispatch WhatsApp message via Composio API or Direct WhatsApp API.
    """
    recipient = clean_phone(to)
    if not recipient:
        return {"ok": False, "error": "Invalid phone number"}

    composio_key = "" if phone_number_id else getattr(settings, "composio_api_key", "")  # chat replies go out via Meta
    if composio_key:
        try:
            # Composio WhatsApp Tool Action Call
            url = "https://backend.composio.dev/api/v1/actions/WHATSAPP_SEND_MESSAGE/execute"
            headers = {
                "x-api-key": composio_key,
                "Content-Type": "application/json",
            }
            payload = {
                "entityId": f"agent_{agent_id}" if agent_id else "default",
                "input": {
                    "recipient": recipient,
                    "message": text,
                }
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(url, headers=headers, json=payload)
                if res.status_code in (200, 201):
                    events.record(
                        "whatsapp.sent",
                        f"WhatsApp dispatched to +{recipient}",
                        text[:120],
                        agent_id=agent_id,
                        lead_id=lead_id,
                        call_id=call_id,
                        actor="ai"
                    )
                    return {"ok": True, "provider": "composio", "data": res.json()}
                else:
                    log.warning("Composio WhatsApp response %s: %s", res.status_code, res.text)
                    return {"ok": False, "error": f"Composio HTTP {res.status_code}: {res.text}"}
        except Exception as e:
            log.exception("Composio dispatch error: %s", e)
            return {"ok": False, "error": str(e)}

    # Fallback to direct Meta/WhatsApp token if configured
    whatsapp_token = getattr(settings, "whatsapp_token", "")
    phone_number_id = phone_number_id or _profile_phone_id(agent_id) or getattr(settings, "whatsapp_phone_number_id", "")
    if whatsapp_token and phone_number_id:
        try:
            url = f"https://graph.facebook.com/v19.0/{phone_number_id}/messages"
            headers = {
                "Authorization": f"Bearer {whatsapp_token}",
                "Content-Type": "application/json",
            }
            payload = {
                "messaging_product": "whatsapp",
                "to": recipient,
                "type": "text",
                "text": {"body": text},
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(url, headers=headers, json=payload)
                if res.status_code in (200, 201):
                    events.record(
                        "whatsapp.sent",
                        f"WhatsApp dispatched to +{recipient}",
                        text[:120],
                        agent_id=agent_id,
                        lead_id=lead_id,
                        call_id=call_id,
                        actor="ai"
                    )
                    return {"ok": True, "provider": "meta", "data": res.json()}
                return {"ok": False, "error": f"Meta HTTP {res.status_code}: {res.text}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # Not configured, log event for traceability
    events.record(
        "whatsapp.queued",
        f"WhatsApp ready for +{recipient} (Awaiting Composio/WhatsApp key)",
        text[:120],
        agent_id=agent_id,
        lead_id=lead_id,
        call_id=call_id,
        actor="system"
    )
    return {"ok": False, "error": "WhatsApp credentials not configured"}


# ---------------- Inbound chat channel (Meta WhatsApp Cloud API) ----------------

CHAT_GUIDANCE = (
    "This is a WhatsApp text chat, not a phone call. Write each reply as a short chat message (1-3 sentences). "
    "Links, numbers and addresses may be written out in full. Never use phone-call phrasing such as offering to "
    "send something on WhatsApp or to call later on this number: you are already on WhatsApp with the customer. "
    "Set end_call to true only when the conversation is clearly finished."
)
NON_TEXT_REPLY = ("Sorry, I can only read text messages here. Please type your message. / "
                  "Maaf kijiye, main yahan sirf text messages padh sakta hoon. Kripya type karke bhejiye.")

MEDIA_TYPES = ("audio", "voice", "image", "document", "video")
MAX_MEDIA_BYTES = 16 * 1024 * 1024
_warned_secret = False
_locks: dict[tuple, threading.RLock] = {}
_locks_guard = threading.Lock()


def _lock_for(agent_id: int, wa_id: str) -> threading.RLock:
    with _locks_guard:
        return _locks.setdefault((agent_id, wa_id), threading.RLock())


def verify_signature(body: bytes, header: str | None) -> bool:
    global _warned_secret
    secret = settings.whatsapp_app_secret
    if not secret:
        if not _warned_secret:
            _warned_secret = True
            log.warning("WHATSAPP_APP_SECRET not set: webhook signatures are NOT being verified")
        return True
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256="):])


def parse_webhook(payload: dict) -> list[dict]:
    out = []
    for entry in (payload or {}).get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            meta = value.get("metadata") or {}
            names = {c.get("wa_id"): (c.get("profile") or {}).get("name") for c in value.get("contacts") or []}
            for m in value.get("messages") or []:
                kind = m.get("type") or ""
                if kind == "text":
                    text = (m.get("text") or {}).get("body") or ""
                elif kind == "button":
                    text = (m.get("button") or {}).get("text") or ""
                elif kind == "interactive":
                    inter = m.get("interactive") or {}
                    reply = inter.get("button_reply") or inter.get("list_reply") or {}
                    text = reply.get("title") or ""
                elif kind == "location":
                    loc = m.get("location") or {}
                    text = f"[location: {loc.get('latitude')},{loc.get('longitude')} {loc.get('name') or ''}]".replace(" ]", "]")
                else:
                    text = ""
                media = m.get(kind) if kind in MEDIA_TYPES and isinstance(m.get(kind), dict) else {}
                if media.get("caption"):
                    text = media["caption"]
                out.append({"media_id": media.get("id"), "mime_type": media.get("mime_type"),
                            "caption": media.get("caption") or "", "filename": media.get("filename") or "",
                            "wamid": m.get("id"), "from": m.get("from"), "name": names.get(m.get("from")) or "",
                            "type": kind, "text": text.strip(), "phone_number_id": meta.get("phone_number_id"),
                            "display_phone_number": meta.get("display_phone_number")})
    return out


def resolve_agent(phone_number_id: str | None, display_phone_number: str | None) -> int | None:
    digits = re.sub(r"\D", "", display_phone_number or "")
    if phone_number_id:
        from app.services import agents
        for aid in agents.ids():
            if str((agents.get_profile(aid) or {}).get("whatsapp_phone_number_id") or "").strip() == str(phone_number_id):
                return aid
    if digits:
        with get_db() as db:
            for aid, num in db.execute(select(Agent.id, Agent.phone_number).where(Agent.phone_number.is_not(None))):
                d = re.sub(r"\D", "", num or "")
                if d and (d == digits or d[-10:] == digits[-10:]):
                    return aid
    return settings.whatsapp_agent_id or None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _open_chat(agent_id: int, lead_id: int) -> int:
    """An open WhatsApp Conversation for this lead, creating one when none is open."""
    with get_db() as db:
        conv = db.scalars(select(Conversation).where(Conversation.agent_id == agent_id, Conversation.lead_id == lead_id,
                          Conversation.channel == "whatsapp", Conversation.status == "open")
                          .order_by(Conversation.id.desc())).first()
        if conv:
            return conv.id
        conv = Conversation(agent_id=agent_id, lead_id=lead_id, channel="whatsapp", status="open", ai_paused=False)
        db.add(conv)
        db.flush()
        return conv.id


def _history(conv_id: int) -> list[dict]:
    """[{role, text}] the agent expects: customer turns stay "customer", AI/human replies are "assistant"."""
    with get_db() as db:
        msgs = db.scalars(select(Message).where(Message.conversation_id == conv_id).order_by(Message.id)).all()
        return [{"role": "customer" if m.sender_type == "customer" else "assistant", "text": m.text or ""} for m in msgs]


def _add_message(conv_id: int, agent_id: int, direction: str, sender_type: str, text: str, *, type_: str = "text",
                  external_id: str | None = None, sender_name: str | None = None) -> bool:
    """Record one turn. Returns False (no-op) when external_id is a duplicate (DB unique constraint)."""
    now = _now().replace(tzinfo=None)
    with get_db() as db:
        msg = Message(conversation_id=conv_id, agent_id=agent_id, direction=direction, sender_type=sender_type,
                      sender_name=sender_name, type=type_, text=text, external_id=external_id,
                      status="received" if direction == "in" else "sent")
        db.add(msg)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            return False
        conv = db.get(Conversation, conv_id)
        if conv is not None:
            conv.last_message_at = now
            if direction == "in":
                conv.last_customer_at = now
    return True


def _send(to: str, text: str, agent_id: int, lead_id: int, phone_number_id: str | None = None) -> dict:
    # Chat replies always leave through Meta, from the number the customer messaged.
    pid = phone_number_id or _profile_phone_id(agent_id) or settings.whatsapp_phone_number_id or None
    return asyncio.run(send_whatsapp_message(to, text, agent_id=agent_id, lead_id=lead_id, phone_number_id=pid))


def download_media(media_id: str, token: str) -> tuple[bytes, str]:
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=10.0) as client:
        meta = client.get(f"https://graph.facebook.com/v19.0/{media_id}", headers=headers)
        meta.raise_for_status()
        info = meta.json()
        if int(info.get("file_size") or 0) > MAX_MEDIA_BYTES:
            raise ValueError("media too large")
        res = client.get(info["url"], headers=headers)
        res.raise_for_status()
        if len(res.content) > MAX_MEDIA_BYTES:
            raise ValueError("media too large")
        return res.content, info.get("mime_type") or res.headers.get("content-type", "")


def transcribe_audio(data: bytes, mime: str) -> str:
    mime = (mime or "audio/ogg").split(";")[0].strip()
    ext = mime.split("/")[-1] or "ogg"
    res = httpx.post("https://api.sarvam.ai/speech-to-text", headers={"api-subscription-key": settings.sarvam_api_key},
                     files={"file": (f"voice.{ext}", data, mime)},
                     data={"model": settings.sarvam_stt_model, "language_code": "unknown"}, timeout=30.0)
    res.raise_for_status()
    return str(res.json().get("transcript") or "").strip()


def handle_inbound(msg: dict) -> None:
    """Process one inbound message (blocking; run in a worker thread)."""
    wa_id = msg.get("from")
    if not wa_id:
        return
    agent_id = resolve_agent(msg.get("phone_number_id"), msg.get("display_phone_number"))
    if not agent_id:
        log.warning("WhatsApp message from %s: no agent resolved (set WHATSAPP_AGENT_ID)", wa_id)
        return
    with _lock_for(agent_id, wa_id):
        try:
            _handle_locked(msg, agent_id, msg.get("wamid"), wa_id)
        except Exception:
            log.exception("WhatsApp inbound handling failed for %s", wa_id)


def _lead_phone(lead_id: int | None) -> str:
    if not lead_id:
        return ""
    from app.models.lead import Lead
    with get_db() as db:
        return db.scalar(select(Lead.phone).where(Lead.id == lead_id)) or ""


def _handle_locked(msg: dict, agent_id: int, wamid: str | None, wa_id: str) -> None:
    from app.services import agent as agent_svc
    from app.services.crm_service import CRMService

    if wamid:
        # Persisted dedupe (the unique constraint on agent_id+external_id is the real guard; this just
        # skips the media download / LLM round trip Meta's own retries would otherwise pay for twice).
        with get_db() as db:
            if db.scalar(select(Message.id).where(Message.agent_id == agent_id, Message.external_id == wamid)) is not None:
                return
    phone = "+" + wa_id
    crm = CRMService(agent_id)
    lead = crm.find_by_phone(phone) or crm.create(
        {"phone": phone, "name": msg.get("name") or "WhatsApp contact", "source": "WhatsApp"}, actor="system")
    lead_id = lead["id"]
    conv_id = _open_chat(agent_id, lead_id)
    with get_db() as db:
        paused = bool(db.scalar(select(Conversation.ai_paused).where(Conversation.id == conv_id)))
    kind = msg.get("type") or ""
    if kind == "reaction":
        return
    text = (msg.get("text") or "").strip()
    shown, say = text, text  # what the transcript shows / what the agent is told
    if not text and kind in ("audio", "voice") and msg.get("media_id"):
        try:
            data, mime = download_media(msg["media_id"], settings.whatsapp_token)
            text = transcribe_audio(data, mime)
        except Exception:
            log.exception("WhatsApp voice note from %s could not be transcribed", phone)
        shown = say = f"🎤 {text}" if text else ""
    elif not text and kind in ("image", "document", "video"):
        say = f"[customer sent {'an' if kind == 'image' else 'a'} {kind}{': ' + msg['filename'] if msg.get('filename') else ''}]"
        shown = say
    media_type = kind if kind in MEDIA_TYPES else "text"
    added = _add_message(conv_id, agent_id, "in", "customer", shown or f"[{kind or 'message'}]",
                          type_=media_type, external_id=wamid, sender_name=msg.get("name") or None)
    if not added:
        return  # a duplicate wamid raced us past the precheck above
    events.record("whatsapp.received", f"WhatsApp from {phone}", (shown or kind)[:120],
                  agent_id=agent_id, lead_id=lead_id, actor="customer")
    if lead.get("do_not_call") or paused:
        return
    end_call = False
    if not say:
        reply = NON_TEXT_REPLY
    else:
        history = _history(conv_id)[:-1]  # everything before the turn just added
        try:
            res = agent_svc.respond(agent_id, history, say.removeprefix("🎤 "), lead, use_embeddings=True, guidance=CHAT_GUIDANCE)
        except Exception:
            log.exception("WhatsApp reply generation failed for %s", phone)
            return
        reply, end_call = (res.get("reply") or "").strip(), bool(res.get("end_call"))
        if not reply:
            return
    out = _send(phone, reply, agent_id, lead_id, msg.get("phone_number_id"))
    if not out.get("ok"):
        log.warning("WhatsApp reply to %s not sent: %s", phone, out.get("error"))
    _add_message(conv_id, agent_id, "out", "ai", reply)
    if end_call:
        close_chat(conv_id)


def _apply_chat_outcome(conv_id: int, agent_id: int, lead_id: int | None, history: list[dict]) -> None:
    from app.services.call_service import CallService
    s = CallService(agent_id).apply_outcome(None, lead_id, history)
    if s is not None:
        with get_db() as db:
            conv = db.get(Conversation, conv_id)
            if conv is not None:
                conv.summary = s.get("summary") or "(no summary text)"


def _submit_chat_summary(conv_id: int, agent_id: int, lead_id: int | None, history: list[dict]) -> None:
    from app.services.call_service import _turn_pool

    def _done(f):
        if f.exception() is not None:
            log.error("Post-chat summary for conversation %s crashed: %r", conv_id, f.exception())
    _turn_pool.submit(_apply_chat_outcome, conv_id, agent_id, lead_id, history).add_done_callback(_done)


def close_chat(conv_id: int) -> bool:
    with get_db() as db:
        conv = db.get(Conversation, conv_id)
        if conv is None or conv.status != "open":
            return False
        agent_id, lead_id = conv.agent_id, conv.lead_id
    wa_id = re.sub(r"\D", "", _lead_phone(lead_id))
    with _lock_for(agent_id or 0, wa_id):
        with get_db() as db:
            conv = db.get(Conversation, conv_id)
            if conv is None or conv.status != "open":
                return False
            lead_id = conv.lead_id
            has_customer = db.scalar(select(Message.id).where(Message.conversation_id == conv_id,
                                      Message.sender_type == "customer")) is not None
            conv.status = "closed"
            conv.closed_at = _now().replace(tzinfo=None)
        if has_customer:
            _submit_chat_summary(conv_id, agent_id, lead_id, _history(conv_id))
    return True


def close_idle_chats() -> int:
    cutoff = _now() - timedelta(minutes=settings.whatsapp_idle_minutes)
    with get_db() as db:
        rows = [(c.id, c.last_message_at, c.created_at)
                for c in db.scalars(select(Conversation).where(Conversation.channel == "whatsapp", Conversation.status == "open"))]
    closed = 0
    for conv_id, last_message_at, created in rows:
        last = last_message_at or created
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if last is not None and last < cutoff and close_chat(conv_id):
            closed += 1
    return closed


def human_reply(conv_id: int, text: str, by: str) -> dict:
    """A teammate answers from the inbox: send via Meta, record the turn, pause the AI for this chat."""
    with get_db() as db:
        conv = db.get(Conversation, conv_id)
        if conv is None or conv.channel != "whatsapp":
            raise LookupError("Chat not found.")
        agent_id, lead_id, status = conv.agent_id, conv.lead_id, conv.status
    if status != "open":
        raise ValueError("This chat is closed.")
    phone = _lead_phone(lead_id)
    with _lock_for(agent_id or 0, re.sub(r"\D", "", phone or "")):
        out = _send(phone, text, agent_id, lead_id)
        if not out.get("ok"):
            raise ValueError(out.get("error") or "WhatsApp send failed")
        _add_message(conv_id, agent_id, "out", "human", text, sender_name=by)
        with get_db() as db:
            conv = db.get(Conversation, conv_id)
            conv.ai_paused = True
    return {"ok": True}


def set_ai_paused(conv_id: int, paused: bool) -> None:
    with get_db() as db:
        conv = db.get(Conversation, conv_id)
        if conv is None or conv.channel != "whatsapp":
            raise LookupError("Chat not found.")
        conv.ai_paused = paused
