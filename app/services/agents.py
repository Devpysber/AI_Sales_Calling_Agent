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
    "retry_min_gap_minutes": 180,   # an unanswered number is not rung again within three hours
    "max_retries": 2,               # two more tries, then it waits for the customer or a scheduled call
    "calling_hours_start": 9,
    "calling_hours_end": 21,
    "calling_days": [0, 1, 2, 3, 4, 5],
    "max_concurrent_calls": 3,
    "ai_auto_emails": True,
    "meeting_reminder_enabled": False,
    "meeting_reminder_hour": 9,
    "daily_report_enabled": False,
    "daily_report_hour": 18,
    "daily_report_email": "",
    # Website leads: call a new enquiry within seconds of the form being submitted
    "speed_to_lead_enabled": False,
    "speed_to_lead_min_seconds": 3600,   # a random 1-2 hours after the form: prompt, not pouncing
    "speed_to_lead_max_seconds": 7200,
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
    # What this agent is to the person on the line. A clinic reminder agent is not a sales consultant,
    # and its caller is a patient, not a prospect: both appear throughout the prompt.
    "agent_role": "senior sales consultant",
    "customer_noun": "prospect",
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
    "max_call_minutes": 5,
    # Call routing: a human number to hand callers to
    "transfer_number": "",
    "team_members": [],
    "inbound_mode": "ai",              # ai | forward (ring the transfer number directly)
    "transfer_on_request": True,       # AI hands over when the caller asks for a person
    "after_hours_mode": "ai",          # ai | forward | message (outside the automation calling window)
    "after_hours_message": "",
    "forward_fallback": "ai",          # ai (agent takes the call) | message, when the team does not pick up
    "notify_missed_calls": True,       # email the admin when a forwarded call is missed
    "inbound_collect": ["name", "requirement", "city"],  # details the agent asks new callers for, in order
    "record_calls": False,
    "detect_voicemail": False,
    "agent_password": "",
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


DESKS_KEY = "desks"


def desks() -> list[dict]:
    """Every active agent as a desk a caller could mean: id, company, agent name, tagline. Cached a minute in the shared store; read per live turn."""
    rows = store.get_json(DESKS_KEY)
    if rows is None:
        rows = []
        for aid in ids(active_only=True):
            try:
                p = get_profile(aid)
            except Exception:  # noqa: BLE001 - a broken profile is not a desk to offer
                continue
            rows.append({"id": aid, "company_name": p.get("company_name") or "", "agent_name": p.get("agent_name") or "",
                         "tagline": (p.get("company_tagline") or "").strip()})
        store.set_json(DESKS_KEY, rows, ttl=60)
    return rows


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


def created_count(created_by: str) -> int:
    """How many workspaces this team member has created, for their agent limit."""
    with get_db() as db:
        return db.scalar(select(func.count(Agent.id)).where(Agent.created_by == created_by)) or 0


def _assert_unique(db, meta: dict, exclude_id: int | None = None) -> None:
    """
    One name and one number per agent. Two agents called the same thing are indistinguishable in the
    switcher, reports and emails; two agents on the same number make inbound routing a coin toss.
    """
    if meta.get("name"):
        clash = db.scalar(select(Agent.id).where(func.lower(Agent.name) == meta["name"].lower(), Agent.id != (exclude_id or 0)))
        if clash:
            raise ValueError(f'An agent called "{meta["name"]}" already exists. Pick a different name.')
    # Numbers may be shared: several agents dial out from one line; inbound on it goes to the agent
    # designated with set_inbound_owner() (see for_inbound).


def create(data: dict, actor: str = "admin", created_by: str | None = None) -> dict:
    meta = _clean_meta({"name": data.get("name"), **{k: data[k] for k in META_FIELDS if k in data and k != "name"}})
    profile = coerce(PROFILE_DEFAULTS, {k: v for k, v in (data.get("profile") or {}).items() if v not in (None, "")})
    copy_from = data.get("copy_from")
    automation = {}
    if copy_from:
        profile = {**get_profile(int(copy_from)), **profile}
        automation = get_automation(int(copy_from))
        automation["auto_dial_enabled"] = automation["retry_enabled"] = False
    with get_db() as db:
        _assert_unique(db, meta)
        count = db.scalar(select(func.count(Agent.id))) or 0
        agent = Agent(**{"color": COLORS[count % len(COLORS)], "status": "active", **meta},
                      created_by=created_by,
                      profile=json.dumps(profile, ensure_ascii=False), automation=json.dumps(automation))
        db.add(agent)
        db.flush()
        result = agent.to_dict()
    store.delete(DESKS_KEY)
    events.record("agent.created", f"Agent created: {result['name']}", agent_id=result["id"], actor=actor)
    return result


def update(agent_id: int, data: dict, actor: str = "admin") -> dict:
    meta = _clean_meta(data)
    with get_db() as db:
        agent = db.get(Agent, agent_id)
        if not agent:
            raise AgentNotFound(f"Agent {agent_id} not found.")
        _assert_unique(db, meta, exclude_id=agent_id)
        changed = [k for k, v in meta.items() if getattr(agent, k) != v]
        for key, value in meta.items():
            setattr(agent, key, value)
        result = agent.to_dict()
    if changed:
        store.delete(DESKS_KEY)
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
    store.delete(DESKS_KEY)
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
    result = {**defaults, **{k: v for k, v in cached.items() if k in defaults and v is not None}}
    
    # Handle corrupted DB state where team_members were saved as strings
    if group == "profile" and "team_members" in result:
        import ast
        clean_members = []
        for m in result["team_members"]:
            if isinstance(m, str):
                try:
                    clean_members.append(ast.literal_eval(m))
                except Exception:
                    pass
            else:
                clean_members.append(m)
        result["team_members"] = clean_members
        
        if not result["team_members"] and result.get("transfer_number"):
            migrated = []
            for part in result["transfer_number"].split(","):
                phone = part.strip()
                if phone:
                    migrated.append({"name": "", "phone": phone, "email": ""})
            result["team_members"] = migrated
        
    return result


def _update_group(agent_id: int, group: str, values: dict, label: str, actor: str) -> dict:
    clean = coerce(DEFAULTS[group], values)
    with get_db() as db:
        agent = db.get(Agent, agent_id)
        if not agent:
            raise AgentNotFound(f"Agent {agent_id} not found.")
        before = {**DEFAULTS[group], **agent.stored(group)}
        setattr(agent, group, json.dumps({**agent.stored(group), **clean}, ensure_ascii=False))
    store.delete(_cache_key(agent_id, group))
    if group == "profile":
        store.delete(DESKS_KEY)
    changed = [k for k, v in clean.items() if before.get(k) != v]
    if changed:
        events.record("settings.updated", f"{label} updated", ", ".join(changed), agent_id=agent_id, actor=actor)
    return _get_group(agent_id, group)


MAX_INBOUND_COLLECT = 4   # every detail is one more question on a paid call; name + need + two more is the ceiling


def _ordered_collect(fields) -> list[str]:
    """Name and need first whatever order was clicked, unknown keys dropped, duplicates removed."""
    from app.services.agent import COLLECT_LABELS
    chosen = list(dict.fromkeys(str(f) for f in (fields or []) if str(f) in COLLECT_LABELS))
    return [f for f in ("name", "requirement") if f in chosen] + [f for f in chosen if f not in ("name", "requirement")]


def get_profile(agent_id: int) -> dict:
    profile = _get_group(agent_id, "profile")
    # A list saved before the cap existed (all nine details on) is trimmed on read: the page and the
    # live call both see at most four, without waiting for someone to re-save the page.
    if isinstance(profile.get("inbound_collect"), list):
        profile["inbound_collect"] = _ordered_collect(profile["inbound_collect"])[:MAX_INBOUND_COLLECT]
    return profile


def update_profile(agent_id: int, values: dict, actor: str = "admin") -> dict:
    values = dict(values)
    if "inbound_collect" in values:
        if not isinstance(values["inbound_collect"], list):
            raise ValueError("inbound_collect must be a list")
        # Name and need first whatever the order clicked, then at most two extras: nine questions is not a call.
        chosen = _ordered_collect(values["inbound_collect"])
        if len(chosen) > MAX_INBOUND_COLLECT:
            raise ValueError(f"Ask new callers for at most {MAX_INBOUND_COLLECT} details; each one is another question on a paid call.")
        values["inbound_collect"] = chosen
    if "team_members" in values:
        if not isinstance(values["team_members"], list):
            raise ValueError("team_members must be a list")
        validated = []
        for m in values["team_members"]:
            if not isinstance(m, dict):
                continue
            digits = phone_digits(str(m.get("phone") or ""))
            if digits:
                if not 11 <= len(digits) <= 15:
                    raise ValueError("Each team member's phone must be a full phone number with country code.")
                validated.append({
                    "name": str(m.get("name") or ""),
                    "phone": f"+{digits}",
                    "email": str(m.get("email") or "")
                })
        values["team_members"] = validated
        # Auto-sync transfer_number for backwards compatibility
        values["transfer_number"] = ",".join(m["phone"] for m in validated)
    elif "transfer_number" in values:
        raw_val = str(values["transfer_number"] or "")
        parts = [p.strip() for p in raw_val.split(",")]
        valid_parts = []
        for p in parts:
            digits = phone_digits(p)
            if digits:
                if not 11 <= len(digits) <= 15:
                    raise ValueError("Each transfer number must be a full phone number with country code, e.g. +91 98765 43210.")
                valid_parts.append(f"+{digits}")
        values["transfer_number"] = ",".join(valid_parts)
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
    # An inverted window (start >= end) or no calling days silently disables every automation; the
    # Inbound page saves each select on change, so this is the only place that can catch it.
    merged = {**get_automation(agent_id), **values}
    start, end = int(merged.get("calling_hours_start", 9)), int(merged.get("calling_hours_end", 21))
    if not (0 <= start <= 23 and 1 <= end <= 24):
        raise ValueError("Calling hours must be between 0 and 24.")
    if start >= end:
        raise ValueError("The opening hour must be earlier than the closing hour.")
    days = merged.get("calling_days")
    if isinstance(days, list) and (not days or any(int(d) not in range(7) for d in days)):
        raise ValueError("Pick at least one calling day.")
    return _update_group(agent_id, "automation", values, "Automation settings", actor)


# ---------------- telephony ----------------

def caller_id(agent_id: int | None) -> str:
    """Digits of the number this agent dials from."""
    number = (get(agent_id) or {}).get("phone_number") if agent_id else None
    return "".join(c for c in (number or settings.plivo_phone_number) if c.isdigit())


INBOUND_OWNER_KEY = "inbound_owner"   # settings state: {"<digits>": agent_id} - who answers calls to that number


def inbound_owner(number: str) -> int | None:
    from app.services.settings_service import SettingsService
    number = normalize_number(number) or ""
    owners = SettingsService().get_state(INBOUND_OWNER_KEY) or {}
    agent_id = owners.get(number)
    return int(agent_id) if agent_id and exists(int(agent_id)) else None


def set_inbound_owner(number: str, agent_id: int | None, actor: str = "admin") -> dict:
    """Designate which agent answers calls to `number`; None clears it (first agent on the number answers)."""
    from app.services.settings_service import SettingsService
    number = normalize_number(number) or ""
    if not number:
        raise ValueError("A number is needed to route inbound calls.")
    svc = SettingsService()
    owners = dict(svc.get_state(INBOUND_OWNER_KEY) or {})
    if agent_id is None:
        owners.pop(number, None)
    else:
        if not exists(agent_id):
            raise AgentNotFound(f"Agent {agent_id} not found.")
        owners[number] = int(agent_id)
    svc.set_state(INBOUND_OWNER_KEY, owners)
    events.record("settings.updated", "Inbound routing updated", f"+{number} answered by agent {agent_id or '(default)'}",
                  agent_id=agent_id, actor=actor)
    return {"number": number, "agent_id": agent_id}


def inbound_choices(agent_ids: list[int]) -> list[dict]:
    """
    What a caller known to several agents can be asked to choose between. Each agent is named by what
    tells them apart on the phone: the company when the agents serve different companies, else the agent.
    """
    rows = []
    for aid in agent_ids:
        try:
            p = get_profile(aid)
        except Exception:  # noqa: BLE001 - a deleted agent is simply not offered
            continue
        rows.append({"agent_id": aid, "agent_name": p["agent_name"], "company": p["company_name"],
                     "about": (p.get("company_tagline") or p.get("objective") or "").strip()[:120]})
    if len(rows) < 2:
        return []
    distinct_companies = len({r["company"].strip().lower() for r in rows}) == len(rows)
    for r in rows:
        r["label"] = r["company"] if distinct_companies else f"{r['agent_name']} ({r['company']})"
    return rows


def for_inbound(to_number: str, lead_agent_id: int | None = None) -> int | None:
    """
    Route an inbound call: the caller's own agent (the one whose CRM already knows the number) so the
    conversation continues with that agent's persona and knowledge; else the agent designated for the
    dialled number, else an agent whose number it is, else the first active agent.
    """
    number = normalize_number(to_number)
    with get_db() as db:
        if lead_agent_id and db.get(Agent, lead_agent_id):
            return lead_agent_id
        if number:
            designated = inbound_owner(number)
            if designated:
                return designated
            owner = db.scalar(select(Agent.id).where(Agent.phone_number == number).order_by(Agent.id))
            if owner:
                return owner
        return db.scalar(select(Agent.id).order_by((Agent.status != "active"), Agent.id))


# ---------------- cross-agent overview ----------------

PIPELINE_STAGES = ["New", "Contacted", "Interested", "Follow Up", "Meeting Booked", "Closed Won"]


def live_calls(unlocked_ids: list[int] | None = None) -> list[dict]:
    """Calls ringing or in progress across every agent, newest first, with the agent's name.

    The light version of overview()["live_calls"]: the app-wide incoming-call banner polls this
    every few seconds on every page, so it must not rebuild series, pipelines and activity.
    """
    with get_db() as db:
        rows = db.execute(select(Call, Lead.name).outerjoin(Lead, Lead.id == Call.lead_id)
                          .where(Call.status.in_(ACTIVE_CALL)).order_by(Call.id.desc()).limit(20)).all()
        calls = [c.to_dict(n, with_transcript=False) for c, n in rows]
        names = dict(db.execute(select(Agent.id, Agent.name)).all())
    if unlocked_ids is not None:
        calls = [c for c in calls if c["agent_id"] in unlocked_ids]
    for call in calls:
        call["agent_name"] = names.get(call["agent_id"])
    return calls


def overview(days: int = 14, unlocked_ids: list[int] | None = None) -> dict:
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
                          .where(Call.status.in_(ACTIVE_CALL)).order_by(Call.id.desc()).limit(50)).all()
        live_calls = [{**c.to_dict(n, with_transcript=False)} for c, n in live]
        if unlocked_ids is not None:
            live_calls = [c for c in live_calls if c["agent_id"] in unlocked_ids]
            live_calls = live_calls[:20]

    agents_list = list_agents()
    if unlocked_ids is not None:
        agents_list = [a for a in agents_list if a["id"] in unlocked_ids]
        
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
    activity = [{**e, "agent_name": names.get(e["agent_id"])} for e in events.list_events(None, limit=15, agent_ids=list(names))]
    totals = [{"date": d, "calls": sum(series[a][d]["calls"] for a in series), "connected": sum(series[a][d]["connected"] for a in series)}
              for d in dates]
    return {"agents": agents_list, "series": totals, "live_calls": live_calls, "activity": activity, "days": days}
