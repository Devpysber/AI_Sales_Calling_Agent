"""
Reminders and account balances for the navbar bell.

- Credits: Plivo balance and OpenRouter credit (from their APIs), cached a few minutes so
  the dashboard polling never costs provider requests. Sarvam and Resend have no balance API.
- Reminders per agent: callbacks due, meetings today/tomorrow, leads with broken numbers,
  unreachable leads, inbound number not connected.
"""

from datetime import datetime, timedelta

import httpx
from sqlalchemy import func, or_, select

from app.core import store
from app.core.config import settings
from app.core.database import get_db
from app.core.logging import get_logger
from app.models.lead import Lead
from app.services import agents

log = get_logger(__name__)
CREDITS_TTL = 300
LOW_PLIVO_BALANCE = 50.0
IST_OFFSET = timedelta(hours=5, minutes=30)


def credits() -> list[dict]:
    cached = store.get_json("alerts:credits")
    if cached is not None:
        return cached
    out = []
    try:
        from app.services.plivo_service import PlivoService
        account = PlivoService().client.account.get()
        balance = float(getattr(account, "cash_credits", 0) or 0)
        out.append({"provider": "Plivo", "label": "Calls balance", "value": f"{balance:.2f} credits", "low": balance < LOW_PLIVO_BALANCE,
                    "hint": "Top up in the Plivo console" if balance < LOW_PLIVO_BALANCE else "Telephony credit"})
    except Exception as e:  # noqa: BLE001
        out.append({"provider": "Plivo", "label": "Calls balance", "value": "unavailable", "low": False, "hint": str(e)[:80]})
    if settings.openrouter_api_key:
        try:
            data = httpx.get("https://openrouter.ai/api/v1/key", timeout=8,
                             headers={"Authorization": f"Bearer {settings.openrouter_api_key}"}).json()["data"]
            remaining = data.get("limit_remaining")
            free = data.get("is_free_tier")
            value = "Free tier" if free and remaining is None else (f"${remaining:.2f} left" if remaining is not None else f"${data.get('usage', 0):.2f} used")
            out.append({"provider": "OpenRouter", "label": "LLM credit", "value": value,
                        "low": bool(free) or (remaining is not None and remaining < 1),
                        "hint": "Free models are rate limited: add credits for production" if free else "Summaries & fallback LLM"})
        except Exception as e:  # noqa: BLE001
            out.append({"provider": "OpenRouter", "label": "LLM credit", "value": "unavailable", "low": False, "hint": str(e)[:80]})
    if settings.sarvam_api_key:
        out.append({"provider": "Sarvam", "label": "Voice & live LLM", "value": "Check dashboard", "low": False,
                    "hint": "Sarvam has no balance API: see dashboard.sarvam.ai"})
    store.set_json("alerts:credits", out, ttl=CREDITS_TTL)
    return out


def reminders() -> list[dict]:
    now = datetime.utcnow() + IST_OFFSET
    today, tomorrow = now.strftime("%Y-%m-%d"), (now + timedelta(days=1)).strftime("%Y-%m-%d")
    items = []
    names = {a["id"]: a["name"] for a in agents.list_agents()}
    with get_db() as db:
        def rows(*where):
            return db.execute(select(Lead.agent_id, func.count()).where(*where).group_by(Lead.agent_id)).all()

        for agent_id, n in rows(Lead.callback_at.like(f"{today}%")):
            items.append({"agent_id": agent_id, "kind": "callback", "level": "info", "count": n,
                          "text": f"{n} callback{'s' if n > 1 else ''} scheduled today", "to": "/leads?view=callbacks"})
        for agent_id, n in rows(Lead.meeting_at.like(f"{today}%")):
            items.append({"agent_id": agent_id, "kind": "meeting", "level": "success", "count": n,
                          "text": f"{n} meeting{'s' if n > 1 else ''} today", "to": "/leads?view=meetings"})
        for agent_id, n in rows(Lead.meeting_at.like(f"{tomorrow}%")):
            items.append({"agent_id": agent_id, "kind": "meeting", "level": "info", "count": n,
                          "text": f"{n} meeting{'s' if n > 1 else ''} tomorrow: confirm by call", "to": "/leads?view=meetings"})
        for agent_id, n in rows(Lead.phone.like("+91%"), func.length(Lead.phone) != 13, Lead.do_not_call.is_(False)):
            items.append({"agent_id": agent_id, "kind": "invalid", "level": "danger", "count": n,
                          "text": f"{n} lead{'s' if n > 1 else ''} with an incomplete phone number", "to": "/leads?view=attention"})
        for agent_id, n in rows(Lead.retry_count >= 3, Lead.do_not_call.is_(False),
                                or_(Lead.call_status.is_(None), Lead.call_status.notin_(("Queued", "Ringing", "In Progress")))):
            items.append({"agent_id": agent_id, "kind": "unreachable", "level": "warning", "count": n,
                          "text": f"{n} lead{'s' if n > 1 else ''} unreachable after 3+ tries", "to": "/leads?view=attention"})
    for item in items:
        item["agent"] = names.get(item["agent_id"])
    order = {"danger": 0, "warning": 1, "success": 2, "info": 3}
    return sorted(items, key=lambda i: order[i["level"]])


def inbound_warning() -> list[dict]:
    """The Plivo number points somewhere else (e.g. the public URL changed after an ngrok restart)."""
    cached = store.get_json("alerts:inbound")
    if cached is None:
        cached = []
        try:
            from app.services.plivo_service import PlivoService
            status = PlivoService().inbound_status()
            if status.get("app_name") == PlivoService.INBOUND_APP and not status.get("connected"):
                cached = [{"agent_id": None, "kind": "inbound", "level": "danger", "count": 1, "to": "/settings",
                           "text": "Inbound calls broken: the public URL changed. Reconnect on Integrations."}]
        except Exception:  # noqa: BLE001
            cached = []
        store.set_json("alerts:inbound", cached, ttl=CREDITS_TTL)
    return cached


def summary() -> dict:
    items = inbound_warning() + reminders()
    balance = credits()
    items += [{"agent_id": None, "kind": "credit", "level": "warning", "count": 1, "to": "/settings",
               "text": f"{c['provider']}: {c['value']} · {c['hint']}"} for c in balance if c["low"]]
    return {"items": items, "credits": balance, "attention": sum(1 for i in items if i["level"] in ("danger", "warning"))}
