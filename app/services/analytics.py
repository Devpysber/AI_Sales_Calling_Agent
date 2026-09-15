"""
Management analytics: period KPIs with comparison, trends, best calling hours and funnel.
All day/hour bucketing is in IST.
"""

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import Integer, func, select

from app.core.database import get_db
from app.models.call import Call
from app.models.lead import Lead

IST = timezone(timedelta(hours=5, minutes=30))
OFFSET = timedelta(hours=5, minutes=30)
ANSWERED = "Completed"
UNREACHED = ("No Answer", "Busy", "Failed", "Canceled")
PIPELINE = ["New", "Contacted", "Interested", "Follow Up", "Meeting Booked", "Closed Won"]


def _kpis(rows) -> dict:
    total = len(rows)
    connected = [r for r in rows if r.status == ANSWERED]
    talk = sum(r.duration or 0 for r in connected)
    meetings = sum(r.outcome == "meeting_booked" for r in rows)
    latencies = [r.avg_latency_ms for r in rows if r.avg_latency_ms]
    return {
        "calls": total,
        "connected": len(connected),
        "connect_rate": round(100 * len(connected) / total, 1) if total else None,
        "talk_seconds": talk,
        "avg_duration": round(talk / len(connected)) if connected else None,
        "meetings": meetings,
        "meeting_rate": round(100 * meetings / len(connected), 1) if connected else None,
        "hot": sum(r.qualification == "Hot" for r in rows),
        "avg_latency_ms": round(sum(latencies) / len(latencies)) if latencies else None,
    }


def usage(rows) -> dict:
    """Billable usage and an estimate from the rates in settings (0 = not set)."""
    from app.core.config import settings

    metered = [r for r in rows if r.tts_chars is not None]
    tts = sum(r.tts_chars or 0 for r in metered)
    stt = sum(r.stt_seconds or 0 for r in metered)
    llm = sum(r.llm_requests or 0 for r in metered)
    connected_minutes = sum((r.duration or 0) for r in rows if r.status == ANSWERED) / 60
    cost = {
        "telephony": connected_minutes * settings.cost_per_call_minute,
        "tts": tts / 10_000 * settings.cost_per_10k_tts_chars,
        "stt": stt / 3600 * settings.cost_per_stt_hour,
        "llm": llm * settings.cost_per_llm_request,
    }
    answered = sum(r.status == ANSWERED for r in metered) or 0
    total = sum(cost.values())
    return {
        "metered_calls": len(metered), "tts_chars": tts, "stt_seconds": round(stt), "llm_requests": llm,
        "call_minutes": round(connected_minutes, 1),
        "cost": {k: round(v, 2) for k, v in cost.items()}, "total_cost": round(total, 2),
        "cost_per_connected_call": round(total / answered, 2) if answered else None,
        "rates_configured": any((settings.cost_per_call_minute, settings.cost_per_10k_tts_chars,
                                 settings.cost_per_stt_hour, settings.cost_per_llm_request)),
        "currency": settings.cost_currency,
    }


def report(agent_id: int, days: int = 30) -> dict:
    today = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0)
    start = (today - timedelta(days=days - 1) - OFFSET).replace(tzinfo=None)
    prev_start = start - timedelta(days=days)
    mine = Lead.agent_id == agent_id

    with get_db() as db:
        rows = db.execute(
            select(Call.created_at, Call.status, Call.duration, Call.outcome, Call.qualification, Call.sentiment,
                   Call.trigger, Call.hangup_cause, Call.error, Call.avg_latency_ms,
                   Call.tts_chars, Call.stt_seconds, Call.llm_requests)
            .where(Call.agent_id == agent_id, Call.created_at >= prev_start)
        ).all()
        by_status = dict(db.execute(select(Lead.status, func.count()).where(mine).group_by(Lead.status)).all())
        sources = db.execute(
            select(Lead.source, func.count(), func.sum((Lead.meeting_at.is_not(None) & (Lead.meeting_at != "")).cast(Integer)),
                   func.sum((Lead.qualification == "Hot").cast(Integer)))
            .where(mine).group_by(Lead.source)
        ).all()
        total_leads = db.scalar(select(func.count(Lead.id)).where(mine)) or 0
        new_leads = db.scalar(select(func.count(Lead.id)).where(mine, Lead.created_at >= start)) or 0

    current = [r for r in rows if r.created_at >= start]
    previous = [r for r in rows if r.created_at < start]

    series = {}
    for d in range(days):
        key = (today - timedelta(days=days - 1 - d)).strftime("%Y-%m-%d")
        series[key] = {"date": key, "calls": 0, "connected": 0, "meetings": 0, "talk_seconds": 0}
    heat = defaultdict(lambda: {"calls": 0, "connected": 0})
    triggers = defaultdict(lambda: {"calls": 0, "connected": 0, "meetings": 0})
    failures = Counter()

    for r in current:
        local = r.created_at + OFFSET
        day = series.get(local.strftime("%Y-%m-%d"))
        ok = r.status == ANSWERED
        if day:
            day["calls"] += 1
            day["connected"] += ok
            day["meetings"] += r.outcome == "meeting_booked"
            day["talk_seconds"] += (r.duration or 0) if ok else 0
        cell = heat[(local.weekday(), local.hour)]
        cell["calls"] += 1
        cell["connected"] += ok
        t = triggers[r.trigger or "manual"]
        t["calls"] += 1
        t["connected"] += ok
        t["meetings"] += r.outcome == "meeting_booked"
        if r.status in UNREACHED:
            failures[r.status if r.status != "Failed" else (r.hangup_cause or r.error or "Failed")[:80]] += 1

    reached = sum(by_status.get(s, 0) for s in by_status if s != "New")
    funnel = [
        {"stage": "Leads", "count": total_leads},
        {"stage": "Contacted", "count": reached},
        {"stage": "Interested", "count": sum(by_status.get(s, 0) for s in ("Interested", "Follow Up", "Meeting Booked", "Closed Won"))},
        {"stage": "Meeting booked", "count": sum(by_status.get(s, 0) for s in ("Meeting Booked", "Closed Won"))},
        {"stage": "Won", "count": by_status.get("Closed Won", 0)},
    ]

    return {
        "days": days,
        "kpis": _kpis(current),
        "usage": usage(current),
        "previous": _kpis(previous),
        "new_leads": new_leads,
        "series": list(series.values()),
        "heatmap": [{"weekday": w, "hour": h, **v} for (w, h), v in sorted(heat.items())],
        "outcomes": dict(Counter(r.outcome for r in current if r.outcome)),
        "qualification": dict(Counter(r.qualification for r in current if r.qualification)),
        "sentiment": dict(Counter(r.sentiment for r in current if r.sentiment)),
        "triggers": [{"trigger": k, **v} for k, v in sorted(triggers.items(), key=lambda kv: -kv[1]["calls"])],
        "failures": [{"reason": k, "count": v} for k, v in failures.most_common(6)],
        "funnel": funnel,
        "pipeline": {s: by_status.get(s, 0) for s in PIPELINE},
        "sources": sorted(
            [{"source": s or "unknown", "leads": n, "meetings": int(m or 0), "hot": int(h or 0)} for s, n, m, h in sources],
            key=lambda x: -x["leads"])[:10],
    }

