"""
Agent workspaces.

Every agent owns its persona, voice, phone number, automation schedule,
knowledge base, leads, calls and activity. All data queries elsewhere are
filtered by agent_id so workspaces never mix.
"""

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, func, select

from app.core import store
from app.core.config import settings
from app.core.database import get_db
from app.models.agent import Agent
from app.models.call import Call
from app.models.document import Document, DocumentChunk
from app.models.event import Event
from app.models.lead import Lead
from app.services import events
from app.services.settings_service import SettingsService, coerce

IST = timezone(timedelta(hours=5, minutes=30))
CACHE_TTL = 10
ACTIVE_CALL = ("Queued", "Ringing", "In Progress")
COLORS = ["#5b4bf5", "#0e8a5e", "#d9480f", "#1f6feb", "#c2255c", "#7048e8", "#0b7285", "#b36b00"]

AUTOMATION_DEFAULTS = {
    "auto_dial_enabled": False,
    "auto_dial_interval_minutes": 5,
    "max_calls_per_run": 5,
    "retry_enabled": False,
    "retry_interval_minutes": 15,
    "retry_min_gap_minutes": 60,
    "max_retries": 3,
    "calling_hours_start": 9,
    "calling_hours_end": 21,
    "calling_days": [0, 1, 2, 3, 4, 5],
    "max_concurrent_calls": 3,
    "meeting_reminder_enabled": False,
    "meeting_reminder_hour": 9,
    "daily_report_enabled": False,
    "daily_report_hour": 18,
    "daily_report_email": "",
    # Website leads: call a new enquiry within seconds of the form being submitted
    "speed_to_lead_enabled": False,
    "speed_to_lead_min_seconds": 60,   # random delay before the call, so it does not feel robotic
    "speed_to_lead_max_seconds": 120,
    # Nurture: call Interested / Follow Up leads again when nobody has spoken to them for a while
    "nurture_enabled": False,
    "nurture_after_days": 3,
    "nurture_max_attempts": 2,
}

LANGUAGE_CODES = ("en-IN", "hi-IN", "bn-IN", "ta-IN", "te-IN", "kn-IN", "ml-IN", "mr-IN", "gu-IN", "pa-IN", "od-IN")

PROFILE_DEFAULTS = {
    "agent_name": "Ashish",
    "company_name": "Psyber Technologies",
    "company_tagline": "",
    "website_url": "",                 # the site this agent handles leads for
    "voice_speaker": "rahul",
    "default_language": "en-IN",
    "objective": "Understand the prospect's business, explain how our services help, and book a discovery meeting with our team.",
    "call_to_action": "Book a 30-minute discovery call with our solutions team.",
    "greeting_en": "Hi {name}, this is {agent} calling from {company}. Is this a good time to talk for a minute?",
    "greeting_hi": "नमस्ते {name}, मैं {company} से {agent} बोल रहा हूँ। क्या अभी एक मिनट बात कर सकते हैं?",
    "instructions": (
        "Be warm, confident and concise, like a senior sales consultant on a phone call.\n"
        "Ask one question at a time and listen. Qualify on need, timeline, budget and decision maker.\n"
        "If the prospect is busy, offer a callback time. If not interested, thank them and end politely."
    ),
    "objection_handling": (
        "Price: focus on outcomes and ROI, offer a tailored quote after a short discovery call.\n"
        "Already have a vendor: ask what they would improve, position a no-obligation comparison.\n"
        "Send details by email: agree, confirm the email address, and still propose a short call."
    ),
    "qualification_criteria": "Hot: clear need and wants a meeting or proposal. Warm: interested but no commitment. Cold: no need or not interested.",
    "forbidden_topics": "Never promise discounts, delivery dates or features that are not in the knowledge base.",
    "max_call_minutes": 8,
    # Call routing: a human number to hand callers to
    "transfer_number": "",
    "inbound_mode": "ai",              # ai | forward (ring the transfer number directly)
    "transfer_on_request": True,       # AI hands over when the caller asks for a person
    "after_hours_mode": "ai",          # ai | forward | message (outside the automation calling window)
    "after_hours_message": "",
    "forward_fallback": "ai",          # ai (agent takes the call) | message, when the team does not pick up
    "notify_missed_calls": True,       # email the admin when a forwarded call is missed
    "inbound_collect": ["name", "requirement", "city"],  # details the agent asks new callers for, in order
    "record_calls": False,
    "detect_voicemail": False,
}

DEFAULTS = {"profile": PROFILE_DEFAULTS, "automation": AUTOMATION_DEFAULTS}
META_FIELDS = ("name", "description", "color", "phone_number", "status")


class AgentNotFound(LookupError):
    pass


def phone_digits(value: str | None) -> str:
    """Digits with country code. A bare 10-digit Indian mobile (6-9…) or 0-prefixed number gets 91."""
    digits = "".join(c for c in (value or "") if c.isdigit())
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in "6789":
        digits = "91" + digits
    return digits


def normalize_number(value: str | None) -> str | None:
    digits = "".join(c for c in (value or "") if c.isdigit())
    return f"+{digits}" if digits else None


def _clean_meta(data: dict) -> dict:
    meta = {k: data[k] for k in META_FIELDS if k in data}
    if "name" in meta:
        meta["name"] = (meta["name"] or "").strip()
        if not meta["name"]:
            raise ValueError("Agent name is required.")
        meta["name"] = meta["name"][:255]
    if "phone_number" in meta:
        meta["phone_number"] = normalize_number(meta["phone_number"])
    if "status" in meta and meta["status"] not in ("active", "paused"):
        raise ValueError("Status must be active or paused.")
    if "color" in meta and not (isinstance(meta["color"], str) and meta["color"].startswith("#") and len(meta["color"]) in (4, 7)):
        raise ValueError("Color must be a hex value like #5b4bf5.")
    return meta


def exists(agent_id: int) -> bool:
    with get_db() as db:
        return db.get(Agent, agent_id) is not None


def get(agent_id: int) -> dict | None:
    with get_db() as db:
        agent = db.get(Agent, agent_id)
        return agent.to_dict() if agent else None


def require(agent_id: int) -> dict:
    agent = get(agent_id)
    if not agent:
        raise AgentNotFound(f"Agent {agent_id} not found.")
    return agent


def ids(active_only: bool = False) -> list[int]:
    with get_db() as db:
        query = select(Agent.id).order_by(Agent.id)
        if active_only:
            query = query.where(Agent.status == "active")
        return list(db.scalars(query))


def list_agents() -> list[dict]:
    today = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0)
    today_utc = (today - timedelta(hours=5, minutes=30)).replace(tzinfo=None)
    with get_db() as db:
        agents = [a.to_dict() for a in db.scalars(select(Agent).order_by(Agent.id))]

        def per_agent(query):
            return dict(db.execute(query).all())

        leads = per_agent(select(Lead.agent_id, func.count(Lead.id)).group_by(Lead.agent_id))
        hot = per_agent(select(Lead.agent_id, func.count(Lead.id)).where(Lead.qualification == "Hot").group_by(Lead.agent_id))
        meetings = per_agent(select(Lead.agent_id, func.count(Lead.id))
                             .where(Lead.meeting_at.is_not(None), Lead.meeting_at != "").group_by(Lead.agent_id))
        calls_today = per_agent(select(Call.agent_id, func.count(Call.id)).where(Call.created_at >= today_utc).group_by(Call.agent_id))
        connected_today = per_agent(select(Call.agent_id, func.count(Call.id))
                                    .where(Call.created_at >= today_utc, Call.status == "Completed").group_by(Call.agent_id))
        live = per_agent(select(Call.agent_id, func.count(Call.id)).where(Call.status.in_(ACTIVE_CALL)).group_by(Call.agent_id))
        documents = per_agent(select(Document.agent_id, func.count(Document.id)).group_by(Document.agent_id))
        last_call = per_agent(select(Call.agent_id, func.max(Call.created_at)).group_by(Call.agent_id))
        customized = {a.id: bool(a.stored("profile")) for a in db.scalars(select(Agent))}

    from app.services.call_service import within_calling_hours

    for agent in agents:
        aid = agent["id"]
        profile = get_profile(aid)
        automation = get_automation(aid)
        automation_on = automation["auto_dial_enabled"] or automation["retry_enabled"]
        agent.update({
            "within_calling_hours": within_calling_hours(automation),
            "automation_on": automation_on,
            "setup": {"persona": customized.get(aid, False), "knowledge": documents.get(aid, 0) > 0,
                      "leads": leads.get(aid, 0) > 0, "number": bool(agent["phone_number"] or settings.plivo_phone_number), "automation": automation_on},
            "persona": {k: profile[k] for k in ("agent_name", "company_name", "voice_speaker", "default_language")},
            "stats": {
                "leads": leads.get(aid, 0), "hot": hot.get(aid, 0), "meetings": meetings.get(aid, 0),
                "calls_today": calls_today.get(aid, 0), "connected_today": connected_today.get(aid, 0),
                "live": live.get(aid, 0), "documents": documents.get(aid, 0),
                "last_call_at": last_call[aid].isoformat(timespec="seconds") + "Z" if last_call.get(aid) else None,
            },
        })
    return agents


def create(data: dict, actor: str = "admin") -> dict:
    meta = _clean_meta({"name": data.get("name"), **{k: data[k] for k in META_FIELDS if k in data and k != "name"}})
    profile = coerce(PROFILE_DEFAULTS, {k: v for k, v in (data.get("profile") or {}).items() if v not in (None, "")})
    copy_from = data.get("copy_from")
    automation = {}
    if copy_from:
        profile = {**get_profile(int(copy_from)), **profile}
        automation = get_automation(int(copy_from))
        automation["auto_dial_enabled"] = automation["retry_enabled"] = False
    with get_db() as db:
        count = db.scalar(select(func.count(Agent.id))) or 0
        agent = Agent(**{"color": COLORS[count % len(COLORS)], "status": "active", **meta},
                      profile=json.dumps(profile, ensure_ascii=False), automation=json.dumps(automation))
        db.add(agent)
        db.flush()
        result = agent.to_dict()
    events.record("agent.created", f"Agent created: {result['name']}", agent_id=result["id"], actor=actor)
    return result


def update(agent_id: int, data: dict, actor: str = "admin") -> dict:
    meta = _clean_meta(data)
    with get_db() as db:
        agent = db.get(Agent, agent_id)
        if not agent:
            raise AgentNotFound(f"Agent {agent_id} not found.")
        changed = [k for k, v in meta.items() if getattr(agent, k) != v]
        for key, value in meta.items():
            setattr(agent, key, value)
        result = agent.to_dict()
    if changed:
        events.record("settings.updated", "Agent details updated", ", ".join(changed), agent_id=agent_id, actor=actor)
    return result


def delete(agent_id: int, actor: str = "admin") -> bool:
    with get_db() as db:
        agent = db.get(Agent, agent_id)
        if not agent:
            return False
        name = agent.name
        doc_ids = select(Document.id).where(Document.agent_id == agent_id)
        db.query(DocumentChunk).filter(DocumentChunk.document_id.in_(doc_ids)).delete(synchronize_session=False)
        for model in (Event, Call, Document, Lead):
            db.query(model).filter(model.agent_id == agent_id).delete(synchronize_session=False)
        db.delete(agent)
    for group in DEFAULTS:
        store.delete(_cache_key(agent_id, group))
    SettingsService().delete_state(f"last_run.{agent_id}.")
    events.record("agent.deleted", f"Agent deleted: {name}", actor=actor)
    return True


# ---------------- profile & automation ----------------

def _cache_key(agent_id: int, group: str) -> str:
    return f"agent:{agent_id}:{group}"


def _get_group(agent_id: int, group: str) -> dict:
    defaults = DEFAULTS[group]
    cached = store.get_json(_cache_key(agent_id, group))
    if cached is None:
        with get_db() as db:
            agent = db.get(Agent, agent_id)
            if not agent:
                raise AgentNotFound(f"Agent {agent_id} not found.")
            cached = agent.stored(group)
        store.set_json(_cache_key(agent_id, group), cached, ttl=CACHE_TTL)
    return {**defaults, **{k: v for k, v in cached.items() if k in defaults}}


def _update_group(agent_id: int, group: str, values: dict, label: str, actor: str) -> dict:
    clean = coerce(DEFAULTS[group], values)
    with get_db() as db:
        agent = db.get(Agent, agent_id)
        if not agent:
            raise AgentNotFound(f"Agent {agent_id} not found.")
        before = {**DEFAULTS[group], **agent.stored(group)}
        setattr(agent, group, json.dumps({**agent.stored(group), **clean}, ensure_ascii=False))
    store.delete(_cache_key(agent_id, group))
    changed = [k for k, v in clean.items() if before.get(k) != v]
    if changed:
        events.record("settings.updated", f"{label} updated", ", ".join(changed), agent_id=agent_id, actor=actor)
    return _get_group(agent_id, group)


def get_profile(agent_id: int) -> dict:
    return _get_group(agent_id, "profile")


def update_profile(agent_id: int, values: dict, actor: str = "admin") -> dict:
    values = dict(values)
    if "transfer_number" in values:
        digits = phone_digits(str(values["transfer_number"] or ""))
        if digits and not 11 <= len(digits) <= 15:
            raise ValueError("Transfer number must be a full phone number with country code, e.g. +91 98765 43210.")
        values["transfer_number"] = f"+{digits}" if digits else ""
    for key, allowed in (("inbound_mode", ("ai", "forward")), ("after_hours_mode", ("ai", "forward", "message")),
                         ("forward_fallback", ("ai", "message"))):
        if key in values and values[key] not in allowed:
            raise ValueError(f"{key} must be one of: {', '.join(allowed)}")
    if values.get("inbound_mode") == "forward" or values.get("after_hours_mode") == "forward":
        number = values.get("transfer_number", get_profile(agent_id).get("transfer_number"))
        if not number:
            raise ValueError("Add a transfer number before forwarding calls to it.")
    return _update_group(agent_id, "profile", values, "Agent profile", actor)


def get_automation(agent_id: int) -> dict:
    return _get_group(agent_id, "automation")


def update_automation(agent_id: int, values: dict, actor: str = "admin") -> dict:
    return _update_group(agent_id, "automation", values, "Automation settings", actor)


# ---------------- telephony ----------------

def caller_id(agent_id: int | None) -> str:
    """Digits of the number this agent dials from."""
    number = (get(agent_id) or {}).get("phone_number") if agent_id else None
    return "".join(c for c in (number or settings.plivo_phone_number) if c.isdigit())


def for_inbound(to_number: str, lead_agent_id: int | None = None) -> int | None:
    """Route an inbound call: the agent owning the dialled number, else the caller's agent, else the first active agent."""
    number = normalize_number(to_number)
    with get_db() as db:
        if number:
            owner = db.scalar(select(Agent.id).where(Agent.phone_number == number).order_by(Agent.id))
            if owner:
                return owner
        if lead_agent_id and db.get(Agent, lead_agent_id):
            return lead_agent_id
        return db.scalar(select(Agent.id).order_by((Agent.status != "active"), Agent.id))


# ---------------- cross-agent overview ----------------

PIPELINE_STAGES = ["New", "Contacted", "Interested", "Follow Up", "Meeting Booked", "Closed Won"]


def overview(days: int = 14) -> dict:
    """Everything the all-agents home needs in one request: per-agent trends, setup health, live calls and recent activity."""
    today = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = (today - timedelta(days=days - 1) - timedelta(hours=5, minutes=30)).replace(tzinfo=None)
    dates = [(today - timedelta(days=days - 1 - d)).strftime("%Y-%m-%d") for d in range(days)]

    with get_db() as db:
        rows = db.execute(select(Call.agent_id, Call.created_at, Call.status, Call.duration, Call.outcome)
                          .where(Call.created_at >= start_utc)).all()
        stages = db.execute(select(Lead.agent_id, Lead.status, func.count(Lead.id)).group_by(Lead.agent_id, Lead.status)).all()
        now_ist = datetime.now(IST).strftime("%Y-%m-%d %H:%M")
        today_str = now_ist[:10]
        queue = dict(db.execute(select(Lead.agent_id, func.count()).where(Lead.call_status == "Pending", Lead.do_not_call.is_(False))
                                .group_by(Lead.agent_id)).all())
        callbacks = dict(db.execute(select(Lead.agent_id, func.count()).where(Lead.callback_at.like(f"{today_str}%"), Lead.callback_at >= now_ist)
                                    .group_by(Lead.agent_id)).all())
        next_meeting = dict(db.execute(select(Lead.agent_id, func.min(Lead.meeting_at)).where(Lead.meeting_at >= now_ist)
                                       .group_by(Lead.agent_id)).all())
        attention = dict(db.execute(select(Lead.agent_id, func.count()).where(Lead.do_not_call.is_(False), or_(
            Lead.retry_count >= 3, Lead.phone.like("+91%") & (func.length(Lead.phone) != 13))).group_by(Lead.agent_id)).all())
        today_utc = (today - timedelta(hours=5, minutes=30)).replace(tzinfo=None)
        inbound_today = dict(db.execute(select(Call.agent_id, func.count()).where(Call.direction == "inbound", Call.created_at >= today_utc)
                                        .group_by(Call.agent_id)).all())
        live = db.execute(select(Call, Lead.name).outerjoin(Lead, Lead.id == Call.lead_id)
                          .where(Call.status.in_(ACTIVE_CALL)).order_by(Call.id.desc()).limit(20)).all()
        live_calls = [{**c.to_dict(n, with_transcript=False)} for c, n in live]

    agents_list = list_agents()
    series = {a["id"]: {d: {"date": d, "calls": 0, "connected": 0, "meetings": 0} for d in dates} for a in agents_list}
    talk = {a["id"]: 0 for a in agents_list}
    for agent_id, created, status, duration, outcome in rows:
        day = (created + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
        bucket = series.get(agent_id, {}).get(day)
        if bucket is None:
            continue
        bucket["calls"] += 1
        bucket["connected"] += status == "Completed"
        bucket["meetings"] += outcome == "meeting_booked"
        if status == "Completed":
            talk[agent_id] += duration or 0

    pipeline = {a["id"]: {s: 0 for s in PIPELINE_STAGES} for a in agents_list}
    for agent_id, status, count in stages:
        if agent_id in pipeline and status in pipeline[agent_id]:
            pipeline[agent_id][status] = count

    for a in agents_list:
        aid = a["id"]
        points = list(series[aid].values())
        calls = sum(p["calls"] for p in points)
        connected = sum(p["connected"] for p in points)
        a.update({
            "series": points,
            "period": {"calls": calls, "connected": connected, "meetings": sum(p["meetings"] for p in points),
                       "connect_rate": round(100 * connected / calls, 1) if calls else None, "talk_seconds": talk[aid]},
            "pipeline": pipeline[aid],
            "ops": {
                "queue": queue.get(aid, 0), "callbacks_today": callbacks.get(aid, 0), "next_meeting": next_meeting.get(aid),
                "needs_attention": attention.get(aid, 0), "inbound_today": inbound_today.get(aid, 0),
                "caller_id": "+" + caller_id(aid), "number_is_default": not a.get("phone_number"),
            },
        })

    names = {a["id"]: a["name"] for a in agents_list}
    for call in live_calls:
        call["agent_name"] = names.get(call["agent_id"])
    activity = [{**e, "agent_name": names.get(e["agent_id"])} for e in events.list_events(None, limit=15) if e["agent_id"] in names]
    totals = [{"date": d, "calls": sum(series[a][d]["calls"] for a in series), "connected": sum(series[a][d]["connected"] for a in series)}
              for d in dates]
    return {"agents": agents_list, "series": totals, "live_calls": live_calls, "activity": activity, "days": days}
