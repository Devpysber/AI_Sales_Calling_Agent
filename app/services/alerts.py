"""
Reminders and live provider balances for the navbar bell and low-credit popup.

Everything comes from real sources:
- Plivo: account balance, auto-recharge and billing mode from the Account API; the per-minute rate and
  recent spend from the call records (CDR) API, so "minutes left" is computed from what you really pay.
- OpenRouter: /api/v1/key (free tier, credit limit and remaining, daily/weekly/monthly spend).
- Sarvam: no public balance API, so usage measured by this app (voice characters, speech seconds,
  AI replies) today and over 7 days.
- Reminders: the CRM (callbacks, meetings, broken numbers, unreachable leads, Plivo routing).

Balances are cached briefly (force refresh available); reminders can be snoozed per item.
"""

import time
from datetime import datetime, timedelta

import httpx
from sqlalchemy import func, or_, select

from app.core import store
from app.core.config import settings
from app.core.database import get_db
from app.core.logging import get_logger
from app.models.call import Call
from app.models.lead import Lead
from app.services import agents
from app.services.settings_service import SettingsService

log = get_logger(__name__)
CREDITS_TTL = 180
IST_OFFSET = timedelta(hours=5, minutes=30)
LOW_MINUTES = 300      # warn when the Plivo balance covers fewer connected minutes than this
CRITICAL_MINUTES = 60  # popup: calls are about to stop


# ---------------- balances ----------------

def _plivo() -> dict:
    from app.services.plivo_service import PlivoService

    client = PlivoService().client
    account = client.account.get()
    balance = float(getattr(account, "cash_credits", 0) or 0)
    records = client.calls.list(limit=20)
    rates = [float(r.total_rate) for r in records if getattr(r, "total_rate", None)]
    spent = sum(float(getattr(r, "total_amount", 0) or 0) for r in records)
    billed_minutes = sum(int(getattr(r, "bill_duration", 0) or 0) for r in records) / 60
    rate = max(rates) if rates else None
    minutes_left = int(balance / rate) if rate else None
    level = ("critical" if minutes_left is not None and minutes_left < CRITICAL_MINUTES
             else "low" if minutes_left is not None and minutes_left < LOW_MINUTES else "ok")
    return {
        "provider": "Plivo", "label": "Telephony balance", "balance": round(balance, 2), "unit": "credits",
        "value": f"{balance:.2f}", "level": level,
        "detail": (f"≈ {minutes_left:,} connected minutes left at {rate:g}/min" if minutes_left is not None else "No billed calls yet"),
        "facts": [
            ["Billing", f"{getattr(account, 'billing_mode', '—')} · auto-recharge {'on' if getattr(account, 'auto_recharge', False) else 'off'}"],
            ["Last 20 calls", f"{spent:.2f} spent · {billed_minutes:.1f} billed min"],
        ],
        "action": {"label": "Top up Plivo", "url": "https://console.plivo.com/billing/"},
    }


def _openrouter() -> dict | None:
    if not settings.openrouter_api_key:
        return None
    data = httpx.get("https://openrouter.ai/api/v1/key", timeout=8,
                     headers={"Authorization": f"Bearer {settings.openrouter_api_key}"}).json()["data"]
    free, remaining, limit = data.get("is_free_tier"), data.get("limit_remaining"), data.get("limit")
    if remaining is not None:
        level = "critical" if remaining < 0.2 else "low" if remaining < 1 else "ok"
        value = f"${remaining:.2f} left"
    else:
        level = "low" if free else "ok"
        value = "Free tier" if free else "Pay as you go"
    return {
        "provider": "OpenRouter", "label": "Summaries & fallback LLM", "balance": remaining, "unit": "USD",
        "value": value, "level": level,
        "detail": "Free models are rate limited and can time out under load" if free else (f"Limit ${limit:.2f}" if limit else "No spending limit set"),
        "facts": [
            ["Spend today", f"${data.get('usage_daily', 0):.4f}"],
            ["This week / month", f"${data.get('usage_weekly', 0):.4f} / ${data.get('usage_monthly', 0):.4f}"],
        ],
        "action": {"label": "Add credits", "url": "https://openrouter.ai/settings/credits"},
    }


def _sarvam() -> dict | None:
    if not settings.sarvam_api_key:
        return None
    from app.services.settings_service import SettingsService
    secrets = SettingsService().get_state("secrets") or {}
    currency = secrets.get("cost_currency") or settings.cost_currency
    cost_per_tts = float(secrets.get("cost_per_10k_tts_chars") or settings.cost_per_10k_tts_chars)
    cost_per_stt = float(secrets.get("cost_per_stt_hour") or settings.cost_per_stt_hour)
    cost_per_llm = float(secrets.get("cost_per_llm_request") or settings.cost_per_llm_request)
    
    since_day = datetime.utcnow() - timedelta(days=1)
    since_week = datetime.utcnow() - timedelta(days=7)
    with get_db() as db:
        def totals(since):
            return db.execute(select(func.coalesce(func.sum(Call.tts_chars), 0), func.coalesce(func.sum(Call.stt_seconds), 0),
                                     func.coalesce(func.sum(Call.llm_requests), 0)).where(Call.created_at >= since)).one()
        day, week = totals(since_day), totals(since_week)
        
        balance = None
        unit = None
        value = "Measured usage"
        level = "ok"
        detail = "Sarvam has no balance API: usage below is measured by this app"
        
        if "sarvam_credits" in secrets and "sarvam_credits_updated_at" in secrets:
            updated_at = datetime.utcfromtimestamp(secrets["sarvam_credits_updated_at"])
            since_update = totals(updated_at)
            used = (since_update[0] / 10000 * cost_per_tts) + (since_update[1] / 3600 * cost_per_stt) + (since_update[2] * cost_per_llm)
            left = float(secrets["sarvam_credits"]) - used
            balance = round(left, 2)
            unit = currency
            value = f"{currency}{balance:,.2f}"
            level = "ok" if left > 10 else ("low" if left > 0 else "critical")
            detail = f"Estimated balance based on usage since it was set to {secrets['sarvam_credits']}"

    def fmt(t):
        cost = (t[0] / 10000) * cost_per_tts + (t[1] / 3600) * cost_per_stt + t[2] * cost_per_llm
        return f"{currency}{cost:.2f} ({int(t[0]):,} chars · {int(t[1]) // 60}m {int(t[1]) % 60}s speech · {int(t[2])} replies)"
    return {
        "provider": "Sarvam", "label": "Live voice, speech & LLM", "balance": balance, "unit": unit,
        "value": value, "level": level,
        "detail": detail,
        "facts": [["Last 24 hours", fmt(day)], ["Last 7 days", fmt(week)]],
        "action": {"label": "Open Sarvam dashboard", "url": "https://dashboard.sarvam.ai/"},
    }


def credits(force: bool = False) -> dict:
    cached = None if force else store.get_json("alerts:credits:v2")
    if cached is not None:
        return cached
    out = []
    for name, fetch in (("Plivo", _plivo), ("OpenRouter", _openrouter), ("Sarvam", _sarvam)):
        try:
            item = fetch()
        except Exception as e:  # noqa: BLE001 - one provider failing must not hide the others
            log.warning("Balance check for %s failed: %s", name, e)
            item = {"provider": name, "label": "Balance", "value": "Unavailable", "level": "unknown", "detail": str(e)[:120],
                    "facts": [], "action": None, "balance": None, "unit": None}
        if item:
            out.append(item)
    result = {"providers": out, "checked_at": int(time.time())}
    store.set_json("alerts:credits:v2", result, ttl=CREDITS_TTL)
    return result


# ---------------- reminders ----------------

def _snoozed() -> dict:
    state = SettingsService().get_state("alerts_snoozed") or {}
    now = time.time()
    return {k: v for k, v in state.items() if v > now}


def snooze(key: str, hours: float) -> None:
    state = _snoozed()
    state[key] = time.time() + max(0.25, min(hours, 24 * 7)) * 3600
    SettingsService().set_state("alerts_snoozed", state)


def reminders() -> list[dict]:
    now = datetime.utcnow() + IST_OFFSET
    today, tomorrow = now.strftime("%Y-%m-%d"), (now + timedelta(days=1)).strftime("%Y-%m-%d")
    hour_ahead = (now + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
    names = {a["id"]: a["name"] for a in agents.list_agents()}
    items: list[dict] = []

    def add(agent_id, kind, level, n, text, to, action=None, when=None):
        items.append({"key": f"{kind}:{agent_id}:{today}", "agent_id": agent_id, "agent": names.get(agent_id), "kind": kind,
                      "level": level, "count": n, "text": text, "to": to, "action": action, "when": when})

    with get_db() as db:
        def grouped(*where):
            return db.execute(select(Lead.agent_id, func.count(), func.min(Lead.callback_at), func.min(Lead.meeting_at))
                              .where(*where).group_by(Lead.agent_id)).all()

        for agent_id, n, first, _ in grouped(Lead.callback_at.is_not(None), Lead.callback_at != "", Lead.callback_at <= hour_ahead):
            add(agent_id, "callback_soon", "warning", n, f"{n} callback{'s' if n > 1 else ''} due within the hour (first at {first[11:16]})",
                "/leads?view=callbacks", "View callbacks", first)
        for agent_id, n, first, _ in grouped(Lead.callback_at.like(f"{today}%"), Lead.callback_at > hour_ahead):
            add(agent_id, "callback", "info", n, f"{n} more callback{'s' if n > 1 else ''} later today", "/leads?view=callbacks", None, first)
        for agent_id, n, _, first in grouped(Lead.meeting_at.like(f"{today}%")):
            add(agent_id, "meeting_today", "success", n, f"{n} meeting{'s' if n > 1 else ''} today (first at {first[11:16] or '—'})",
                "/leads?view=meetings", "Open meetings", first)
        for agent_id, n, _, first in grouped(Lead.meeting_at.like(f"{tomorrow}%")):
            add(agent_id, "meeting_tomorrow", "info", n, f"{n} meeting{'s' if n > 1 else ''} tomorrow: confirm by call today",
                "/leads?view=meetings", "Call to confirm", first)
        for agent_id, n, _, _ in grouped(Lead.phone.like("+91%"), func.length(Lead.phone) != 13, Lead.do_not_call.is_(False)):
            add(agent_id, "invalid_phone", "danger", n, f"{n} lead{'s' if n > 1 else ''} with an incomplete phone number: calls fail",
                "/leads?view=attention", "Fix numbers")
        for agent_id, n, _, _ in grouped(Lead.retry_count >= 3, Lead.do_not_call.is_(False),
                                         or_(Lead.call_status.is_(None), Lead.call_status.notin_(("Queued", "Ringing", "In Progress")))):
            add(agent_id, "unreachable", "warning", n, f"{n} lead{'s' if n > 1 else ''} unreachable after 3+ tries: try WhatsApp or email",
                "/leads?view=attention", "Review")
        for agent_id, n, _, _ in grouped(Lead.source == "inbound call", or_(Lead.name.is_(None), Lead.name == ""), Lead.do_not_call.is_(False)):
            add(agent_id, "caller_unknown", "warning", n, f"{n} inbound caller{'s' if n > 1 else ''} still without a name: call back to complete",
                "/leads?view=new_callers", "Complete details")
        for agent_id, n, _, _ in grouped(Lead.status.in_(("Interested", "Follow Up")), Lead.do_not_call.is_(False),
                                         or_(Lead.last_contacted_at.is_(None), Lead.last_contacted_at < datetime.utcnow() - timedelta(days=3)),
                                         or_(Lead.meeting_at.is_(None), Lead.meeting_at == "")):
            add(agent_id, "warm_idle", "info", n, f"{n} warm lead{'s' if n > 1 else ''} not contacted in 3+ days",
                "/leads?view=hot_uncalled", "Follow up")
    order = {"danger": 0, "warning": 1, "success": 2, "info": 3}
    return sorted(items, key=lambda i: (order[i["level"]], i.get("when") or ""))


def _routing() -> list[dict]:
    cached = store.get_json("alerts:inbound")
    if cached is None:
        cached = []
        try:
            from app.services.plivo_service import PlivoService
            status = PlivoService().inbound_status()
            if status.get("app_name") == PlivoService.INBOUND_APP and not status.get("connected"):
                cached = [{"key": "inbound", "agent_id": None, "agent": None, "kind": "inbound", "level": "danger", "count": 1,
                           "to": "/settings", "action": "Reconnect", "when": None,
                           "text": "Inbound calls are broken: the public URL changed. Reconnect the Plivo number."}]
        except Exception:  # noqa: BLE001
            cached = []
        store.set_json("alerts:inbound", cached, ttl=CREDITS_TTL)
    return cached


def summary(force: bool = False, unlocked: list[int] | None = None) -> dict:
    if unlocked is not None:
        balances = {"providers": [], "checked_at": int(time.time())}
        items = [i for i in reminders() if i["agent_id"] in unlocked]
    else:
        balances = credits(force)
        items = _routing() + reminders()
        for p in balances["providers"]:
            if p["level"] in ("low", "critical"):
                items.insert(0, {"key": f"credit:{p['provider']}:{p['level']}", "agent_id": None, "agent": None, "kind": "credit",
                                 "level": "danger" if p["level"] == "critical" else "warning", "count": 1, "to": p["action"]["url"] if p.get("action") else "/settings",
                                 "external": True, "action": p["action"]["label"] if p.get("action") else None, "when": None,
                                 "text": f"{p['provider']}: {p['value']} · {p['detail']}"})
    snoozed = _snoozed()
    visible = [i for i in items if i["key"] not in snoozed]
    popup = next((p for p in balances["providers"] if p["level"] == "critical" and f"popup:{p['provider']}" not in snoozed), None)
    return {
        "items": visible, "snoozed": len(items) - len(visible), "balances": balances["providers"], "checked_at": balances["checked_at"],
        "attention": sum(1 for i in visible if i["level"] in ("danger", "warning")),
        "popup": popup,
    }
