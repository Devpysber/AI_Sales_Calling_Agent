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
import json
import logging
import os
import re
import socket
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core import store
from app.core.config import settings
from app.core.database import get_db
from app.models.agent import Agent
from app.models.channel import Conversation, Message
from app.services import events

log = logging.getLogger(__name__)

# ---------------- Durable inbound queue (Redis list, consumed by queue_worker_loop) ----------------
# Design: one global Redis LIST (not a queue-per-contact) + the existing per-(agent,wa_id) Redis lock
# (store.acquire_lock) enforces ordering at processing time. Simpler than per-contact queues and reuses
# infra that already exists (acquire_lock); a contact whose lock is briefly held just gets re-enqueued.
# Delivery is at-least-once (plain BLPOP, no RPOPLPUSH/ack list): if a worker crashes after BLPOP but
# before finishing, that one message is lost from the queue, but Day 1's unique (agent_id, external_id)
# constraint plus Meta's own webhook retries are the backstop — simplest option that still can't double-send
# under normal operation (crash mid-send would just mean Meta retries the delivery itself).
QUEUE_KEY = "va:whatsapp:inbound"
BURST_WINDOW_SECONDS = 5  # merge customer messages arriving within this window into one agent turn
BURST_POLL_SECONDS = 1
LOCK_TTL_SECONDS = 30
LOCK_ACQUIRE_ATTEMPTS = 3
LOCK_ACQUIRE_WAIT = 0.3
OWNER = f"{socket.gethostname()}:{os.getpid()}"


def _queue_enabled() -> bool:
    """True when the shared store is real Redis (see app.core.store._make_store) -- reusing that same
    fallback condition rather than inventing new config: no REDIS_URL means single-process dev, where
    inline synchronous processing is both sufficient and simpler."""
    return isinstance(store.store, store.RedisStore)


def enqueue_inbound(msg: dict) -> None:
    """Webhook entry point: durable+ordered via Redis when configured, inline otherwise (dev fallback)."""
    if _queue_enabled():
        store.store.client.rpush(QUEUE_KEY, json.dumps(msg))
    else:
        handle_inbound(msg)


def _lock_name(agent_id: int, wa_id: str) -> str:
    return f"whatsapp:{agent_id}:{wa_id}"


def _acquire_with_retry(name: str) -> bool:
    for _ in range(LOCK_ACQUIRE_ATTEMPTS):
        if store.acquire_lock(name, OWNER, LOCK_TTL_SECONDS):
            return True
        time.sleep(LOCK_ACQUIRE_WAIT)
    return False


def _requeue_front(msg: dict) -> None:
    """Put a message back at the head (not the tail) of the queue: it was already the next one due for
    this contact, so losing a lock race must not let it be overtaken by a later, unrelated message."""
    store.store.client.lpush(QUEUE_KEY, json.dumps(msg))


def _contact_key(msg: dict) -> tuple:
    return (msg.get("phone_number_id"), msg.get("from"))


def _drain_same_contact(key: tuple, max_items: int = 20) -> list[dict]:
    """Non-blocking: pop whatever is already queued, keep same-contact messages for the batch, and put
    everything else straight back at the head of the queue so other contacts' ordering is undisturbed."""
    client = store.store.client
    batch, others = [], []
    for _ in range(max_items):
        raw = client.lpop(QUEUE_KEY)
        if raw is None:
            break
        other = json.loads(raw)
        if _contact_key(other) == key:
            batch.append(other)
        else:
            others.append(raw)
    if others:
        client.lpush(QUEUE_KEY, *reversed(others))
    return batch


def queue_worker_loop(stop_event: threading.Event | None = None) -> None:
    """Consumer loop for `python -m app.worker` / `app.chat_worker`: BLPOP, lock the contact, process
    (merging a short burst of messages into one agent turn), release."""
    client = store.store.client
    while stop_event is None or not stop_event.is_set():
        item = client.blpop(QUEUE_KEY, timeout=5)
        if item is None:
            continue
        _, raw = item
        try:
            msg = json.loads(raw)
        except Exception:
            log.exception("WhatsApp queue: unreadable message, dropping")
            continue
        try:
            _process_from_queue(msg)
        except Exception:
            log.exception("WhatsApp queue: failed to process message")


def _process_from_queue(msg: dict) -> None:
    wa_id = msg.get("from")
    if not wa_id:
        return
    agent_id = resolve_agent(msg.get("phone_number_id"), msg.get("display_phone_number"))
    if not agent_id:
        log.warning("WhatsApp message from %s: no agent resolved (set WHATSAPP_AGENT_ID)", wa_id)
        return
    name = _lock_name(agent_id, wa_id)
    if not _acquire_with_retry(name):
        # Someone else is mid-turn for this contact; don't block the whole worker on it, requeue at the
        # front for another pass (ours or another worker's) rather than let a later message cut ahead.
        _requeue_front(msg)
        return
    try:
        key = _contact_key(msg)
        batch = [msg] + _drain_same_contact(key)
        client = store.store.client
        item = client.blpop(QUEUE_KEY, timeout=BURST_POLL_SECONDS)
        if item is not None:
            _, raw = item
            extra = json.loads(raw)
            if _contact_key(extra) == key:
                batch.append(extra)
                batch.extend(_drain_same_contact(key))
            else:
                client.lpush(QUEUE_KEY, raw)
        try:
            _handle_batch(batch, agent_id, wa_id)
        except Exception:
            log.exception("WhatsApp inbound handling failed for %s", wa_id)
    finally:
        store.delete("lock:" + name)

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


def _profile_waba_id(agent_id: int | None) -> str:
    if not agent_id:
        return ""
    try:
        from app.services import agents
        return str((agents.get_profile(agent_id) or {}).get("whatsapp_business_account_id") or "").strip()
    except Exception:
        return ""


def _profile_whatsapp_token(agent_id: int | None) -> str:
    """Per-agent Embedded Signup business token (decrypted), or "" if this agent has none set --
    callers fall back to the global settings.whatsapp_token, same pattern as _profile_phone_id."""
    if not agent_id:
        return ""
    try:
        from app.services import agents
        return agents.get_agent_whatsapp_token(agent_id) or ""
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
    whatsapp_token = _profile_whatsapp_token(agent_id) or getattr(settings, "whatsapp_token", "")
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
                flow_data = None
                if kind == "text":
                    text = (m.get("text") or {}).get("body") or ""
                elif kind == "button":
                    text = (m.get("button") or {}).get("text") or ""
                elif kind == "interactive":
                    inter = m.get("interactive") or {}
                    if inter.get("type") == "nfm_reply":
                        # WhatsApp Flow completion: interactive.nfm_reply.response_json is a JSON
                        # *string* of the submitted field values -- surface it structured, not as text.
                        nfm = inter.get("nfm_reply") or {}
                        try:
                            flow_data = json.loads(nfm.get("response_json") or "{}")
                        except (ValueError, TypeError):
                            flow_data = {}
                        text = ""
                    else:
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
                item = {"media_id": media.get("id"), "mime_type": media.get("mime_type"),
                        "caption": media.get("caption") or "", "filename": media.get("filename") or "",
                        "wamid": m.get("id"), "from": m.get("from"), "name": names.get(m.get("from")) or "",
                        "type": "flow_response" if flow_data is not None else kind, "text": text.strip(),
                        "phone_number_id": meta.get("phone_number_id"),
                        "display_phone_number": meta.get("display_phone_number")}
                if flow_data is not None:
                    item["flow_data"] = flow_data
                out.append(item)
    return out


def parse_status_updates(payload: dict) -> list[dict]:
    """Meta delivery/read receipts for messages THIS business sent: value.statuses[] (separate from
    value.messages[], which parse_webhook handles). {"wamid", "status", "error"} per entry."""
    out = []
    for entry in (payload or {}).get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            for s in value.get("statuses") or []:
                errors = s.get("errors") or []
                error = "; ".join(f"{e.get('code')}: {e.get('title')}" for e in errors) if errors else None
                out.append({"wamid": s.get("id"), "status": s.get("status") or "", "error": error})
    return out


# Forward-only progression: a late "sent"/"delivered" arriving after "read" was already recorded must
# never regress it. "failed" is handled separately below (always applied, any time).
_STATUS_RANK = {"received": 0, "queued": 0, "sent": 1, "delivered": 2, "read": 3}


def apply_status_update(wamid: str | None, status: str, error: str | None = None) -> bool:
    """Update the Message row (matched by external_id == wamid, the wamid of our own outbound send)
    with a delivery/read/failed status from a Meta statuses[] webhook entry. Status only moves forward
    (sent -> delivered -> read); "failed" always applies, since it can arrive at any point and is
    terminal. Returns False when no matching Message row was found (e.g. wamid not yet persisted, or
    a status for a message outside what we track)."""
    if not wamid or not status:
        return False
    with get_db() as db:
        msg = db.scalars(select(Message).where(Message.external_id == wamid)).first()
        if msg is None:
            log.warning("WhatsApp status update for unknown wamid %s: %s", wamid, status)
            return False
        if status == "failed":
            msg.status = "failed"
            msg.error = error
            if error:
                log.warning("WhatsApp message %s failed: %s", wamid, error)
            return True
        if status not in _STATUS_RANK:
            return False
        if _STATUS_RANK[status] > _STATUS_RANK.get(msg.status, 0):
            msg.status = status
        return True


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


FREE_FORM_WINDOW = timedelta(hours=24)


def is_within_free_form_window(conversation: Conversation) -> bool:
    """Meta's 24-hour customer-care window: free-form text is only allowed within 24h of the customer's
    own last message. No last_customer_at at all means the customer has never messaged us (an
    outbound-initiated conversation) -- that's always outside the window, not inside it."""
    last = getattr(conversation, "last_customer_at", None)
    if last is None:
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return _now() - last < FREE_FORM_WINDOW


class OutsideWindowError(ValueError):
    """Raised when a plain-text WhatsApp send is attempted outside the 24-hour free-form window."""


def _send(to: str, text: str, agent_id: int, lead_id: int, phone_number_id: str | None = None,
          conv_id: int | None = None) -> dict:
    # Chat replies always leave through Meta, from the number the customer messaged.
    if conv_id is not None:
        with get_db() as db:
            conv = db.get(Conversation, conv_id)
            if conv is not None and not is_within_free_form_window(conv):
                raise OutsideWindowError(
                    "This conversation is outside the 24-hour window — send an approved template instead.")
    pid = phone_number_id or _profile_phone_id(agent_id) or settings.whatsapp_phone_number_id or None
    return asyncio.run(send_whatsapp_message(to, text, agent_id=agent_id, lead_id=lead_id, phone_number_id=pid))


async def send_whatsapp_template(
    to: str,
    template_name: str,
    language: str,
    variables: list[str] | None = None,
    agent_id: int | None = None,
    lead_id: int | None = None,
    phone_number_id: str | None = None,
    components: list[dict] | None = None,
) -> dict:
    """Send a pre-approved Meta WhatsApp template message. Exempt from the 24-hour free-form window
    check -- that's the whole point of templates. Reuses the same httpx/token/phone-id plumbing as
    send_whatsapp_message's direct-Meta path (no Composio equivalent for templates).

    `components` is the full Meta `template.components` parameters array (header/body/button
    parameters), for templates with a real structured shape. When omitted, falls back to the
    legacy behaviour of building a single flat BODY component from `variables`."""
    recipient = clean_phone(to)
    if not recipient:
        return {"ok": False, "error": "Invalid phone number"}
    whatsapp_token = _profile_whatsapp_token(agent_id) or getattr(settings, "whatsapp_token", "")
    phone_number_id = phone_number_id or _profile_phone_id(agent_id) or getattr(settings, "whatsapp_phone_number_id", "")
    if not (whatsapp_token and phone_number_id):
        return {"ok": False, "error": "WhatsApp credentials not configured"}
    if components is None:
        components = []
        if variables:
            components.append({"type": "body", "parameters": [{"type": "text", "text": str(v)} for v in variables]})
    payload = {
        "messaging_product": "whatsapp",
        "to": recipient,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language},
            **({"components": components} if components else {}),
        },
    }
    try:
        url = f"https://graph.facebook.com/v19.0/{phone_number_id}/messages"
        headers = {"Authorization": f"Bearer {whatsapp_token}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(url, headers=headers, json=payload)
            if res.status_code in (200, 201):
                events.record(
                    "whatsapp.sent",
                    f"WhatsApp template '{template_name}' dispatched to +{recipient}",
                    "",
                    agent_id=agent_id,
                    lead_id=lead_id,
                    actor="ai",
                )
                return {"ok": True, "provider": "meta", "data": res.json()}
            return {"ok": False, "error": f"Meta HTTP {res.status_code}: {res.text}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


async def create_meta_template(
    name: str,
    language: str,
    category: str,
    components: list[dict],
    agent_id: int | None = None,
    business_account_id: str | None = None,
) -> dict:
    """Submit a template for Meta review: POST /{whatsapp_business_account_id}/message_templates.
    Returns {"ok": False, "error": "..."} when the WABA id or token aren't configured, so callers
    can surface a clear 400 rather than a confusing failure deeper in the call."""
    waba_id = business_account_id or _profile_waba_id(agent_id) or getattr(settings, "whatsapp_business_account_id", "")
    whatsapp_token = getattr(settings, "whatsapp_token", "")
    if not waba_id:
        return {"ok": False, "error": "WhatsApp Business Account ID not configured for this agent"}
    if not whatsapp_token:
        return {"ok": False, "error": "WhatsApp credentials not configured"}
    payload = {"name": name, "language": language, "category": category, "components": components}
    try:
        url = f"https://graph.facebook.com/v19.0/{waba_id}/message_templates"
        headers = {"Authorization": f"Bearer {whatsapp_token}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(url, headers=headers, json=payload)
            if res.status_code in (200, 201):
                return {"ok": True, "data": res.json()}
            return {"ok": False, "error": f"Meta HTTP {res.status_code}: {res.text}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


async def fetch_meta_template(whatsapp_template_id: str, agent_id: int | None = None) -> dict:
    """Refresh a template's review status: GET /{whatsapp_template_id} returns category/status/
    rejected_reason once Meta has reviewed it. Explicit "sync status" action, never polled."""
    whatsapp_token = getattr(settings, "whatsapp_token", "")
    if not whatsapp_token:
        return {"ok": False, "error": "WhatsApp credentials not configured"}
    try:
        url = f"https://graph.facebook.com/v19.0/{whatsapp_template_id}"
        headers = {"Authorization": f"Bearer {whatsapp_token}"}
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(url, headers=headers)
            if res.status_code == 200:
                return {"ok": True, "data": res.json()}
            return {"ok": False, "error": f"Meta HTTP {res.status_code}: {res.text}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


async def upload_template_header_media(file_bytes: bytes, mime_type: str, agent_id: int | None = None) -> dict:
    """Meta's Resumable Upload API: returns a one-time-use media handle for a template's
    HEADER example.header_handle (required by Meta for any non-TEXT header format)."""
    app_id = getattr(settings, "whatsapp_app_id", "")
    whatsapp_token = _profile_whatsapp_token(agent_id) or getattr(settings, "whatsapp_token", "")
    if not app_id:
        return {"ok": False, "error": "Meta App ID not configured (WHATSAPP_APP_ID)"}
    if not whatsapp_token:
        return {"ok": False, "error": "WhatsApp credentials not configured"}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            start = await client.post(
                f"https://graph.facebook.com/v19.0/{app_id}/uploads",
                params={"file_length": len(file_bytes), "file_type": mime_type, "access_token": whatsapp_token},
            )
            if start.status_code not in (200, 201):
                return {"ok": False, "error": f"Meta upload session HTTP {start.status_code}: {start.text}"}
            session_id = start.json().get("id")
            if not session_id:
                return {"ok": False, "error": "Meta did not return an upload session id"}
            res = await client.post(
                f"https://graph.facebook.com/v19.0/{session_id}",
                headers={"Authorization": f"OAuth {whatsapp_token}", "file_offset": "0"},
                content=file_bytes,
            )
            if res.status_code not in (200, 201):
                return {"ok": False, "error": f"Meta upload HTTP {res.status_code}: {res.text}"}
            handle = res.json().get("h")
            if not handle:
                return {"ok": False, "error": "Meta did not return a media handle"}
            return {"ok": True, "handle": handle}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


async def complete_embedded_signup(code: str, phone_number_id: str, waba_id: str) -> dict:
    """Meta's WhatsApp Embedded Signup, step 2 of 2: the frontend's FB.login() already returned
    phone_number_id/waba_id (from the popup's own postMessage event, not from any API call) -- this
    exchanges the short-lived authorization `code` for a business access token, then subscribes
    this app to that WABA's webhooks so inbound messages/status updates actually start arriving.
    Real docs: https://developers.facebook.com/docs/whatsapp/embedded-signup"""
    app_id = getattr(settings, "whatsapp_app_id", "")
    app_secret = getattr(settings, "whatsapp_app_secret", "")
    if not app_id or not app_secret:
        return {"ok": False, "error": "Meta App ID / App Secret not configured"}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            token_res = await client.get(
                "https://graph.facebook.com/v19.0/oauth/access_token",
                params={"client_id": app_id, "client_secret": app_secret, "code": code},
            )
            if token_res.status_code != 200:
                return {"ok": False, "error": f"Meta token exchange HTTP {token_res.status_code}: {token_res.text}"}
            access_token = token_res.json().get("access_token")
            if not access_token:
                return {"ok": False, "error": "Meta did not return an access token"}

            # Subscribe this app to the new WABA's webhooks (required for inbound messages/status
            # updates to reach /api/whatsapp/webhook at all).
            sub_res = await client.post(
                f"https://graph.facebook.com/v19.0/{waba_id}/subscribed_apps",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if sub_res.status_code != 200:
                return {"ok": False, "error": f"Meta subscribe HTTP {sub_res.status_code}: {sub_res.text}"}

            return {"ok": True, "phone_number_id": phone_number_id, "waba_id": waba_id, "access_token": access_token}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


# ---------------- WhatsApp Flows (native multi-screen forms, navigate-only) ----------------

async def create_meta_flow(agent_id: int, name: str, category: str = "LEAD_GENERATION",
                           business_account_id: str | None = None) -> dict:
    """Create a Flow shell on Meta: POST /{waba_id}/flows -> {"id": "<flow_id>"}. The Flow JSON itself
    is uploaded separately (upload_flow_json) once this id exists."""
    waba_id = business_account_id or _profile_waba_id(agent_id) or getattr(settings, "whatsapp_business_account_id", "")
    whatsapp_token = _profile_whatsapp_token(agent_id) or getattr(settings, "whatsapp_token", "")
    if not waba_id:
        return {"ok": False, "error": "WhatsApp Business Account ID not configured for this agent"}
    if not whatsapp_token:
        return {"ok": False, "error": "WhatsApp credentials not configured"}
    try:
        url = f"https://graph.facebook.com/v19.0/{waba_id}/flows"
        headers = {"Authorization": f"Bearer {whatsapp_token}", "Content-Type": "application/json"}
        payload = {"name": name, "categories": [category]}
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(url, headers=headers, json=payload)
            if res.status_code in (200, 201):
                return {"ok": True, "data": res.json()}
            return {"ok": False, "error": f"Meta HTTP {res.status_code}: {res.text}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


async def upload_flow_json(agent_id: int, meta_flow_id: str, screens: list[dict], version: str = "3.1") -> dict:
    """Upload the Flow's JSON definition: POST /{flow_id}/assets (multipart, asset_type=FLOW_JSON,
    file=<the flow JSON as a file part>). Verified against Meta's official Flows API reference
    (developers.facebook.com/docs/whatsapp/flows/reference/flowsapi): field `file` (application/json,
    max 10MB), field `name` must be exactly "flow.json", field `asset_type` must be exactly "FLOW_JSON" --
    field names, required values, and content-type all match our implementation."""
    whatsapp_token = _profile_whatsapp_token(agent_id) or getattr(settings, "whatsapp_token", "")
    if not whatsapp_token:
        return {"ok": False, "error": "WhatsApp credentials not configured"}
    flow_json = json.dumps({"version": version, "screens": screens})
    try:
        url = f"https://graph.facebook.com/v19.0/{meta_flow_id}/assets"
        headers = {"Authorization": f"Bearer {whatsapp_token}"}
        files = {"file": ("flow.json", flow_json, "application/json")}
        data = {"name": "flow.json", "asset_type": "FLOW_JSON"}
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(url, headers=headers, data=data, files=files)
            if res.status_code in (200, 201):
                body = res.json()
                validation_errors = body.get("validation_errors")
                if isinstance(validation_errors, list) and validation_errors:
                    return {"ok": False, "error": "Meta reported validation errors",
                            "validation_errors": validation_errors}
                return {"ok": True, "data": body}
            return {"ok": False, "error": f"Meta HTTP {res.status_code}: {res.text}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


async def publish_meta_flow(agent_id: int, meta_flow_id: str) -> dict:
    """Publish the Flow: POST /{flow_id}/publish. A Flow must be published before it can be sent to a
    customer."""
    whatsapp_token = _profile_whatsapp_token(agent_id) or getattr(settings, "whatsapp_token", "")
    if not whatsapp_token:
        return {"ok": False, "error": "WhatsApp credentials not configured"}
    try:
        url = f"https://graph.facebook.com/v19.0/{meta_flow_id}/publish"
        headers = {"Authorization": f"Bearer {whatsapp_token}"}
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(url, headers=headers)
            if res.status_code in (200, 201):
                return {"ok": True, "data": res.json()}
            return {"ok": False, "error": f"Meta HTTP {res.status_code}: {res.text}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def send_whatsapp_flow(to: str, agent_id: int | None, lead_id: int | None, flow_id: str, body_text: str,
                       cta_text: str = "Start", first_screen_id: str = "WELCOME",
                       phone_number_id: str | None = None) -> dict:
    """Send a published Flow to a customer as an interactive "flow" message."""
    interactive = {
        "type": "flow",
        "body": {"text": (body_text or "")[:1024]},
        "action": {
            "name": "flow",
            "parameters": {
                "flow_message_version": "3",
                "flow_id": flow_id,
                "flow_cta": (cta_text or "Start")[:20],
                "flow_action": "navigate",
                "flow_action_payload": {"screen": first_screen_id or "WELCOME"},
            },
        },
    }
    payload = {"messaging_product": "whatsapp", "to": clean_phone(to), "type": "interactive", "interactive": interactive}
    return _meta_post(payload, agent_id, lead_id, phone_number_id)


def _meta_post(payload: dict, agent_id: int | None, lead_id: int | None, phone_number_id: str | None = None) -> dict:
    """Shared sync POST to the Meta Graph /messages endpoint for message types beyond plain text
    (document, interactive, location). Composio has no equivalent action for these, so they always go
    through the direct Meta token/phone-number-id path, same credentials as send_whatsapp_message."""
    token = settings.whatsapp_token
    pid = phone_number_id or _profile_phone_id(agent_id) or settings.whatsapp_phone_number_id
    if not (token and pid):
        return {"ok": False, "error": "WhatsApp credentials not configured"}
    try:
        with httpx.Client(timeout=10.0) as client:
            res = client.post(f"https://graph.facebook.com/v19.0/{pid}/messages",
                              headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                              json=payload)
        if res.status_code in (200, 201):
            events.record("whatsapp.sent", f"WhatsApp {payload.get('type')} dispatched", "",
                          agent_id=agent_id, lead_id=lead_id, actor="ai")
            return {"ok": True, "data": res.json()}
        return {"ok": False, "error": f"Meta HTTP {res.status_code}: {res.text}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def send_whatsapp_document(to: str, agent_id: int | None, lead_id: int | None, link: str, filename: str | None = None,
                           caption: str | None = None, phone_number_id: str | None = None) -> dict:
    doc = {"link": link}
    if filename:
        doc["filename"] = filename
    if caption:
        doc["caption"] = caption
    payload = {"messaging_product": "whatsapp", "to": clean_phone(to), "type": "document", "document": doc}
    return _meta_post(payload, agent_id, lead_id, phone_number_id)


def send_whatsapp_buttons(to: str, agent_id: int | None, lead_id: int | None, body: str, buttons: list[str],
                          phone_number_id: str | None = None) -> dict:
    """Up to 3 quick-reply buttons (Meta's interactive "button" message)."""
    btns = [{"type": "reply", "reply": {"id": f"opt_{i}", "title": str(b)[:20]}} for i, b in enumerate(buttons[:3])]
    interactive = {"type": "button", "body": {"text": (body or "")[:1024]}, "action": {"buttons": btns}}
    payload = {"messaging_product": "whatsapp", "to": clean_phone(to), "type": "interactive", "interactive": interactive}
    return _meta_post(payload, agent_id, lead_id, phone_number_id)


def send_whatsapp_list(to: str, agent_id: int | None, lead_id: int | None, body: str, button_label: str,
                       options: list[str], phone_number_id: str | None = None) -> dict:
    """An interactive "list" message, for more than 3 options."""
    rows = [{"id": f"opt_{i}", "title": str(o)[:24]} for i, o in enumerate(options[:10])]
    interactive = {"type": "list", "body": {"text": (body or "")[:1024]},
                   "action": {"button": (button_label or "Choose")[:20], "sections": [{"title": "Options", "rows": rows}]}}
    payload = {"messaging_product": "whatsapp", "to": clean_phone(to), "type": "interactive", "interactive": interactive}
    return _meta_post(payload, agent_id, lead_id, phone_number_id)


def send_whatsapp_location(to: str, agent_id: int | None, lead_id: int | None, latitude: float, longitude: float,
                           name: str = "", address: str = "", phone_number_id: str | None = None) -> dict:
    loc = {"latitude": latitude, "longitude": longitude}
    if name:
        loc["name"] = name
    if address:
        loc["address"] = address
    payload = {"messaging_product": "whatsapp", "to": clean_phone(to), "type": "location", "location": loc}
    return _meta_post(payload, agent_id, lead_id, phone_number_id)


def send_whatsapp_product(to: str, agent_id: int | None, lead_id: int | None, catalog_id: str,
                          product_retailer_id: str, body_text: str = "Check this out!",
                          phone_number_id: str | None = None) -> dict:
    """A single-product message from Meta's WhatsApp Business product catalog."""
    interactive = {"type": "product", "body": {"text": (body_text or "")[:1024]},
                   "action": {"catalog_id": catalog_id, "product_retailer_id": product_retailer_id}}
    payload = {"messaging_product": "whatsapp", "to": clean_phone(to), "type": "interactive", "interactive": interactive}
    return _meta_post(payload, agent_id, lead_id, phone_number_id)


def send_whatsapp_product_list(to: str, agent_id: int | None, lead_id: int | None, catalog_id: str,
                               header_text: str, body_text: str, sections: list[dict],
                               phone_number_id: str | None = None) -> dict:
    """A multi-product message: a header plus grouped sections of catalog products
    (sections: list[{"title": str, "product_retailer_ids": list[str]}]), capped at 10 sections / 30 products."""
    built_sections = []
    total = 0
    for sec in sections[:10]:
        ids = [str(pid) for pid in (sec.get("product_retailer_ids") or [])]
        ids = ids[: max(0, 30 - total)]
        total += len(ids)
        built_sections.append({"title": str(sec.get("title") or "Options")[:24],
                               "product_items": [{"product_retailer_id": pid} for pid in ids]})
        if total >= 30:
            break
    interactive = {"type": "product_list",
                   "header": {"type": "text", "text": (header_text or "")[:60]},
                   "body": {"text": (body_text or "")[:1024]},
                   "action": {"catalog_id": catalog_id, "sections": built_sections}}
    payload = {"messaging_product": "whatsapp", "to": clean_phone(to), "type": "interactive", "interactive": interactive}
    return _meta_post(payload, agent_id, lead_id, phone_number_id)


def send_whatsapp_catalog(to: str, agent_id: int | None, lead_id: int | None, catalog_id: str, body_text: str,
                          thumbnail_product_retailer_id: str | None = None,
                          phone_number_id: str | None = None) -> dict:
    """A catalog message letting the customer browse the agent's whole connected product catalog."""
    parameters = {}
    if thumbnail_product_retailer_id:
        parameters["thumbnail_product_retailer_id"] = thumbnail_product_retailer_id
    interactive = {"type": "catalog_message", "body": {"text": (body_text or "")[:1024]},
                   "action": {"name": "catalog_message", "parameters": parameters}}
    payload = {"messaging_product": "whatsapp", "to": clean_phone(to), "type": "interactive", "interactive": interactive}
    return _meta_post(payload, agent_id, lead_id, phone_number_id)


def send_whatsapp_cta_url(to: str, agent_id: int | None, lead_id: int | None, body_text: str, button_text: str,
                          url: str, phone_number_id: str | None = None) -> dict:
    """A single call-to-action button that opens a URL (Meta's interactive "cta_url" message)."""
    interactive = {"type": "cta_url", "body": {"text": (body_text or "")[:1024]},
                   "action": {"name": "cta_url", "parameters": {"display_text": (button_text or "")[:20], "url": url}}}
    payload = {"messaging_product": "whatsapp", "to": clean_phone(to), "type": "interactive", "interactive": interactive}
    return _meta_post(payload, agent_id, lead_id, phone_number_id)


def send_whatsapp_location_request(to: str, agent_id: int | None, lead_id: int | None, body_text: str,
                                   phone_number_id: str | None = None) -> dict:
    """Asks the customer to share their location (Meta's interactive "location_request_message")."""
    interactive = {"type": "location_request_message", "body": {"text": (body_text or "")[:1024]},
                   "action": {"name": "send_location"}}
    payload = {"messaging_product": "whatsapp", "to": clean_phone(to), "type": "interactive", "interactive": interactive}
    return _meta_post(payload, agent_id, lead_id, phone_number_id)


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
    """Process one inbound message (blocking; run in a worker thread). Used directly by the no-Redis
    dev fallback (enqueue_inbound) and by tests; the Redis queue path goes through _process_from_queue."""
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


def _handle_batch(batch: list[dict], agent_id: int, wa_id: str) -> None:
    """Record every message in the burst as its own Message row, but generate/send only one reply for
    the merged batch (one chat_agent.respond() + one outbound send)."""
    contexts, added = [], 0
    for m in batch:
        ctx, did_add = _record_turn(m, agent_id, m.get("wamid"), wa_id)
        if did_add:
            added += 1
        if ctx is not None:
            contexts.append(ctx)
    if not contexts:
        return
    combined_say = "\n".join(c["say"] for c in contexts if c["say"])
    _reply_turn(agent_id, contexts[-1], combined_say, drop_last=added)


def _lead_phone(lead_id: int | None) -> str:
    if not lead_id:
        return ""
    from app.models.lead import Lead
    with get_db() as db:
        return db.scalar(select(Lead.phone).where(Lead.id == lead_id)) or ""


_FLOW_FIELD_MAP = {"full_name": "name", "name": "name", "phone": "phone", "email": "email"}


def _apply_flow_fields(crm, lead_id: int, flow_data: dict) -> None:
    """Write common WhatsApp Flow fields onto the lead's CRM record. Deliberately modest: only a small,
    fixed set of well-known field names are mapped (not a generic field-mapper) -- mirrors how other
    tools call CRMService.update with a small explicit dict."""
    update = {}
    for key, value in (flow_data or {}).items():
        column = _FLOW_FIELD_MAP.get(key)
        if column and value:
            update[column] = str(value)
    if update:
        try:
            crm.update(int(lead_id), update, actor="customer", title="Details submitted via WhatsApp Flow")
        except Exception:
            log.exception("Failed to apply WhatsApp Flow fields for lead %s", lead_id)


def _record_turn(msg: dict, agent_id: int, wamid: str | None, wa_id: str) -> tuple[dict | None, bool]:
    """Persist one inbound message as its own Message row (always, for the transcript/audit trail).
    Returns (ctx, added): `ctx` is the reply context (None when there's nothing further to do for this
    particular message -- dedupe/reaction/do-not-call/paused), `added` is whether a Message row was
    actually written (used by batch callers to size the history cutoff for the merged reply)."""
    from app.services.crm_service import CRMService

    if wamid:
        # Persisted dedupe (the unique constraint on agent_id+external_id is the real guard; this just
        # skips the media download / LLM round trip Meta's own retries would otherwise pay for twice).
        with get_db() as db:
            if db.scalar(select(Message.id).where(Message.agent_id == agent_id, Message.external_id == wamid)) is not None:
                return None, False
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
        return None, False
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
    elif kind == "flow_response":
        flow_data = msg.get("flow_data") or {}
        shown = say = "[WhatsApp Flow submitted: " + ", ".join(f"{k}={v}" for k, v in flow_data.items()) + "]"
        _apply_flow_fields(crm, lead_id, flow_data)
    media_type = kind if kind in MEDIA_TYPES else "text"
    added = _add_message(conv_id, agent_id, "in", "customer", shown or f"[{kind or 'message'}]",
                          type_=media_type, external_id=wamid, sender_name=msg.get("name") or None)
    if not added:
        return None, False  # a duplicate wamid raced us past the precheck above
    events.record("whatsapp.received", f"WhatsApp from {phone}", (shown or kind)[:120],
                  agent_id=agent_id, lead_id=lead_id, actor="customer")
    if lead.get("do_not_call") or paused:
        return None, True
    return {"conv_id": conv_id, "lead": lead, "lead_id": lead_id, "phone": phone, "say": say, "msg": msg}, True


_SEND_RETRY_ATTEMPTS = 3
_SEND_RETRY_BACKOFF_SECONDS = [1, 3]  # delays between attempts 1->2 and 2->3


def _send_with_retry(phone: str, reply: str, agent_id: int, lead_id: int,
                      phone_number_id: str | None, conv_id: int | None) -> dict:
    """Bounded retry with backoff for the outbound send only, scoped to transient failures.
    OutsideWindowError is a permanent policy block and is re-raised immediately (no retry).
    Note: this blocks the calling worker thread for up to ~4s worst case (1s + 3s backoff) --
    an accepted tradeoff since the contact's lock is already held during the send today anyway."""
    last_error = None
    for attempt in range(_SEND_RETRY_ATTEMPTS):
        out = _send(phone, reply, agent_id, lead_id, phone_number_id, conv_id=conv_id)
        if out.get("ok"):
            return out
        last_error = out.get("error")
        if attempt < _SEND_RETRY_ATTEMPTS - 1:
            time.sleep(_SEND_RETRY_BACKOFF_SECONDS[attempt])
    return {"ok": False, "error": last_error}


def _reply_turn(agent_id: int, ctx: dict, say: str, drop_last: int = 1) -> None:
    """Generate (and send) one reply -- for a single message or a burst-merged batch alike."""
    from app.services import chat_agent

    conv_id, lead, lead_id, phone, msg = ctx["conv_id"], ctx["lead"], ctx["lead_id"], ctx["phone"], ctx["msg"]
    end_call = False
    if not say:
        reply = NON_TEXT_REPLY
    else:
        history = _history(conv_id)[:-drop_last] if drop_last else _history(conv_id)  # before the turn(s) just added
        try:
            res = chat_agent.respond(agent_id, conv_id, history, say.removeprefix("🎤 "), lead, channel="whatsapp")
        except Exception:
            log.exception("WhatsApp reply generation failed for %s", phone)
            return
        reply, end_call = (res.get("reply") or "").strip(), bool(res.get("end_call"))
        if not reply:
            return
    try:
        out = _send_with_retry(phone, reply, agent_id, lead_id, msg.get("phone_number_id"), conv_id)
    except OutsideWindowError as e:
        log.warning("WhatsApp reply to %s not sent: %s", phone, e)
        return
    if not out.get("ok"):
        log.warning("WhatsApp reply to %s not sent: %s", phone, out.get("error"))
        return
    _add_message(conv_id, agent_id, "out", "ai", reply)
    if end_call:
        close_chat(conv_id)


def _handle_locked(msg: dict, agent_id: int, wamid: str | None, wa_id: str) -> None:
    ctx, _ = _record_turn(msg, agent_id, wamid, wa_id)
    if ctx is None:
        return
    _reply_turn(agent_id, ctx, ctx["say"])


def _apply_chat_outcome(conv_id: int, agent_id: int, lead_id: int | None, history: list[dict]) -> None:
    from app.services.call_service import CallService
    s = CallService(agent_id).apply_outcome(None, lead_id, history)
    if s is not None:
        with get_db() as db:
            conv = db.get(Conversation, conv_id)
            if conv is not None:
                conv.summary = s.get("summary") or "(no summary text)"


def _submit_chat_summary(conv_id: int, agent_id: int, lead_id: int | None, history: list[dict]) -> None:
    from app.services.call_service import _background_pool

    def _done(f):
        if f.exception() is not None:
            log.error("Post-chat summary for conversation %s crashed: %r", conv_id, f.exception())
    _background_pool.submit(_apply_chat_outcome, conv_id, agent_id, lead_id, history).add_done_callback(_done)


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
        try:
            out = _send(phone, text, agent_id, lead_id, conv_id=conv_id)
        except OutsideWindowError as e:
            raise ValueError(str(e))
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
