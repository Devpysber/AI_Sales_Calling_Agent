"""
Instagram Messaging dispatcher/webhook handler -- the Instagram counterpart to whatsapp_service.py.

Reuses the same chat_agent brain and Conversation/Message/ContactIdentity infrastructure WhatsApp uses.
Differences from WhatsApp:
- Identity: Instagram users have no phone number, so leads are found/created via ContactIdentity
  (agent_id, channel="instagram", external_id=<IG-scoped sender id>) instead of phone lookup.
- Webhook shape: Instagram Messaging uses the Messenger-Platform-style payload
  (`entry[].messaging[]`, each item `{sender:{id}, recipient:{id}, message:{mid, text, attachments}}`),
  NOT WhatsApp Business Cloud API's `entry[].changes[].value.messages[]` shape. This is the shape
  documented for Meta's unified Messenger/Instagram Messaging webhooks; it has not been verified against
  a live Meta payload in this change, so treat it as medium-confidence and check against real traffic /
  current Meta docs before relying on it in production.
- Queue: a separate Redis list (own key) rather than sharing WhatsApp's QUEUE_KEY, to avoid touching
  WhatsApp's already-working queue code. The generic primitives (store.acquire_lock/delete,
  store.RedisStore) are reused; the loop shape is necessarily similar but the business logic
  (identity resolution, send payload) is Instagram-specific.
"""

import hashlib
import hmac
import json
import logging
import os
import socket
import threading
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core import store
from app.core.config import settings
from app.core.database import get_db
from app.models.channel import ContactIdentity, Conversation, Message
from app.services import events
from app.services.identity import merge_identity

log = logging.getLogger(__name__)

QUEUE_KEY = "va:instagram:inbound"
BURST_WINDOW_SECONDS = 5  # merge customer messages arriving within this window into one agent turn
BURST_POLL_SECONDS = 1
LOCK_TTL_SECONDS = 30
LOCK_ACQUIRE_ATTEMPTS = 3
LOCK_ACQUIRE_WAIT = 0.3
OWNER = f"{socket.gethostname()}:{os.getpid()}"

CHAT_GUIDANCE = (
    "This is an Instagram DM chat, not a phone call. Write each reply as a short chat message (1-3 "
    "sentences). Links, numbers and addresses may be written out in full. Never offer to call the "
    "customer or send something on WhatsApp: you are already chatting with them on Instagram. Set "
    "end_call to true only when the conversation is clearly finished."
)
NON_TEXT_REPLY = "Sorry, I can only read text messages here. Please type your message."

_warned_secret = False
_locks: dict[tuple, threading.RLock] = {}
_locks_guard = threading.Lock()


def _lock_for(agent_id: int, ig_id: str) -> threading.RLock:
    with _locks_guard:
        return _locks.setdefault((agent_id, ig_id), threading.RLock())


def _queue_enabled() -> bool:
    return isinstance(store.store, store.RedisStore)


def enqueue_inbound(msg: dict) -> None:
    if _queue_enabled():
        store.store.client.rpush(QUEUE_KEY, json.dumps(msg))
    else:
        handle_inbound(msg)


def _lock_name(agent_id: int, ig_id: str) -> str:
    return f"instagram:{agent_id}:{ig_id}"


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
    return (msg.get("page_id"), msg.get("from"))


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
    """Consumer loop for the Instagram inbound queue. Same BLPOP+per-contact-lock shape as WhatsApp's
    queue_worker_loop, kept as its own loop/queue rather than sharing WhatsApp's to avoid risking its
    now-working queue code; merges a short burst of messages into one agent turn, mirroring WhatsApp."""
    client = store.store.client
    while stop_event is None or not stop_event.is_set():
        item = client.blpop(QUEUE_KEY, timeout=5)
        if item is None:
            continue
        _, raw = item
        try:
            msg = json.loads(raw)
        except Exception:
            log.exception("Instagram queue: unreadable message, dropping")
            continue
        _process_from_queue(msg)


def _process_from_queue(msg: dict) -> None:
    ig_id = msg.get("from")
    if not ig_id:
        return
    agent_id = resolve_agent(msg.get("page_id"))
    if not agent_id:
        log.warning("Instagram message from %s: no agent resolved (set INSTAGRAM_AGENT_ID)", ig_id)
        return
    name = _lock_name(agent_id, ig_id)
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
            _handle_batch(batch, agent_id, ig_id)
        except Exception:
            log.exception("Instagram inbound handling failed for %s", ig_id)
    finally:
        store.delete("lock:" + name)


def verify_signature(body: bytes, header: str | None) -> bool:
    global _warned_secret
    secret = settings.instagram_app_secret
    if not secret:
        if not _warned_secret:
            _warned_secret = True
            log.warning("INSTAGRAM_APP_SECRET not set: webhook signatures are NOT being verified")
        return True
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256="):])


def parse_webhook(payload: dict) -> list[dict]:
    """Normalize one Instagram Messaging webhook delivery. Shipped: text messages, plus a generic
    "[attachment]" fallback for non-text (image/story-reply/etc). Deferred: attachment download/
    transcription, story-mention/reply-specific handling -- a later day's work, same as WhatsApp's
    media pipeline was built incrementally."""
    out = []
    for entry in (payload or {}).get("entry") or []:
        page_id = entry.get("id")
        for m in entry.get("messaging") or []:
            message = m.get("message") or {}
            if not message or message.get("is_echo"):
                continue  # echo of our own outbound send, or a non-message event (e.g. read receipt)
            sender = (m.get("sender") or {}).get("id")
            if not sender:
                continue
            text = (message.get("text") or "").strip()
            if not text and message.get("attachments"):
                text = "[attachment]"
            out.append({"mid": message.get("mid"), "from": sender, "text": text,
                        "page_id": page_id, "recipient_id": (m.get("recipient") or {}).get("id")})
    return out


def resolve_agent(page_id: str | None) -> int | None:
    """Profile-based first (mirrors whatsapp_service._profile_phone_id / resolve_agent): the agent whose
    profile `instagram_account_id` matches the inbound page id, so multiple agents can each own their own
    connected Instagram account. Falls back to the single-tenant env vars for anyone who hasn't set it."""
    if page_id:
        from app.services import agents
        for aid in agents.ids():
            if str((agents.get_profile(aid) or {}).get("instagram_account_id") or "").strip() == str(page_id):
                return aid
    if page_id and settings.instagram_page_id and str(page_id) == str(settings.instagram_page_id):
        return settings.instagram_agent_id or None
    return settings.instagram_agent_id or None


async def send_instagram_message(to: str, text: str, agent_id: int | None = None, lead_id: int | None = None) -> dict:
    token = settings.instagram_token
    page_id = settings.instagram_page_id
    if not (token and page_id):
        events.record("instagram.queued", f"Instagram ready for {to} (Awaiting Instagram credentials)",
                      text[:120], agent_id=agent_id, lead_id=lead_id, actor="system")
        return {"ok": False, "error": "Instagram credentials not configured"}
    url = f"https://graph.facebook.com/v19.0/{page_id}/messages"
    payload = {"recipient": {"id": to}, "message": {"text": text}}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(url, params={"access_token": token}, json=payload)
            if res.status_code in (200, 201):
                events.record("instagram.sent", f"Instagram dispatched to {to}", text[:120],
                              agent_id=agent_id, lead_id=lead_id, actor="ai")
                return {"ok": True, "provider": "meta", "data": res.json()}
            log.warning("Instagram send response %s: %s", res.status_code, res.text)
            return {"ok": False, "error": f"Meta HTTP {res.status_code}: {res.text}"}
    except Exception as e:
        log.exception("Instagram dispatch error: %s", e)
        return {"ok": False, "error": str(e)}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _find_or_create_lead(agent_id: int, ig_id: str) -> int:
    """Identity via ContactIdentity, not phone: Instagram users commonly have no phone number."""
    with get_db() as db:
        ci = db.scalar(select(ContactIdentity).where(ContactIdentity.agent_id == agent_id,
                       ContactIdentity.channel == "instagram", ContactIdentity.external_id == ig_id))
        if ci:
            return ci.lead_id
    from app.services.crm_service import CRMService
    crm = CRMService(agent_id)
    lead = crm.create({"phone": None, "name": "Instagram contact", "source": "Instagram"},
                      actor="system", allow_no_phone=True)
    with get_db() as db:
        db.add(ContactIdentity(agent_id=agent_id, lead_id=lead["id"], channel="instagram", external_id=ig_id))
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            existing = db.scalar(select(ContactIdentity).where(ContactIdentity.agent_id == agent_id,
                                 ContactIdentity.channel == "instagram", ContactIdentity.external_id == ig_id))
            if existing:
                return existing.lead_id
            raise
    return lead["id"]


def _open_chat(agent_id: int, lead_id: int) -> int:
    with get_db() as db:
        conv = db.scalars(select(Conversation).where(Conversation.agent_id == agent_id, Conversation.lead_id == lead_id,
                          Conversation.channel == "instagram", Conversation.status == "open")
                          .order_by(Conversation.id.desc())).first()
        if conv:
            return conv.id
        conv = Conversation(agent_id=agent_id, lead_id=lead_id, channel="instagram", status="open", ai_paused=False)
        db.add(conv)
        db.flush()
        return conv.id


def _history(conv_id: int) -> list[dict]:
    with get_db() as db:
        msgs = db.scalars(select(Message).where(Message.conversation_id == conv_id).order_by(Message.id)).all()
        return [{"role": "customer" if m.sender_type == "customer" else "assistant", "text": m.text or ""} for m in msgs]


def _add_message(conv_id: int, agent_id: int, direction: str, sender_type: str, text: str, *,
                  external_id: str | None = None) -> bool:
    now = _now().replace(tzinfo=None)
    with get_db() as db:
        msg = Message(conversation_id=conv_id, agent_id=agent_id, direction=direction, sender_type=sender_type,
                      type="text", text=text, external_id=external_id,
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


def _send(to: str, text: str, agent_id: int, lead_id: int) -> dict:
    import asyncio
    return asyncio.run(send_instagram_message(to, text, agent_id=agent_id, lead_id=lead_id))


def handle_inbound(msg: dict) -> None:
    """Process one inbound message (blocking). Used by the no-Redis dev fallback and by tests; the Redis
    queue path goes through queue_worker_loop's own locking."""
    ig_id = msg.get("from")
    if not ig_id:
        return
    agent_id = resolve_agent(msg.get("page_id"))
    if not agent_id:
        log.warning("Instagram message from %s: no agent resolved (set INSTAGRAM_AGENT_ID)", ig_id)
        return
    with _lock_for(agent_id, ig_id):
        try:
            _handle_locked(msg, agent_id, ig_id)
        except Exception:
            log.exception("Instagram inbound handling failed for %s", ig_id)


def _maybe_merge_on_phone(agent_id: int, lead_id: int) -> None:
    """If this (Instagram-only) lead has since acquired a phone number that matches a different,
    existing lead, reassign this lead's identities/conversations onto that lead instead of keeping a
    duplicate. The existing phone-identified lead always wins (treated as the richer record); this does
    not merge CRM field conflicts, only ContactIdentity/Conversation ownership."""
    from app.services.crm_service import CRMService
    crm = CRMService(agent_id)
    lead = crm.get(lead_id)
    phone = (lead or {}).get("phone")
    if not phone:
        return
    other = crm.find_by_phone(phone)
    if other and other["id"] != lead_id:
        merge_identity(agent_id, lead_id, other["id"])


def _apply_chat_outcome(conv_id: int, agent_id: int, lead_id: int | None, history: list[dict]) -> None:
    from app.services.call_service import CallService
    s = CallService(agent_id).apply_outcome(None, lead_id, history)
    if s is not None:
        with get_db() as db:
            conv = db.get(Conversation, conv_id)
            if conv is not None:
                conv.summary = s.get("summary") or "(no summary text)"
    if lead_id:
        _maybe_merge_on_phone(agent_id, lead_id)


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
        has_customer = db.scalar(select(Message.id).where(Message.conversation_id == conv_id,
                                  Message.sender_type == "customer")) is not None
        conv.status = "closed"
        conv.closed_at = _now().replace(tzinfo=None)
    if has_customer:
        _submit_chat_summary(conv_id, agent_id, lead_id, _history(conv_id))
    return True


def _handle_batch(batch: list[dict], agent_id: int, ig_id: str) -> None:
    """Record every message in the burst as its own Message row, but generate/send only one reply for
    the merged batch (one chat_agent.respond() + one outbound send)."""
    contexts, added = [], 0
    for m in batch:
        ctx, did_add = _record_turn(m, agent_id, ig_id)
        if did_add:
            added += 1
        if ctx is not None:
            contexts.append(ctx)
    if not contexts:
        return
    combined_say = "\n".join(c["say"] for c in contexts if c["say"])
    _reply_turn(agent_id, contexts[-1], combined_say, drop_last=added)


def _record_turn(msg: dict, agent_id: int, ig_id: str) -> tuple[dict | None, bool]:
    """Persist one inbound message as its own Message row (always, for the transcript/audit trail).
    Returns (ctx, added): `ctx` is the reply context (None when there's nothing further to do for this
    particular message -- dedupe/do-not-call/paused), `added` is whether a Message row was actually
    written (used by batch callers to size the history cutoff for the merged reply)."""
    from app.services.crm_service import CRMService

    mid = msg.get("mid")
    if mid:
        with get_db() as db:
            if db.scalar(select(Message.id).where(Message.agent_id == agent_id, Message.external_id == mid)) is not None:
                return None, False
    lead_id = _find_or_create_lead(agent_id, ig_id)
    conv_id = _open_chat(agent_id, lead_id)
    with get_db() as db:
        paused = bool(db.scalar(select(Conversation.ai_paused).where(Conversation.id == conv_id)))
    text = (msg.get("text") or "").strip()
    added = _add_message(conv_id, agent_id, "in", "customer", text or "[attachment]", external_id=mid)
    if not added:
        return None, False
    lead = CRMService(agent_id).get(lead_id) or {"id": lead_id}
    events.record("instagram.received", f"Instagram from {ig_id}", text[:120],
                  agent_id=agent_id, lead_id=lead_id, actor="customer")
    if lead.get("do_not_call") or paused:
        return None, True
    return {"conv_id": conv_id, "lead": lead, "lead_id": lead_id, "ig_id": ig_id, "say": text, "msg": msg}, True


def _reply_turn(agent_id: int, ctx: dict, say: str, drop_last: int = 1) -> None:
    """Generate (and send) one reply -- for a single message or a burst-merged batch alike."""
    from app.services import chat_agent

    conv_id, lead, lead_id, ig_id = ctx["conv_id"], ctx["lead"], ctx["lead_id"], ctx["ig_id"]
    if not say:
        reply, end_call = NON_TEXT_REPLY, False
    else:
        history = _history(conv_id)[:-drop_last] if drop_last else _history(conv_id)
        try:
            res = chat_agent.respond(agent_id, conv_id, history, say, lead, channel="instagram")
        except Exception:
            log.exception("Instagram reply generation failed for %s", ig_id)
            return
        reply, end_call = (res.get("reply") or "").strip(), bool(res.get("end_call"))
        if not reply:
            return
    out = _send(ig_id, reply, agent_id, lead_id)
    if not out.get("ok"):
        log.warning("Instagram reply to %s not sent: %s", ig_id, out.get("error"))
    _add_message(conv_id, agent_id, "out", "ai", reply)
    if end_call:
        close_chat(conv_id)


def _handle_locked(msg: dict, agent_id: int, ig_id: str) -> None:
    ctx, _ = _record_turn(msg, agent_id, ig_id)
    if ctx is None:
        return
    _reply_turn(agent_id, ctx, ctx["say"])


def human_reply(conv_id: int, text: str, by: str) -> dict:
    with get_db() as db:
        conv = db.get(Conversation, conv_id)
        if conv is None or conv.channel != "instagram":
            raise LookupError("Chat not found.")
        agent_id, lead_id, status = conv.agent_id, conv.lead_id, conv.status
        ci = db.scalar(select(ContactIdentity).where(ContactIdentity.agent_id == agent_id,
                       ContactIdentity.channel == "instagram", ContactIdentity.lead_id == lead_id))
        ig_id = ci.external_id if ci else None
    if status != "open":
        raise ValueError("This chat is closed.")
    if not ig_id:
        raise ValueError("No Instagram identity for this chat.")
    with _lock_for(agent_id or 0, ig_id):
        out = _send(ig_id, text, agent_id, lead_id)
        if not out.get("ok"):
            raise ValueError(out.get("error") or "Instagram send failed")
        with get_db() as db:
            msg = Message(conversation_id=conv_id, agent_id=agent_id, direction="out", sender_type="human",
                          sender_name=by, type="text", text=text, status="sent")
            db.add(msg)
            db.flush()
            conv = db.get(Conversation, conv_id)
            conv.ai_paused = True
            conv.last_message_at = _now().replace(tzinfo=None)
    return {"ok": True}


def set_ai_paused(conv_id: int, paused: bool) -> None:
    with get_db() as db:
        conv = db.get(Conversation, conv_id)
        if conv is None or conv.channel != "instagram":
            raise LookupError("Chat not found.")
        conv.ai_paused = paused
