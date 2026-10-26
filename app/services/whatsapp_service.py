"""
WhatsApp & Post-Call Messaging Dispatcher.
Supports:
1. Native WhatsApp Web Bridge (QR code scan for any Personal or Business WhatsApp number).
2. Composio WhatsApp connector.
3. WhatsApp Cloud API / Direct WhatsApp Webhook fallback.
"""

import asyncio
import hashlib
import logging
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import httpx
from app.core import store
from app.core.config import settings
from app.services import events

log = logging.getLogger(__name__)

def is_whatsapp_configured() -> bool:
    return True

def clean_phone(phone: str) -> str:
    """Normalize phone to E.164 without plus, e.g. 919876543210."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 10:
        return "91" + digits
    return digits

def _bridge_headers() -> dict:
    return {"X-Bridge-Secret": settings.wa_bridge_secret} if settings.wa_bridge_secret else {}


async def bridge_request(agent_id: int, method: str, path: str, json: dict | None = None) -> dict:
    """Call the wa-bridge for one agent (path like "status" or "send"); never raises, failures come back as ok=False."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.request(method, f"{settings.wa_bridge_url.rstrip('/')}/api/wa/{agent_id}/{path}",
                                       headers=_bridge_headers(), json=json)
        data = res.json() if res.content else {}
        data = data if isinstance(data, dict) else {"data": data}
        return {"ok": res.status_code < 400, **data, "http_status": res.status_code}
    except Exception as e:  # noqa: BLE001 - the bridge being down is a state to show, not a crash
        log.warning("WhatsApp bridge %s %s failed: %s", method, path, e)
        return {"ok": False, "status": "bridge_unreachable", "error": f"WhatsApp bridge unreachable: {e}"}


def _save_outbound(agent_id: int | None, lead_id: int | None, recipient: str, text: str, provider: str,
                   message_id: str | None, via: str | None) -> None:
    """Keep every sent message in the chat log so the agent, the lead page and later calls can read it."""
    if not agent_id:
        return
    try:
        from app.core.database import get_db
        from app.models.whatsapp_message import WhatsAppMessage
        if not lead_id:
            from app.services.crm_service import CRMService
            lead_id = (CRMService(agent_id).find_by_phone(recipient) or {}).get("id")
        with get_db() as db:
            db.add(WhatsAppMessage(agent_id=agent_id, lead_id=lead_id, phone=recipient, direction="out", text=text,
                                   wa_message_id=message_id or None, provider=via or provider))
    except Exception:
        log.exception("Failed to store outbound WhatsApp message")


# Anti-ban rules for linked personal/business numbers. Replies to someone who messaged us are never capped;
# messages WE start are limited per agent number (the bridge adds an overall messages-per-minute bucket).
OPT_OUT_TAG = "whatsapp_opt_out"
GAP_SECONDS = (20, 60)       # random gap between messages we initiate, per number
DUPLICATE_RECIPIENTS = 3     # the same text to more than this many people in 24 h is refused

STOP_RE = re.compile(
    r"^\W*(stop|unsubscribe|opt[ -]?out)\W*$|unsubscribe|(message|msg|text) (mat|nahi|na) (karo|karna|kijiye)|(message|messages|msg|whatsapp) band karo|"
    r"(don'?t|do not|never|stop) (message|messaging|text|texting|msg|whatsapp)", re.I)


def is_opted_out(lead: dict | None) -> bool:
    return bool(lead) and OPT_OUT_TAG in (lead.get("tags") or [])


def mark_opt_out(agent_id: int, lead_id: int) -> None:
    """Tag the lead so nothing is ever sent to them on WhatsApp again (the tag shows on the lead page)."""
    from app.services.crm_service import CRMService
    crm = CRMService(agent_id)
    lead = crm.get(lead_id) or {}
    if lead and not is_opted_out(lead):
        crm.update(lead_id, {"tags": [*lead.get("tags", []), OPT_OUT_TAG]}, actor="ai", event_type="whatsapp.opt_out",
                   title="Asked not to be messaged on WhatsApp")


def _limited(agent_id: int, lead_id: int | None, text: str, kind: str, message: str) -> dict:
    """Record why a send was queued or refused, so it shows on the dashboard."""
    events.record("whatsapp.limited", message, text[:120], agent_id=agent_id, lead_id=lead_id, actor="system")
    return {"ok": False, kind: True, "error": message}


def _has_inbound(agent_id: int, recipient: str) -> bool:
    from sqlalchemy import select

    from app.core.database import get_db
    from app.models.whatsapp_message import WhatsAppMessage
    with get_db() as db:
        return db.scalar(select(WhatsAppMessage.id).where(
            WhatsAppMessage.agent_id == agent_id, WhatsAppMessage.phone == recipient, WhatsAppMessage.direction == "in").limit(1)) is not None


def _check_limits(agent_id: int, lead_id: int | None, recipient: str, text: str, reply: bool, cleared: bool, send_later) -> dict | None:
    """None when the send may go now; otherwise the refused / queued result."""
    from app.services.crm_service import CRMService
    crm = CRMService(agent_id)
    lead = (crm.get(lead_id) if lead_id else None) or crm.find_by_phone(recipient) or {}
    lead_id = lead_id or lead.get("id")
    if is_opted_out(lead):
        return _limited(agent_id, lead_id, text, "refused", f"Not sent: +{recipient} asked not to be messaged on WhatsApp.")
    if reply or cleared:
        return None
    if lead.get("do_not_call"):
        return _limited(agent_id, lead_id, text, "refused", f"Not sent: +{recipient} is marked do not contact.")
    digest = hashlib.sha1(" ".join(text.lower().split()).encode()).hexdigest()[:16]
    seen = store.get_json(f"wa:dup:{agent_id}:{digest}", []) or []
    if recipient not in seen and len(seen) >= DUPLICATE_RECIPIENTS:
        return _limited(agent_id, lead_id, text, "refused",
                        f"Not sent: the same text already went to {len(seen)} other people in 24 hours. Personalise the message.")
    if not _has_inbound(agent_id, recipient) and store.rate_limited(f"wa_new:{agent_id}:{date.today()}", settings.wa_daily_new_chats, 86400):
        return _limited(agent_id, lead_id, text, "refused",
                        f"Not sent: the daily limit of {settings.wa_daily_new_chats} new WhatsApp conversations is reached. Try tomorrow.")
    if recipient not in seen:
        store.set_json(f"wa:dup:{agent_id}:{digest}", [*seen, recipient], ttl=86400)
    now = time.time()
    run_at = max(now, float(store.get_json(f"wa:gap:{agent_id}", 0) or 0))
    store.set_json(f"wa:gap:{agent_id}", run_at + random.uniform(*GAP_SECONDS), ttl=3600)
    if run_at - now > 1:
        delay = run_at - now
        timer = threading.Timer(delay, send_later)
        timer.daemon = True
        timer.start()
        return _limited(agent_id, lead_id, text, "queued",
                        f"Queued, not sent yet: WhatsApp messages we start are spaced out; this goes in about {int(delay)}s.")
    return None


async def send_whatsapp_message(
    to: str,
    text: str,
    agent_id: int | None = None,
    lead_id: int | None = None,
    call_id: int | None = None,
    via: str | None = None,
    reply: bool = False,
    _cleared: bool = False,
) -> dict:
    """
    `via` overrides the provider stored in the chat log (the dashboard sets "dashboard": a human is in the chat).
    `reply` marks an answer to a message they sent: shown as typing, and exempt from the new-conversation limits.
    Anything else is agent-initiated and limited (opt-out, duplicate text, daily new chats, spacing); a send held back
    comes back as {"ok": False, "queued": True} or {"ok": False, "refused": True}.
    Dispatch WhatsApp message via:
    1. Local Native WhatsApp Web Bridge (QR scan login for personal & business WhatsApp).
    2. Composio WhatsApp API.
    3. Meta Cloud API.
    """
    recipient = clean_phone(to)
    if not recipient:
        return {"ok": False, "error": "Invalid phone number"}

    if agent_id and via != "dashboard":
        verdict = await asyncio.to_thread(
            _check_limits, agent_id, lead_id, recipient, text, reply, _cleared,
            lambda: send_whatsapp_message_sync(to=recipient, text=text, agent_id=agent_id, lead_id=lead_id, call_id=call_id,
                                               via=via, _cleared=True))
        if verdict:
            return verdict

    # 1. Native WhatsApp Web Bridge check
    try:
        if agent_id:
            base = f"{settings.wa_bridge_url.rstrip('/')}/api/wa/{agent_id}"
            async with httpx.AsyncClient(timeout=8.0, headers=_bridge_headers()) as client:
                status_res = await client.get(f"{base}/status")
                if status_res.status_code == 200:
                    data = status_res.json()
                    if data.get("status") == "connected":
                        send_res = await client.post(f"{base}/send", json={"to": recipient, "text": text, "typing": reply}, timeout=45.0)
                        if send_res.status_code == 200 and send_res.json().get("ok"):
                            events.record(
                                "whatsapp.sent",
                                f"WhatsApp sent to +{recipient}",
                                text[:120],
                                agent_id=agent_id,
                                lead_id=lead_id,
                                call_id=call_id,
                                actor="ai"
                            )
                            _save_outbound(agent_id, lead_id, recipient, text, "native_qr",
                                           send_res.json().get("messageId"), via)
                            return {"ok": True, "provider": "native_qr", "data": send_res.json()}
    except Exception as e:
        log.warning("Native WhatsApp Web Bridge error: %s", e)

    # 2. Composio WhatsApp Tool Action Call
    composio_key = getattr(settings, "composio_api_key", "")
    if composio_key:
        try:
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
                    _save_outbound(agent_id, lead_id, recipient, text, "composio", None, via)
                    return {"ok": True, "provider": "composio", "data": res.json()}
                else:
                    log.warning("Composio WhatsApp response %s: %s", res.status_code, res.text)
        except Exception as e:
            log.exception("Composio dispatch error: %s", e)

    # 3. Fallback to direct Meta/WhatsApp token if configured
    whatsapp_token = getattr(settings, "whatsapp_token", "")
    phone_number_id = getattr(settings, "whatsapp_phone_number_id", "")
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
                    _save_outbound(agent_id, lead_id, recipient, text, "meta", None, via)
                    return {"ok": True, "provider": "meta", "data": res.json()}
        except Exception as e:
            log.warning("Meta dispatch error: %s", e)

    # Not connected yet
    events.record(
        "whatsapp.queued",
        f"WhatsApp ready for +{recipient} (Scan QR Code on WhatsApp tab to connect)",
        text[:120],
        agent_id=agent_id,
        lead_id=lead_id,
        call_id=call_id,
        actor="system"
    )
    return {"ok": False, "error": "WhatsApp account not connected. Scan QR code in WhatsApp tab."}


def send_whatsapp_message_sync(**kwargs) -> dict:
    """send_whatsapp_message for synchronous callers (tools run in worker threads, possibly under a running loop)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(send_whatsapp_message(**kwargs))
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(send_whatsapp_message(**kwargs))).result()
