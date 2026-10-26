"""
WhatsApp chat agent.

A lead messages the number linked to an agent; the same persona, knowledge base and lead record that serve
its calls answer in chat. One LLM call per reply, and one reply per burst of messages.
"""

import asyncio
import contextlib
import time
import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.core import store
from app.core.database import get_db
from app.core.logging import get_logger
from app.models.whatsapp_message import WhatsAppMessage
from app.services import agent, agents, events, whatsapp_service
from app.services.call_service import apply_turn_signals
from app.services.crm_service import CRMService
from app.services.settings_service import SettingsService

log = get_logger(__name__)

DEBOUNCE_SECONDS = 2.5       # people send 2-3 messages in a row: wait for the last one
HUMAN_QUIET_MINUTES = 30     # a reply typed from the dashboard keeps the agent quiet this long
HISTORY_MESSAGES = 20
QUIET_MINUTES = 30           # a chat this idle gets summarised onto the lead
SUMMARY_PER_RUN = 5          # chats summarised per scheduler run (cost cap)
ANSWERED_TTL = 7 * 86400
RETRY_SECONDS = 20           # one delayed retry of a reply that failed (LLM error, bridge hiccup)


def _now() -> datetime:
    return datetime.utcnow()


def _rows(agent_id: int, lead_id: int, limit: int = HISTORY_MESSAGES) -> list:
    """Newest `limit` messages of this lead's chat, oldest first."""
    with get_db() as db:
        rows = db.execute(
            select(WhatsAppMessage.id, WhatsAppMessage.direction, WhatsAppMessage.text, WhatsAppMessage.provider,
                   WhatsAppMessage.created_at)
            .where(WhatsAppMessage.agent_id == agent_id, WhatsAppMessage.lead_id == lead_id)
            .order_by(WhatsAppMessage.id.desc()).limit(limit)).all()
    return list(reversed(rows))


def _latest_inbound_id(agent_id: int, lead_id: int) -> int:
    with get_db() as db:
        return db.scalar(select(func.max(WhatsAppMessage.id)).where(
            WhatsAppMessage.agent_id == agent_id, WhatsAppMessage.lead_id == lead_id, WhatsAppMessage.direction == "in")) or 0


def unanswered(rows: list, answered_upto: int | None) -> tuple[list, list]:
    """(history before the burst, inbound messages of the burst). A burst is every inbound message past the last reply."""
    last_out = max((r.id for r in rows if r.direction == "out"), default=0)
    last_human = max((r.id for r in rows if r.direction == "out" and r.provider == "dashboard"), default=0)
    threshold = max(answered_upto if answered_upto is not None else last_out, last_human)
    burst = [r for r in rows if r.direction == "in" and r.id > threshold]
    if not burst:
        return rows, []
    return [r for r in rows if r.id < burst[0].id], burst


async def handle_inbound(agent_id: int, lead_id: int, phone: str) -> None:
    """Debounce a burst of messages, then answer it once. Background task of the inbound webhook."""
    key = f"wa:last:{agent_id}:{lead_id}"
    token = await asyncio.to_thread(_latest_inbound_id, agent_id, lead_id)
    store.set_json(key, token, ttl=60)
    await asyncio.sleep(DEBOUNCE_SECONDS)
    if store.get_json(key) != token:
        return  # a newer message arrived: its own task answers the whole burst
    for attempt in range(2):
        try:
            if await asyncio.to_thread(reply_to_burst, agent_id, lead_id, phone):
                return
        except Exception:
            log.exception("WhatsApp reply failed for lead %s", lead_id)
        if attempt == 0:
            await asyncio.sleep(RETRY_SECONDS)


def reply_to_burst(agent_id: int, lead_id: int, phone: str) -> bool:
    """True when the burst is dealt with (replied, or deliberately left); False when it should be tried again."""
    lock = f"wa:reply:{agent_id}:{lead_id}"
    owner = uuid.uuid4().hex
    for _ in range(30):  # a reply still being written for the previous burst: wait for it
        if store.acquire_lock(lock, owner, 180):
            break
        time.sleep(1)
    else:
        return False
    try:
        return _reply(agent_id, lead_id, phone)
    finally:
        store.release_lock(lock, owner)  # not a plain delete: after a TTL expiry the lock may be someone else's


def _reply(agent_id: int, lead_id: int, phone: str) -> bool:
    if agents.is_paused(agent_id) or not agents.get_automation(agent_id).get("whatsapp_auto_reply", True):
        return True
    crm = CRMService(agent_id)
    lead = crm.get(lead_id)
    if not lead or whatsapp_service.is_opted_out(lead):
        return True
    answered_key = f"wa:answered:{agent_id}:{lead_id}"
    rows = _rows(agent_id, lead_id)
    human_cutoff = _now() - timedelta(minutes=HUMAN_QUIET_MINUTES)
    if any(r.direction == "out" and r.provider == "dashboard" and r.created_at and r.created_at >= human_cutoff for r in rows):
        return True  # a person is in this chat
    history_rows, burst = unanswered(rows, store.get_json(answered_key))
    if not burst:
        return True
    customer_text = "\n".join(r.text for r in burst)[:1500]

    if any(whatsapp_service.is_stop_request(r.text) for r in burst):
        # They asked us to stop: no reply, and nothing is ever sent to them on WhatsApp again.
        whatsapp_service.mark_opt_out(agent_id, lead_id)
        store.set_json(answered_key, burst[-1].id, ttl=ANSWERED_TTL)
        return True

    history = [{"role": "assistant" if r.direction == "out" else "customer", "text": r.text} for r in history_rows]
    result = agent.respond(agent_id, history, customer_text, lead, channel="whatsapp")
    reply = result["reply"]
    if reply:
        sent = whatsapp_service.send_whatsapp_message_sync(to=phone, text=reply, agent_id=agent_id, lead_id=lead_id, reply=True)
        if not sent.get("ok"):
            log.warning("WhatsApp reply to lead %s not sent: %s", lead_id, sent.get("error"))
            if not sent.get("refused"):
                return False  # left unanswered; signals and actions wait for the retry so they run once
    # Marked answered only now: an LLM error or a failed send used to swallow the burst for good.
    store.set_json(answered_key, burst[-1].id, ttl=ANSWERED_TTL)
    apply_turn_signals(crm, lead_id, result)
    _act(agent_id, lead_id, lead, result.get("action", "none"), customer_text)
    return True


def _act(agent_id: int, lead_id: int, lead: dict, action: str, customer_text: str) -> None:
    if action == "call_now":
        from app.services.agent_tools import dial_lead_tool
        outcome = dial_lead_tool(agent_id, lead_id, now=True)
        events.record("whatsapp.call", f"Call requested on WhatsApp: {outcome}"[:255], agent_id=agent_id, lead_id=lead_id, actor="ai")
    elif action == "handoff":
        from app.services.agent_tools import add_note_tool
        from app.services.notification_service import notify_team
        who = lead.get("name") or lead.get("phone")
        ask = customer_text[:200]
        add_note_tool(agent_id, lead_id, f"WhatsApp: customer wants a person. They said: {ask}")
        events.record("whatsapp.handover", f"Action for the team: {ask}", f"from {who} on WhatsApp", agent_id=agent_id,
                      lead_id=lead_id, actor="ai")
        persona = agents.get_profile(agent_id)
        with contextlib.suppress(Exception):
            notify_team(f"{who} wants a person on WhatsApp - {persona['company_name']}",
                        f"{who} asked for a human on WhatsApp.\n\nThey said: {ask}\nPhone: {lead.get('phone')}",
                        lead_id=lead_id, agent_id=agent_id)


def summarise_quiet_chats(agent_id: int) -> str:
    """Scheduler job: write a summary onto leads whose chat went quiet (only chats with 2+ messages from the lead)."""
    now = _now()
    state = SettingsService()
    with get_db() as db:
        candidates = db.execute(
            select(WhatsAppMessage.lead_id, func.max(WhatsAppMessage.id), func.max(WhatsAppMessage.created_at))
            .where(WhatsAppMessage.agent_id == agent_id, WhatsAppMessage.lead_id.is_not(None),
                   WhatsAppMessage.created_at >= now - timedelta(days=1))
            .group_by(WhatsAppMessage.lead_id)).all()
    done = 0
    for lead_id, last_id, last_at in candidates:
        if done >= SUMMARY_PER_RUN:
            break
        if last_at > now - timedelta(minutes=QUIET_MINUTES) or (state.get_state(f"wa_summary.{lead_id}") or 0) >= last_id:
            continue
        with get_db() as db:
            inbound = db.scalar(select(func.count(WhatsAppMessage.id)).where(
                WhatsAppMessage.agent_id == agent_id, WhatsAppMessage.lead_id == lead_id, WhatsAppMessage.direction == "in"))
        if (inbound or 0) < 2:
            continue
        rows = _rows(agent_id, lead_id, 40)
        history = [{"role": "assistant" if r.direction == "out" else "customer", "text": r.text} for r in rows]
        try:
            s = agent.summarize(history)
        except Exception as e:  # noqa: BLE001 - tried again on the next run
            log.warning("WhatsApp summary for lead %s failed: %s", lead_id, e)
            continue
        from app.services.call_service import _merge_text
        crm = CRMService(agent_id)
        current = crm.get(lead_id) or {}
        updates = {"summary": "WhatsApp: " + str(s["summary"])[:900]} if s.get("summary") else {}
        for key in ("requirements", "objections"):
            merged = _merge_text(current.get(key), s.get(key))
            if merged is not None:
                updates[key] = merged
        if s.get("qualification") in ("Hot", "Warm", "Cold"):
            updates["qualification"] = s["qualification"]
        if updates:
            crm.update(lead_id, updates, actor="system", touch=True)
        events.record("whatsapp.summary", "AI summarised the WhatsApp chat", str(s.get("summary") or "")[:300],
                      agent_id=agent_id, lead_id=lead_id, actor="ai")
        state.set_state(f"wa_summary.{lead_id}", last_id)
        done += 1
    return f"{done} chat(s) summarised" if done else "no chats to summarise"
