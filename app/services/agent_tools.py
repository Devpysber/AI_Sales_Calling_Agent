import subprocess
import contextlib
import json
import re
from datetime import datetime, timezone, timedelta
from app.services.notification_service import send_email, email_sent
from app.services.crm_service import CRMService
from app.core.config import settings
from app.core.database import get_db
from app.core.logging import get_logger
from sqlalchemy import text

log = get_logger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))
QUALIFICATIONS = {"Hot", "Warm", "Cold"}


def _to_ist_text(value: str) -> str | None:
    """Parse an ISO-ish datetime (offset optional; naive means IST) into 'YYYY-MM-DD HH:MM' IST text."""
    raw = str(value or "").strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        try:
            dt = datetime.strptime(raw[:16], "%Y-%m-%d %H:%M")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(IST).strftime("%Y-%m-%d %H:%M")

def send_email_tool(to: str, subject: str, body: str, agent_id: int) -> str:
    """Send an email to anyone."""
    try:
        status = send_email(to, subject, body, agent_id=agent_id, actor="ai")
        return f"Email successfully sent to {to}" if email_sent(status) else f"Failed to send email: {status}"
    except Exception as e:
        return f"Failed to send email: {str(e)}"

def check_email_status_tool(agent_id: int, hours: int = 24) -> str:
    """Recent outgoing emails for this agent from the activity log, newest first."""
    from app.services import events
    since = datetime.now(timezone.utc) - timedelta(hours=max(1, min(hours, 24 * 14)))
    rows = [e for e in events.list_events(agent_id, type_prefix="email", limit=50)
            if str(e.get("created_at") or "") >= since.strftime("%Y-%m-%dT%H:%M")]
    if not rows:
        return f"No emails were sent by this agent in the last {hours} hours. Email sending itself is configured and working."
    lines = [f"- {e.get('created_at', '')[:16].replace('T', ' ')}: {e.get('title')} ({e.get('detail') or 'sent'})" for e in rows[:10]]
    failed = sum(1 for e in rows if "fail" in str(e.get("detail") or "").lower())
    return f"{len(rows)} email(s) in the last {hours} hours, {failed} failed.\n" + "\n".join(lines)


def check_records_tool(query: str, agent_id: int) -> str:
    """Query the CRM for past calls, leads, and histories."""
    crm = CRMService(agent_id)
    leads = crm.list_leads(search=query, page=1, page_size=5).get("items", [])
    if not leads:
        return f"No records found for query: {query}"

    result = "Found the following records:\n"
    for lead in leads:
        result += f"- {lead.get('name', 'Unknown')} ({lead.get('phone')}) - Status: {lead.get('status')}\n"
    return result

def recent_calls_tool(agent_id: int, limit: int = 5, lead: str | None = None) -> str:
    """The agent's latest calls with who, direction, duration, result and when: the answer to 'last call?'."""
    from app.services.call_service import ACTIVE, CallService
    limit = max(1, min(int(limit or 5), 10))
    items = [c for c in CallService(agent_id).list_calls(search=lead, page=1, page_size=limit + 2).get("items", [])
             if c.get("status") not in ACTIVE][:limit]
    if not items:
        return "No calls found for this agent." + (f" (search: {lead})" if lead else "")
    lines = []
    for c in items:
        who = c.get("lead_name") or c.get("team_name") or (c.get("from_number") if c.get("direction") == "inbound" else c.get("to_number")) or "unknown"
        seconds = int(c.get("duration") or 0)
        length = f"{seconds // 60}m {seconds % 60}s" if seconds else "not connected"
        when = _to_ist_text(c.get("created_at") or "") or "unknown time"
        summary = (c.get("summary") or "").strip()
        lines.append(f"- {when} IST · {c.get('direction')} · {who} · {c.get('status')} · {length}"
                     + (f" · {c.get('outcome')}" if c.get("outcome") else "") + (f" · {summary[:140]}" if summary else ""))
    return "Latest calls, newest first (the current live call is not listed):\n" + "\n".join(lines)


AUTOMATION_SWITCHES = {
    "auto_dial": ("auto_dial_enabled", "Auto-dialer"), "retry": ("retry_enabled", "Retry calls"),
    "speed_to_lead": ("speed_to_lead_enabled", "Speed to lead"), "nurture": ("nurture_enabled", "Follow-up calls"),
    "meeting_reminder": ("meeting_reminder_enabled", "Meeting reminders"), "daily_report": ("daily_report_enabled", "Daily report"),
    "auto_emails": ("ai_auto_emails", "AI emails"),
}


def _int(v, default: int) -> int:
    """Best-effort int from free text markup can send ('five', '3 calls', '24h')."""
    m = re.search(r"\d+", str(v or ""))
    return int(m.group()) if m else default


def _on_flag(v) -> bool:
    """`on` as the model sends it: a boolean, or "false"/"off"/"band"/"no" quoted as a string, never bool("false")."""
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "yes", "1", "on", "resume", "enable", "start", "chalu")


_ALL_WORDS = re.compile(r"\b(sab|saare|sare|sabhi|all|every|poora|pura)\b|सब|सारे|सभी|पूरा", re.I)
_OFF_WORDS = re.compile(r"\b(off|band|bandh|stop|pause|roko|rok|disable)\b|बंद|रोक|ऑफ", re.I)
_ON_WORDS = re.compile(r"\b(on|chalu|start|resume|enable|shuru)\b|चालू|शुरू|ऑन", re.I)
_SWITCH_WORDS = {
    "auto_dial": re.compile(r"auto.?dial|dialer|डायल", re.I), "retry": re.compile(r"retr(y|ies)|रिट्राई", re.I),
    "speed_to_lead": re.compile(r"speed|website lead|form lead|स्पीड", re.I), "nurture": re.compile(r"nurture|follow.?up|फॉलो", re.I),
    "meeting_reminder": re.compile(r"reminder|रिमाइंडर", re.I), "daily_report": re.compile(r"daily report|report|रिपोर्ट", re.I),
    "auto_emails": re.compile(r"e-?mails?|मेल", re.I),
}
_AUTOMATION_WORD = re.compile(r"automation|ऑटोमेशन|automations", re.I)


def team_quick_action(agent_id: int, text: str) -> tuple[str, str] | None:
    """
    A colleague's automation command with one right answer, run without the model: "saare automations off kar do",
    "auto dial band karo", "retry chalu karo". Returns (tool result, spoken confirmation key) or None when the
    words are not a plain switch command (the model handles anything with a question or a condition in it).
    """
    # Anything with a second request in it goes to the model instead: this path answers the whole turn
    # with one canned line, so "retry band karo AUR Rahul ko kal call karna" used to lose the callback.
    if not text or "?" in text or re.search(
            r"\b(kya|kyu|kyun|why|what|kab|when|agar|if|aur|and|then|phir|call|bhej|bhejna|send|sms|whatsapp"
            r"|message|book|schedule|callback)\b|क्या|क्यों|कब|अगर|और|फिर|कॉल|भेज", text, re.I):
        return None
    on = bool(_ON_WORDS.search(text)) and not _OFF_WORDS.search(text)
    off = bool(_OFF_WORDS.search(text))
    if not (on or off):
        return None
    named = [k for k, rx in _SWITCH_WORDS.items() if rx.search(text)]
    if _ALL_WORDS.search(text) and _AUTOMATION_WORD.search(text):
        named = list(AUTOMATION_SWITCHES)
    elif not named and _AUTOMATION_WORD.search(text):
        named = [k for k in AUTOMATION_SWITCHES if k != "auto_emails"]   # "automation band karo" = the dialling ones
    if not named or _names_another_agent(text, agent_id):
        return None   # "Hairscope ka auto dial band karo": the model routes it with set_agent_automation
    return set_automation_tool(agent_id, named, not off), ("off" if off else "on")


def _names_another_agent(text: str, agent_id: int) -> bool:
    """True when the words name some other workspace, so the switch must not land on this one.

    The admin rings whichever desk owns the number and says "Hairscope ka auto dial band karo": without
    this the dialler of the desk that answered was switched off and the confirmation named neither."""
    low = (text or "").lower()
    from app.services import agents
    try:
        rows = agents.list_agents()
    except Exception:  # noqa: BLE001 - cannot tell: let the model route it rather than guess a workspace
        return True
    return any(name and len(name) >= 3 and name in low
               for r in rows if r.get("id") != agent_id
               for name in (str(r.get("name") or "").strip().lower(),
                            str((r.get("persona") or {}).get("company_name") or "").strip().lower()))


def set_automation_tool(agent_id: int, switches: list[str] | str, on: bool) -> str:
    """Turn one or more of this agent's automations on or off; the only way a colleague's 'pause/resume X' takes effect."""
    from app.services.agents import update_automation
    # sarvam markup hands the list over as one string: "speed_to_lead, auto_dial" / '["auto_dial"]'
    if isinstance(switches, str):
        raw = switches.strip()
        if raw.startswith("["):
            try:
                switches = json.loads(raw)
            except json.JSONDecodeError:
                switches = raw.strip("[]")
        if isinstance(switches, str):
            switches = [n for n in re.split(r"[,\s/]+|\band\b|\baur\b", switches) if n]
    names = [str(n).strip().strip("\"'") for n in (switches or [])]
    known, unknown = {}, []
    for name in names:
        key, label = AUTOMATION_SWITCHES.get(str(name or "").strip().lower(), (None, None))
        (known.__setitem__(key, label) if key else unknown.append(str(name)))
    if not known:
        return f"Failed: unknown automation {', '.join(unknown) or '(none)'}. Choose from: {', '.join(AUTOMATION_SWITCHES)}."
    try:
        update_automation(agent_id, {k: bool(on) for k in known}, actor="team")
    except Exception as e:  # noqa: BLE001 - spoken back as a failure, never as success
        return f"Failed to change {', '.join(known.values())}: {e}"
    done = f"{', '.join(known.values())} now {'on' if on else 'off'}."
    return done + (f" Unknown: {', '.join(unknown)}." if unknown else "")


def today_stats_tool(agent_id: int) -> str:
    """This agent's numbers right now: leads, hot leads, meetings, calls and connections today, live calls."""
    from app.services import agents
    row = next((a for a in agents.list_agents() if a["id"] == agent_id), None)
    if not row:
        return "No stats available for this agent."
    s = row["stats"]
    return (f"Today: {s['calls_today']} calls, {s['connected_today']} connected, {s['live']} live now. "
            f"Overall: {s['leads']} leads, {s['hot']} hot, {s['meetings']} meetings booked, {s['documents']} knowledge documents. "
            f"Automation {'on' if row.get('automation_on') else 'off'}, "
            f"{'within' if row.get('within_calling_hours') else 'outside'} calling hours.")


def check_credits_tool() -> str:
    """What is left with each provider, the way the alerts bell already shows it.

    This used to answer "no billing integration is connected" while the balances were being fetched
    for the web UI a function away, so the commonest question on an admin call — kitna balance bacha
    hai — was answered with the one thing that was certainly untrue.
    """
    try:
        from app.services import alerts
        providers = (alerts.credits(False) or {}).get("providers") or []
    except Exception as e:  # noqa: BLE001 - a balance is never worth failing a live turn over
        log.warning("Credit check failed: %s", e)
        return "I could not read the balances just now."
    lines = []
    for p in providers:
        value = str(p.get("value") or "").strip()
        if not value:
            continue
        # The detail carries decimals ("at 0.00475/min"), so it is taken whole or not at all.
        detail = " ".join(str(p.get("detail") or "").split())
        lines.append(f"{p.get('provider')}: {value}" + (f", {detail}" if 0 < len(detail) <= 60 else ""))
    if not lines:
        return "No provider is reporting a balance right now."
    low = [p.get("provider") for p in providers if p.get("level") in ("low", "critical")]
    return "; ".join(lines) + (f". Running low: {', '.join(low)}." if low else ".")

def update_lead_status_tool(lead_id, new_status: str, agent_id: int, lead: str | None = None) -> str:
    """Update a lead's status in the CRM; the lead may be named by id, name or phone."""
    from app.services.call_service import JOURNEY, EXIT_STAGES
    crm = CRMService(agent_id)
    lead_id, note = resolve_lead(agent_id, lead_id, lead)
    if not lead_id:
        return note
    canon = {s.lower().replace("-", " "): s for s in list(JOURNEY) + list(EXIT_STAGES) + list(QUALIFICATIONS)}
    value = canon.get(str(new_status or "").strip().lower().replace("_", " ").replace("-", " "), "")
    if value in QUALIFICATIONS:
        field = "qualification"
    elif value:
        field = "status"
    else:
        allowed = ", ".join(list(JOURNEY) + sorted(EXIT_STAGES) + sorted(QUALIFICATIONS))
        return f"Invalid status '{new_status}'. Allowed values: {allowed}."
    try:
        crm.update(lead_id, {field: value}, actor="team")
        return f"Successfully updated lead {lead_id} {field} to '{value}'."
    except Exception as e:
        return f"Failed to update lead status: {str(e)}"

def check_agent_schedule_tool(agent_id: int) -> str:
    """Check the calling window schedule for the agent."""
    from app.services.agents import get_automation
    cfg = get_automation(agent_id)
    start = cfg.get("calling_hours_start", 9)
    end = cfg.get("calling_hours_end", 21)
    days = cfg.get("calling_days") or [0, 1, 2, 3, 4, 5]
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    active_days = [day_names[int(d)] for d in days if 0 <= int(d) < 7] or ["None"]
    return f"The agent calling window is from {start}:00 to {end}:00 IST on {', '.join(active_days)}."

def system_diagnostics_tool() -> str:
    """Run basic system diagnostics for the Admin."""
    try:
        out = subprocess.check_output(["uptime"]).decode("utf-8")
        return f"System Diagnostics: {out.strip()}"
    except Exception as e:
        return f"Failed to run diagnostics: {str(e)}"

def resolve_agent(agent: str | int | None, fallback: int) -> tuple[int | None, str]:
    """Admin names an agent by id, name or company ('carsindias', 'Hairscope wala'); nothing named = this agent."""
    from app.services.agents import list_agents
    if agent in (None, "", 0):
        return fallback, ""
    rows = list_agents()
    text = str(agent).strip().lower()
    if text.isdigit():
        return (int(text), "") if any(r["id"] == int(text) for r in rows) else (None, f"Failed: no agent with id {text}.")
    hits = [r for r in rows if text in str(r.get("name") or "").lower() or text in str((r.get("persona") or {}).get("company_name") or "").lower()]
    if len(hits) == 1:
        return hits[0]["id"], ""
    if not hits:
        return None, f"Failed: no agent matches '{agent}'. Agents: " + ", ".join(f"{r['name']} (id {r['id']})" for r in rows)
    return None, f"Failed: '{agent}' matches several agents: " + ", ".join(f"{r['name']} (id {r['id']})" for r in hits)


def all_agents_overview_tool() -> str:
    """Every agent in one line each: today's calls, live, leads, hot, automation state (Admin only)."""
    from app.services.agents import list_agents
    rows = list_agents()
    if not rows:
        return "No agents in the system."
    lines = [f"- {r['name']} (id {r['id']}): {r['stats']['calls_today']} calls today, {r['stats']['live']} live, "
             f"{r['stats']['leads']} leads, {r['stats']['hot']} hot, {r['stats']['meetings']} meetings, "
             f"automation {'on' if r.get('automation_on') else 'off'}" for r in rows]
    return "All agents:\n" + "\n".join(lines)


def list_all_agents_tool() -> str:
    """List all agents in the system."""
    from app.services.agents import list_agents
    agents = list_agents()
    if not agents:
        return "No agents found in the system."
    result = "Active agents in the system:\n"
    for agent in agents:
        result += f"- Agent {agent.get('id')}: {agent.get('name')} (Owner: {agent.get('created_by')})\n"
    return result

def resolve_lead(agent_id: int, lead_id=None, lead: str | None = None) -> tuple[int | None, str]:
    """A lead by id, else by name or phone (the caller says "Sonu Sharma", never an id). (id, note)"""
    crm = CRMService(agent_id)
    if lead_id:
        try:
            found = crm.get(int(lead_id))
            if found:
                return found["id"], ""
        except (TypeError, ValueError):
            pass
    query = str(lead or "").strip() or (str(lead_id).strip() if lead_id and not str(lead_id).strip().isdigit() else "")
    if not query:
        return None, "No lead named. Ask who it is for (name or phone)."
    digits = "".join(c for c in query if c.isdigit())
    if len(digits) >= 10:
        found = crm.find_by_phone(query)
        if found:
            return found["id"], ""
    items = crm.list_leads(search=query, page=1, page_size=5).get("items", [])
    if len(items) == 1:
        return items[0]["id"], ""
    if not items:
        return None, f"No lead matches '{query}'. Ask for the exact name or phone number."
    return None, "Several leads match: " + "; ".join(f"lead_id {i['id']} {i.get('name')} ({i.get('phone')})" for i in items) + ". Ask which one."


def schedule_callback_tool(lead_id, date_time: str, agent_id: int, lead: str | None = None) -> str:
    """Schedule a callback for a lead named by id, name or phone."""
    from app.services.call_service import _valid_callback
    crm = CRMService(agent_id)
    lead_id, note = resolve_lead(agent_id, lead_id, lead)
    if not lead_id:
        return note
    when = _valid_callback(_to_ist_text(date_time))
    if not when:
        return f"Invalid callback time '{date_time}': use 'YYYY-MM-DD HH:MM' in IST, not in the past and within 30 days."
    # The callbacks job skips do-not-call leads, so booking one here was a promise nothing could keep.
    current = crm.get(lead_id) or {}
    if current.get("do_not_call"):
        return (f"Lead {lead_id} is marked Do Not Call, so no callback was scheduled. "
                "Take them off Do Not Call first if they asked us to ring back.")
    if current.get("phone_valid") is False:
        return f"Lead {lead_id} has an incomplete phone number, so no callback was scheduled. Fix the number first."
    # The callbacks job only dials inside calling hours, so a time outside them was booked, never
    # dialled at that time, and reported back as if it were. Book what will actually happen and say so.
    from app.services.call_service import _clamp_callback
    from app.services.agents import get_automation
    try:
        window_cfg = get_automation(agent_id)
    except Exception:  # noqa: BLE001 - no automation row: the clamp's own defaults apply
        window_cfg = {}
    booked, moved = _clamp_callback(when, window_cfg)
    try:
        crm.update(lead_id, {"callback_at": booked, "call_status": "Pending"}, actor="team")
        if moved:
            return (f"{when} IST is outside calling hours, so the callback for lead {lead_id} is booked for "
                    f"{booked} IST instead.")
        return f"Callback scheduled for lead {lead_id} at {booked} IST."
    except Exception as e:
        return f"Failed to schedule callback: {str(e)}"

def get_agent_config_tool(target_agent_id: int) -> str:
    """Get the configuration of any agent (Admin only)."""
    from app.services.agents import get_profile
    try:
        profile = get_profile(target_agent_id)
        return f"Configuration for Agent {target_agent_id}:\n{json.dumps(profile, indent=2, default=str)}"
    except Exception as e:
        return f"Failed to get agent configuration: {str(e)}"

def pause_agent_automation_tool(target_agent_id: int) -> str:
    """Pause the auto-dialer for an agent (Admin only)."""
    from app.services.agents import update_automation
    try:
        update_automation(target_agent_id, {"auto_dial_enabled": False}, actor="system")
        return f"Auto-dialer has been paused for Agent {target_agent_id}."
    except Exception as e:
        return f"Failed to pause automation: {str(e)}"

def send_sms_tool(to: str, message: str, agent_id: int, role: str = "team") -> str:
    """Send an SMS. A team member may only text someone already in this agent's own CRM.

    Anyone on a team call could otherwise dictate any number in the world and any text, from the
    company's own sender id. An admin keeps the open form, since they can already reach everything.
    """
    digits = "".join(ch for ch in str(to or "") if ch.isdigit())
    if not digits:
        return "Failed: I need the number to text, said in digits."
    if role != "admin":
        from app.services.crm_service import CRMService
        known = CRMService(agent_id).find_by_phone("+" + digits)
        if not known:
            return (f"Failed: {to} is not one of this agent's leads, so I cannot text it. "
                    "Add the lead first, or ask an administrator.")
    try:
        from app.services.plivo_service import PlivoService
        plivo = PlivoService()
        msg_id = plivo.send_sms(to, message)
        return f"SMS successfully sent to {to}. Message ID: {msg_id}"
    except Exception as e:
        return f"Failed to send SMS to {to}. Error: {str(e)}"

def book_calendar_event_tool(email: str, date_time: str, duration_minutes: int = 30, agent_id: int | None = None) -> str:
    """Record a meeting time on the CRM lead matching the email. No external calendar is connected."""
    from app.services.call_service import _valid_meeting
    if not agent_id or not email:
        return "Calendar booking is not available: no calendar is connected and no lead email was given."
    when = _valid_meeting(_to_ist_text(date_time))
    if not when:
        return f"Calendar booking not done: invalid meeting time '{date_time}'. Use 'YYYY-MM-DD HH:MM' in IST, in the future."
    try:
        crm = CRMService(agent_id)
        leads = crm.list_leads(search=email, page=1, page_size=5).get("items", [])
        lead = next((l for l in leads if (l.get("email") or "").strip().lower() == email.strip().lower()), None)
        if not lead:
            return f"Calendar booking is not available: no external calendar is connected and no CRM lead has the email {email}."
        crm.update(lead["id"], {"meeting_at": when}, actor="team")
        return f"Meeting recorded on CRM lead {lead['id']} ({lead.get('name')}) at {when} IST for {duration_minutes} minutes. No calendar invite was sent."
    except Exception as e:
        return f"Calendar booking is not available: {str(e)}"

def check_active_calls_tool() -> str:
    """Check how many active calls are currently ongoing in the system (Admin only)."""
    try:
        from app.services import call_session
        count = call_session.active_count()
        return f"There are currently {count} active calls in the system."
    except Exception as e:
        return f"Failed to check active calls: {str(e)}"

def add_lead_tool(agent_id: int, name: str | None, phone: str | None, requirement: str | None = None, city: str | None = None) -> str:
    """Create a lead the colleague dictates on the call ("Rahul ka number add karo, 98765 43210, Swift chahiye")."""
    from app.services.crm_service import normalize_phone
    digits = normalize_phone(str(phone or ""))
    if not digits:
        return "Failed: I need a full phone number with country code, said in two groups of five digits."
    crm = CRMService(agent_id)
    existing = crm.find_by_phone(digits)
    if existing:
        return f"Already there: {existing.get('name') or 'unnamed'} ({digits}) is lead {existing['id']}, status {existing.get('status')}."
    data = {"phone": digits, "name": (name or "").strip(), "source": "team call", "status": "New"}
    if requirement:
        data["requirements"] = str(requirement)[:500]
    if city:
        data["city"] = str(city)[:80]
    try:
        lead = crm.create(data, actor="team")
    except Exception as e:  # noqa: BLE001 - spoken back as a failure
        return f"Failed to add the lead: {e}"
    return f"Added {lead.get('name') or digits} as lead {lead['id']}. Say 'call them now' or 'queue them' to dial."


def add_note_tool(agent_id: int, lead_id, note: str | None, lead: str | None = None) -> str:
    """Append a note the agent reads before every call to that lead."""
    lead_id, msg = resolve_lead(agent_id, lead_id, lead)
    if not lead_id:
        return msg
    text = (note or "").strip()
    if not text:
        return "Failed: what should the note say?"
    crm = CRMService(agent_id)
    current = (crm.get(lead_id) or {}).get("notes") or ""
    stamp = datetime.now(IST).strftime("%d %b")
    merged = (current + "\n" if current else "") + f"[{stamp}, team] {text[:400]}"
    crm.update(lead_id, {"notes": merged[-2000:]}, actor="team", event_type="lead.note", title="Note added on a team call")
    return f"Noted on lead {lead_id}: {text[:80]}"


def update_lead_details_tool(agent_id: int, lead_id, lead: str | None = None, **fields) -> str:
    """Change name, email, city, company or requirement on a lead (never the phone: that stays with the CRM)."""
    lead_id, msg = resolve_lead(agent_id, lead_id, lead)
    if not lead_id:
        return msg
    allowed = {"name": "name", "email": "email", "city": "city", "company": "company", "requirement": "requirements", "requirements": "requirements"}
    data = {allowed[k]: str(v).strip()[:500] for k, v in fields.items() if k in allowed and v not in (None, "")}
    if not data:
        return "Failed: say which detail to change (name, email, city, company or requirement)."
    if "email" in data:
        data["email"] = spoken_address(data["email"]) or data["email"]
        if "@" not in data["email"]:
            return f"Failed: '{data['email']}' does not look like an email. Spell it letter by letter."
    try:
        CRMService(agent_id).update(lead_id, data, actor="team", title="Details updated on a team call")
    except Exception as e:  # noqa: BLE001
        return f"Failed to update: {e}"
    return f"Updated {', '.join(data)} on lead {lead_id}."


def set_do_not_call_tool(agent_id: int, lead_id, on: bool, lead: str | None = None) -> str:
    """Mark a lead do-not-call (or lift it): no manual, automated or callback dialling while it is on."""
    lead_id, msg = resolve_lead(agent_id, lead_id, lead)
    if not lead_id:
        return msg
    data = {"do_not_call": bool(on)}
    if on:
        data.update(status="Do Not Call", callback_at="", call_status=None)
    else:
        data["status"] = "Follow Up"
    CRMService(agent_id).update(lead_id, data, actor="team", title=("Do not call" if on else "Calls allowed again") + " (team call)")
    return f"Lead {lead_id} {'will not be called again' if on else 'can be called again'}."


def dial_lead_tool(agent_id: int, lead_id, lead: str | None = None, now: bool = True) -> str:
    """Call a lead right now, or put them at the front of the queue for the dialer."""
    from app.services.call_service import CallError, CallService, within_calling_hours
    from app.services.agents import get_automation
    lead_id, msg = resolve_lead(agent_id, lead_id, lead)
    if not lead_id:
        return msg
    crm = CRMService(agent_id)
    row = crm.get(lead_id) or {}
    if row.get("do_not_call"):
        return f"Failed: lead {lead_id} is marked do-not-call."
    if not now:
        crm.update(lead_id, {"call_status": "Pending", "callback_at": ""}, actor="team", title="Queued on a team call")
        return f"Queued {row.get('name') or lead_id}; the dialer picks them up next inside calling hours."
    if not within_calling_hours(get_automation(agent_id)):
        crm.update(lead_id, {"call_status": "Pending", "callback_at": ""}, actor="team", title="Queued on a team call")
        return "Outside calling hours, so they are queued for the next open hour instead."
    try:
        call = CallService(agent_id).start(lead_id, trigger="manual", actor="team")
    except CallError as e:
        return f"Failed to call: {e}"
    return f"Calling {row.get('name') or row.get('phone')} now (call {call.get('call_id') or call.get('id')})."


def set_meeting_tool(agent_id: int, lead_id, date_time: str, lead: str | None = None) -> str:
    """Book or move a meeting/visit for a lead; the reminder email and the pipeline stage follow."""
    from app.services.call_service import _valid_meeting
    lead_id, msg = resolve_lead(agent_id, lead_id, lead)
    if not lead_id:
        return msg
    when = _valid_meeting(_to_ist_text(date_time))
    if not when:
        return f"Invalid meeting time '{date_time}': give a day and time in IST, not in the past."
    CRMService(agent_id).update(lead_id, {"meeting_at": when, "status": "Meeting Booked", "callback_at": ""}, actor="team",
                                event_type="meeting.booked", title=f"Meeting set for {when} on a team call")
    return f"Meeting for lead {lead_id} set for {when} IST."


def set_calling_hours_tool(agent_id: int, start: int | None, end: int | None) -> str:
    """Change this agent's calling window (24h clock, IST)."""
    from app.services.agents import get_automation, update_automation
    cfg = get_automation(agent_id)

    def hour(v, default):
        # "7 baje shaam", "7 pm", "evening 7" -> 19; a bare "7" for the closing hour also means evening
        n = _int(v, default)
        text = str(v or "").lower()
        if n is not None and n < 12 and re.search(r"pm|shaam|sham|evening|raat|night|शाम|रात", text):
            n += 12
        return n

    values = {}
    if start is not None:
        values["calling_hours_start"] = hour(start, cfg.get("calling_hours_start", 9))
    if end is not None:
        e = hour(end, cfg.get("calling_hours_end", 21))
        if e is not None and e <= values.get("calling_hours_start", cfg.get("calling_hours_start", 9)) and e < 12:
            e += 12
        values["calling_hours_end"] = e
    if not values:
        return "Failed: say the new start and/or end hour."
    try:
        update_automation(agent_id, values, actor="team")
    except Exception as e:  # noqa: BLE001
        return f"Failed: {e}"
    new = get_automation(agent_id)
    return f"Calling hours now {new['calling_hours_start']}:00 to {new['calling_hours_end']}:00 IST."


_DAY_WORDS = [("mon", r"mon|somvar|सोम"), ("tue", r"tue|mangal|मंगल"), ("wed", r"wed|budh|बुध"), ("thu", r"thu|guru|गुरु"),
              ("fri", r"fri|shukra|शुक्र"), ("sat", r"sat|shani|शनि"), ("sun", r"sun|ravi|itwar|रवि|इतवार")]


def _hour_of(value, default: int | None = None) -> int | None:
    """'10 AM', '10 ए एम', '7 pm', '19:00', 10 -> hour on the 24h clock."""
    text = str(value if value is not None else "").lower()
    m = re.search(r"(\d{1,2})(?::(\d{2}))?", text)
    if not m:
        return default
    hour = int(m.group(1))
    if hour < 12 and re.search(r"pm|shaam|sham|evening|raat|night|शाम|रात|पी ?एम", text):
        hour += 12
    if hour == 12 and re.search(r"am|subah|morning|सुबह|ए ?एम", text):
        hour = 0
    return hour if 0 <= hour <= 23 else default


_NUM_WORDS = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
              "shunya": "0", "ek": "1", "do": "2", "teen": "3", "char": "4", "paanch": "5", "panch": "5", "chhe": "6", "che": "6", "saat": "7", "aath": "8", "nau": "9"}
_ADDRESS_STOP = {"is", "id", "email", "mail", "to", "on", "mera", "meri", "hai", "par", "pe", "ko", "send", "bhejo", "bhej", "change", "karo", "kar", "the", "my", "and", "aur", "ka", "ki", "wala"}


def spoken_address(text: str | None) -> str | None:
    """
    An email dictated on a call, including digits said as words and a name said in two words:
    "ashish sharma one two zero five one two at the rate gmail dot com" -> ashishsharma120512@gmail.com.
    Everything up to a stop word before the "at" joins the local part; None when it still is not an address.
    """
    from app.services.voice_stream import spoken_email
    raw = str(text or "").strip().lower()
    if not raw:
        return None
    if "@" in raw and " " not in raw.strip():
        return spoken_email(raw) or raw
    tokens = [_NUM_WORDS.get(t, t) for t in re.split(r"\s+", raw)]
    joined = " ".join(tokens)
    m = re.search(r"\s*(?:@|\bat the rate\b|\bat\b)\s*", joined)
    if not m:
        return spoken_email(joined)
    before, after = joined[:m.start()].split(), joined[m.end():]
    local = []
    for t in reversed(before):
        if t in _ADDRESS_STOP or not re.fullmatch(r"[a-z0-9._+-]+", t):
            break
        local.append(t)
    if not local:
        return spoken_email(joined)
    domain = re.sub(r"\s*\bdot\b\s*", ".", after).replace(" ", "")
    candidate = "".join(reversed(local)).replace("dot", ".") + "@" + domain
    return candidate if re.fullmatch(r"[\w.+-]+@[\w-]+(\.[a-z]{2,})+", candidate) else spoken_email(joined)


def set_schedule_tool(agent_id: int, job: str, time: str | None = None, days: str | list | None = None,
                      recipient: str | None = None) -> str:
    """When a scheduled job runs and who gets it: meeting reminders / daily report hour, calling days, report email."""
    from app.services.agents import get_automation, update_automation
    job = re.sub(r"[^a-z]", "", str(job or "").lower())
    cfg = get_automation(agent_id)
    values, said = {}, []
    if job in ("meetingreminder", "reminder", "reminders"):
        hour = _hour_of(time)
        if hour is None:
            return "Failed: say the hour for the meeting reminders, e.g. 10 AM."
        values["meeting_reminder_hour"] = hour
        values["meeting_reminder_enabled"] = True
        said.append(f"meeting reminders at {hour}:00 IST")
    elif job in ("dailyreport", "report"):
        if time is not None and str(time).strip():
            hour = _hour_of(time)
            if hour is None:
                return "Failed: say the hour for the daily report, e.g. 9 PM."
            values["daily_report_hour"] = hour
            values["daily_report_enabled"] = True
            said.append(f"daily report at {hour}:00 IST")
        if recipient:
            email = spoken_address(str(recipient)) or str(recipient).strip()
            if "@" not in email or "." not in email.split("@")[-1]:
                return f"Failed: '{email}' does not look like an email address. Spell it letter by letter."
            values["daily_report_email"] = email
            said.append(f"daily report to {email}")
        if not values:
            return "Failed: say the hour and/or the email for the daily report."
    elif job in ("callingdays", "callingwindow", "days", "window"):
        raw = " ".join(days) if isinstance(days, list) else str(days or "")
        if not raw.strip():
            return "Failed: say which days, e.g. 'Sunday bhi' or 'Monday to Saturday'."
        current = set(int(d) for d in (cfg.get("calling_days") or [0, 1, 2, 3, 4, 5]))
        named = {i for i, (_k, rx) in enumerate(_DAY_WORDS) if re.search(rx, raw, re.I)}
        if re.search(r"\b(all|every|saare|sab|roz|daily)\b|सब|सारे|रोज़|रोज", raw, re.I):
            named = set(range(7))
        if not named:
            return "Failed: I did not catch the day. Say it like 'Sunday' or 'Monday to Friday'."
        remove = bool(re.search(r"\b(off|band|hata|remove|nahi|mat|except|chhod)\b|बंद|हटा|नहीं|छोड़", raw, re.I))
        new_days = sorted((current - named) if remove else (current | named))
        if not new_days:
            return "Failed: at least one calling day must stay on."
        values["calling_days"] = new_days
        said.append("calling days now " + ", ".join(_DAY_WORDS[d][0].title() for d in new_days))
    else:
        return "Failed: which schedule — meeting reminders, daily report, or calling days?"
    try:
        update_automation(agent_id, values, actor="team")
    except Exception as e:  # noqa: BLE001
        return f"Failed: {e}"
    return "Done: " + "; ".join(said) + "."


def pending_work_tool(agent_id: int) -> str:
    """What is waiting for this agent: queue size, callbacks due today, meetings today, hot leads not yet called."""
    crm = CRMService(agent_id)
    today = datetime.now(IST).strftime("%Y-%m-%d")
    queue = crm.queue_size()
    callbacks = crm.due_callbacks(f"{today} 23:59", 20)
    meetings = crm.meetings_on(today)
    parts = [f"{queue} in the call queue", f"{len(callbacks)} callback(s) due today"]
    if callbacks:
        parts.append("next: " + ", ".join(f"{c.get('name') or c.get('phone')} at {str(c.get('callback_at'))[11:16]}" for c in callbacks[:3]))
    if meetings:
        parts.append(f"{len(meetings)} meeting(s) today: " + ", ".join(f"{m.get('name') or m.get('phone')} {str(m.get('meeting_at'))[11:16]}" for m in meetings[:3]))
    return "; ".join(parts) + "."


def set_agent_paused_tool(target_agent_id: int, paused: bool) -> str:
    """Pause (no calls at all) or resume an agent."""
    from app.services import agents as _agents
    try:
        _agents.update(target_agent_id, {"status": "paused" if paused else "active"}, actor="team")
    except Exception as e:  # noqa: BLE001
        return f"Failed: {e}"
    return f"Agent {target_agent_id} is now {'paused: no calls go out' if paused else 'active'}."


ROUTING_MODES = {"ai": "ai", "agent": "ai", "self": "ai", "forward": "forward", "team": "forward",
                 "human": "forward", "person": "forward", "message": "message", "voicemail": "message"}


def call_routing_tool(agent_id: int) -> str:
    """Who answers this line now, in hours and after them: the Inbound page, spoken."""
    from app.services import agents as agent_service
    from app.services.call_service import within_calling_hours
    p = agent_service.get_profile(agent_id)
    cfg = agent_service.get_automation(agent_id)
    spoken = {"ai": "the AI agent", "forward": "your team", "message": "a message, then hang up"}
    team = [m.get("name") or m.get("phone") for m in (p.get("team_members") or []) if m.get("phone")]
    return (f"In hours: {spoken.get(p.get('inbound_mode'), p.get('inbound_mode'))}. "
            f"After hours: {spoken.get(p.get('after_hours_mode'), p.get('after_hours_mode'))}. "
            f"Open {cfg.get('calling_hours_start')} to {cfg.get('calling_hours_end')}, "
            f"{'open right now' if within_calling_hours(cfg) else 'closed right now'}. "
            f"Hand-over {'on' if p.get('transfer_on_request') else 'off'}"
            + (f", ringing {', '.join(team[:3])}" if team else ", but nobody is listed to ring") + ".")


def set_call_routing_tool(agent_id: int, when: str | None, mode: str | None, handover=None) -> str:
    """Change who answers: in hours, after hours, and whether the agent hands callers over at all."""
    from app.services import agents as agent_service
    updates = {}
    if handover is not None:
        updates["transfer_on_request"] = _on_flag(handover)
    if mode:
        chosen = ROUTING_MODES.get(str(mode).strip().lower())
        if not chosen:
            return "Failed: say who should answer — the AI agent, your team, or a message."
        field = "after_hours_mode" if "after" in str(when or "").lower() or "night" in str(when or "").lower() else "inbound_mode"
        if field == "inbound_mode" and chosen == "message":
            return "Failed: a message instead of answering is only for after hours."
        updates[field] = chosen
    if not updates:
        return "Failed: say what to change — who answers in hours, after hours, or whether hand-over is on."
    try:
        agent_service.update_profile(agent_id, updates, actor="team")
    except ValueError as e:
        return f"Failed: {e}"
    return "Done. " + call_routing_tool(agent_id)


def add_team_member_tool(agent_id: int, name: str | None, phone: str | None, email: str | None = None) -> str:
    """Add a colleague to the ring order, so a caller asking for a person reaches somebody."""
    from app.services import agents as agent_service
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if len(digits) < 11 or len(digits) > 15:
        return "Failed: I need their full number with country code, said in digits."
    p = agent_service.get_profile(agent_id)
    members = list(p.get("team_members") or [])
    if any("".join(ch for ch in str(m.get("phone") or "") if ch.isdigit()) == digits for m in members):
        return f"{name or 'That number'} is already on the ring list."
    members.append({"name": (name or "").strip()[:120], "phone": "+" + digits,
                    **({"email": email.strip()[:200]} if email else {})})
    try:
        agent_service.update_profile(agent_id, {"team_members": members}, actor="team")
    except ValueError as e:
        return f"Failed: {e}"
    return f"Added {name or '+' + digits} to the ring list, position {len(members)}."


def live_calls_tool(agent_id: int) -> str:
    """Calls ringing or talking on this agent right now. A colleague could not see them at all before."""
    from app.services import agents as agent_service
    rows = [c for c in agent_service.live_calls([agent_id]) if c.get("agent_id") == agent_id]
    if not rows:
        return "No calls are live on this agent right now."
    lines = []
    for c in rows[:5]:
        who = c.get("lead_name") or c.get("to_number") or c.get("from_number") or "unknown"
        lines.append(f"{who}, {c.get('direction')}, {c.get('status')}, call {c.get('id')}")
    return f"{len(rows)} live: " + "; ".join(lines) + "."


def end_call_tool(agent_id: int, call_id=None, lead: str | None = None) -> str:
    """Hang up a live call this agent is running.

    The caller's own call is never one of these: this tool runs from inside a call, and the session it
    belongs to has no row in ACTIVE for the colleague speaking, so there is nothing to cut by accident.
    """
    from app.services.call_service import CallError, CallService
    from app.services import agents as agent_service
    calls = CallService(agent_id)
    rows = [c for c in agent_service.live_calls([agent_id]) if c.get("agent_id") == agent_id]
    if not rows:
        return "No calls are live on this agent right now."
    chosen = None
    if call_id and str(call_id).isdigit():
        chosen = next((c for c in rows if c.get("id") == int(call_id)), None)
    elif lead:
        wanted = str(lead).strip().lower()
        chosen = next((c for c in rows
                       if wanted in str(c.get("lead_name") or "").lower()
                       or wanted in str(c.get("to_number") or "")), None)
    elif len(rows) == 1:
        chosen = rows[0]
    if not chosen:
        return ("Say which call to end: " +
                "; ".join(f"{c.get('lead_name') or c.get('to_number')} (call {c.get('id')})" for c in rows[:5]) + ".")
    try:
        calls.hangup(int(chosen["id"]))
    except CallError as e:
        return f"Failed: {e}"
    return f"Ended the call with {chosen.get('lead_name') or chosen.get('to_number')}."


def call_summary_tool(agent_id: int, lead: str | None = None, call_id=None) -> str:
    """What was said and decided on a past call: the summary, outcome and how long it ran."""
    from app.services.call_service import CallService
    calls = CallService(agent_id)
    if call_id and str(call_id).isdigit():
        call = calls.get(int(call_id))
    else:
        items = calls.list_calls(search=lead, page=1, page_size=5).get("items", [])
        call = next((c for c in items if c.get("summary")), items[0] if items else None)
    if not call:
        return f"No call found{f' for {lead}' if lead else ''}."
    seconds = int(call.get("duration") or 0)
    when = (call.get("created_at") or "")[:16].replace("T", " ")
    parts = [f"{call.get('lead_name') or call.get('to_number')}, {when}, {seconds // 60}m {seconds % 60}s",
             f"result {call.get('status')}"]
    if call.get("outcome"):
        parts.append(f"outcome {call['outcome']}")
    if call.get("summary"):
        parts.append(" ".join(str(call["summary"]).split())[:400])
    return ". ".join(parts) + "."


# What a colleague calls each automation number, mapped to the field the Automation page writes.
AUTOMATION_NUMBERS = {
    "calls per run": "max_calls_per_run", "batch size": "max_calls_per_run",
    "simultaneous calls": "max_concurrent_calls", "concurrent calls": "max_concurrent_calls",
    "at a time": "max_concurrent_calls", "max retries": "max_retries", "attempts": "max_retries",
    "retry gap": "retry_min_gap_minutes", "wait between attempts": "retry_min_gap_minutes",
    "dial interval": "auto_dial_interval_minutes", "check every": "auto_dial_interval_minutes",
    "follow up after": "nurture_after_days", "nurture days": "nurture_after_days",
    "follow ups per lead": "nurture_max_attempts", "reminder hour": "meeting_reminder_hour",
    "report hour": "daily_report_hour",
}


def set_automation_number_tool(agent_id: int, setting: str | None, value=None) -> str:
    """Change one number on the Automation page: only the on/off switches were reachable by voice."""
    from app.services import agents as agent_service
    asked = " ".join(str(setting or "").lower().split())
    field = AUTOMATION_NUMBERS.get(asked) or next(
        (f for phrase, f in AUTOMATION_NUMBERS.items() if phrase in asked or asked in phrase), None)
    if not field:
        return ("Failed: say which setting — calls per run, simultaneous calls, max retries, wait between "
                "attempts, dial interval, follow up after, follow-ups per lead, reminder hour or report hour.")
    number = _int(value, -1)
    if number < 0:
        return f"Failed: say the new number for {asked}."
    try:
        agent_service.update_automation(agent_id, {field: number}, actor="team")
    except ValueError as e:
        return f"Failed: {e}"   # carries the allowed range, which is what the caller needs to hear
    return f"Done: {asked or field} is now {number}."


def run_job_now_tool(agent_id: int, job: str | None) -> str:
    """Run a scheduled job this moment instead of waiting for its next turn."""
    from app.services import scheduler
    asked = " ".join(str(job or "").lower().split()).replace("-", " ").replace(" ", "_")
    aliases = {"dialer": "auto_dial", "dialler": "auto_dial", "auto_dialer": "auto_dial", "dial": "auto_dial",
               "retry": "retry_calls", "retries": "retry_calls", "callback": "callbacks",
               "follow_up": "nurture", "follow_ups": "nurture", "followups": "nurture",
               "reminders": "meeting_reminder", "reminder": "meeting_reminder", "report": "daily_report"}
    name = asked if asked in scheduler.JOBS else aliases.get(asked)
    if not name:
        return "Failed: say which one — the dialer, retries, callbacks, follow-ups, the call queue, meeting reminders or the daily report."
    result = scheduler.run_job(agent_id, name, True, actor="team")
    return f"{scheduler.LABELS.get(name, name)}: {result}"


def lead_details_tool(agent_id: int, lead: str | None = None, lead_id=None) -> str:
    """Everything on one lead, read back on a call: only a five-row name list existed before."""
    lead_id, note = resolve_lead(agent_id, lead_id, lead)
    if note:
        return note
    row = CRMService(agent_id).get(lead_id) or {}
    if not row:
        return f"No lead {lead_id} on this agent."
    parts = [f"{row.get('name') or 'No name'} ({row.get('phone')})",
             f"stage {row.get('status')}", f"temperature {row.get('qualification') or 'unknown'}"]
    for label, key in (("needs", "requirements"), ("objections", "objections"), ("notes", "notes"),
                       ("meeting", "meeting_at"), ("callback", "callback_at"), ("email", "email"),
                       ("city", "city"), ("company", "company")):
        value = " ".join(str(row.get(key) or "").split())
        if value:
            parts.append(f"{label}: {value[:200]}")
    if row.get("do_not_call"):
        parts.append("marked Do Not Call")
    return ". ".join(parts) + "."


def analytics_tool(agent_id: int, days: int = 7) -> str:
    """The Insights numbers, spoken: what a colleague rings to ask before a review."""
    from app.services import analytics
    days = max(7, min(int(days or 7), 90))
    try:
        data = analytics.report(agent_id, days)
    except Exception as e:  # noqa: BLE001 - a number is never worth failing a live turn over
        log.warning("Analytics tool failed: %s", e)
        return "I could not read the numbers just now."
    k = data.get("kpis") or {}
    cost = data.get("cost") or {}
    lines = [f"Last {days} days: {k.get('calls', 0)} calls, {k.get('connected', 0)} connected",
             f"{k.get('meetings', 0)} meetings booked"]
    if k.get("connect_rate") is not None:
        lines.append(f"connect rate {k['connect_rate']}%")
    if cost.get("total"):
        lines.append(f"spend {cost.get('currency') or ''}{round(float(cost['total']), 2)}")
    return ", ".join(lines) + "."


def knowledge_list_tool(agent_id: int) -> str:
    """What this agent knows: the documents, and whether search is semantic or keyword only."""
    from app.services import rag
    documents = rag.list_documents(agent_id)
    if not documents:
        return "This agent has no documents yet."
    stats = rag.stats(agent_id)
    named = "; ".join(f"{d['title']} ({d.get('chunk_count') or 0} passages)" for d in documents[:6])
    more = f", and {len(documents) - 6} more" if len(documents) > 6 else ""
    mode = "meaning and keywords" if stats.get("semantic") else "keywords only"
    return f"{len(documents)} document(s): {named}{more}. Search is {mode}."


def knowledge_search_tool(agent_id: int, question: str) -> str:
    """What the agent would say to a customer asking this, read back to a colleague checking it."""
    from app.services import rag
    question = " ".join(str(question or "").split())
    if len(question) < 3:
        return "Failed: ask the question the way a customer would."
    hits = rag.search(agent_id, question, top_k=2, use_embeddings=False)
    if not hits:
        return f"Nothing in the knowledge base answers '{question[:60]}'. The agent would say it will check and come back."
    return " ".join(f"({h.get('title')}) {' '.join((h.get('text') or '').split())[:220]}" for h in hits)


def persona_tool(agent_id: int) -> str:
    """How this agent introduces itself and what it is told to do: the Persona page, spoken."""
    from app.services import agents as agent_service
    p = agent_service.get_profile(agent_id)
    bits = [f"{p.get('agent_name')} for {p.get('company_name')}"]
    for label, key in (("tagline", "company_tagline"), ("role", "agent_role"), ("calls the person", "customer_noun"),
                       ("voice", "voice_speaker"), ("language", "default_language"),
                       ("objective", "objective"), ("asks for", "call_to_action")):
        value = " ".join(str(p.get(key) or "").split())
        if value:
            bits.append(f"{label}: {value[:160]}")
    return ". ".join(bits) + "."


TAUGHT_FACT_CHARS = 200   # of the fact itself in the saved passage; the rest of the 380 is breadcrumb and keywords
KNOWN_OVERLAP = 0.8       # this much of the fact's own words already in a passage: we know it
TOPIC_OVERLAP = 0.6       # this much of the topic's words in common: the passage is about the same thing
TAUGHT_KEY = "rag:taught:%s"
TAUGHT_TTL = 3600
NUMBER_RE = re.compile(r"\d[\d,.]*")
# Values a colleague speaks rather than writes: "nine to six", "saat din", "Monday to Saturday". A
# contradiction usually shows up in one of these, and on a phone call they are far more common than digits.
VALUE_WORDS = re.compile(
    r"\b(zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|forty|fifty|"
    r"hundred|thousand|lakh|crore|half|quarter|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"daily|weekly|monthly|free)\b"
    r"|एक|दो|तीन|चार|पाँच|पांच|छह|सात|आठ|नौ|दस|सौ|हज़ार|हजार|लाख|करोड़"
    r"|सोमवार|मंगलवार|बुधवार|गुरुवार|शुक्रवार|शनिवार|रविवार|मुफ्त|फ्री", re.I)


def _values(text: str) -> set[str]:
    """Every number-like value in a line, written either way."""
    return set(NUMBER_RE.findall(text)) | {m.group(0).lower() for m in VALUE_WORDS.finditer(text)}


def _overlap(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a) if a else 0.0


def teach_fact_tool(agent_id: int, fact: str, topic: str | None = None) -> str:
    """Teach the knowledge base something, the way you would tell a new colleague.

    Checking and saving are one tool call, so the model cannot save without looking first. The answer
    leads with ALREADY KNOWN / CONFLICTS / SAVED, which the prompt turns into what the agent says next.
    Keyword search only: this runs mid-call and an embedding round trip would be heard as a pause.
    """
    from app.core import store
    from app.services import rag
    fact = " ".join((fact or "").split())
    if len(fact) < 30:
        return "Failed: say the fact in a full sentence, with the number or name in it."
    topic = " ".join((topic or "").split())[:60] or fact[:60]

    fact_words, topic_words = set(rag.tokenize(fact)), set(rag.tokenize(topic))
    # Indexing runs in a background thread, so a fact taught a moment ago is not searchable yet. The
    # same colleague repeating themselves on the same call must still be told we have it.
    recent = store.get_json(TAUGHT_KEY % agent_id) or []
    candidates = [{"text": t} for t in recent] + rag.search(agent_id, f"{topic} {fact}", top_k=3, use_embeddings=False)

    for hit in candidates:
        text = hit.get("text") or ""
        words = set(rag.tokenize(text))
        if _overlap(fact_words, words) >= KNOWN_OVERLAP:
            return f"ALREADY KNOWN: the knowledge base already says: {text[:200]}"

    # A passage about the same topic carrying a different number is a contradiction the agent would
    # otherwise read out to a customer, so it is surfaced instead of being buried under a second version.
    clash = next((hit.get("text") or "" for hit in candidates
                  if _overlap(topic_words, set(rag.tokenize(hit.get("text") or ""))) >= TOPIC_OVERLAP
                  and _values(hit.get("text") or "") != _values(fact)), None)

    passage = _taught_passage(topic, fact)
    rag.add_text(agent_id, f"{topic} (taught on a call)"[:60], passage, actor="team")
    # The passage, not the bare fact: it carries the topic line, so the next check compares like with
    # like against what search will return once indexing catches up.
    store.set_json(TAUGHT_KEY % agent_id, ([passage] + recent)[:20], ttl=TAUGHT_TTL)
    if clash:
        return (f"SAVED. CONFLICTS with what we already say: {clash[:200]} — tell them both versions and ask which "
                f"is right. If the old one is wrong, a person has to delete it on the Knowledge page.")
    return f"SAVED under '{topic}'. It is searchable from the next call."


def _taught_passage(topic: str, fact: str) -> str:
    """A fact shaped like the rest of the knowledge base, so hybrid search finds it again.

    One block under the 400-character chunk limit: a breadcrumb line for context, the fact itself, and
    an English keyword tail, because a Hindi caller's question is expanded to English before keyword
    search runs and a passage with no English words in it would never match it.
    """
    from app.services.rag import QUERY_GLOSS, tokenize
    fact = fact[:TAUGHT_FACT_CHARS]
    keywords = []
    for token in tokenize(f"{topic} {fact}"):
        for word in (QUERY_GLOSS.get(token) or "").split():
            if word not in keywords:
                keywords.append(word)
    block = f"{topic}\n{fact}"
    tail = " ".join(keywords)[:max(0, 380 - len(block) - 11)]
    return f"{block}\nKeywords: {tail}".rstrip() if tail.strip() else block


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Send an email to any recipient.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "The recipient's email address."},
                    "subject": {"type": "string", "description": "The subject of the email."},
                    "body": {"type": "string", "description": "The body content of the email."}
                },
                "required": ["to", "subject", "body"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_email_status",
            "description": "What emails this agent sent recently (to whom, subject, delivered or failed) and the last email service check. Use when a team member asks whether any mail went out or whether email is working.",
            "parameters": {"type": "object", "properties": {"hours": {"type": "integer", "description": "How far back to look, in hours (default 24)."}}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_records",
            "description": "Search the CRM for past calls, leads, and histories by name, phone, or email.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query (name, phone number, or email)."}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_credits",
            "description": "Check the remaining credits or account balance for the platform.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "today_stats",
            "description": "This agent's numbers: calls and connections today, live calls, leads, hot leads, meetings, "
                           "automation state. Use for 'how many calls today', 'kitni calls hui', 'how is it going'.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_automation",
            "description": "Turn one or more of this agent's automations on or off in one go: auto_dial (outbound dialer), retry "
                           "(retry unanswered calls), speed_to_lead (call new website leads), nurture (follow-up / warm-lead calls), "
                           "meeting_reminder, daily_report, auto_emails. Use for 'pause/stop/resume/start/on/off/band/chalu' "
                           "requests; list every automation they named. Nothing changes unless this tool is called.",
            "parameters": {
                "type": "object",
                "properties": {
                    "switches": {"type": "array", "items": {"type": "string", "enum": ["auto_dial", "retry", "speed_to_lead", "nurture", "meeting_reminder", "daily_report", "auto_emails"]},
                                 "description": "All automations the caller named."},
                    "on": {"type": "boolean", "description": "true to switch on / resume, false to switch off / pause."}
                },
                "required": ["switches", "on"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "recent_calls",
            "description": "List this agent's latest completed calls with who, direction, duration, result and time. Use for "
                           "'last call', 'how long did the call go', 'who called', 'what happened on the call with X'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "How many calls to list (1-10, default 5)."},
                    "lead": {"type": "string", "description": "Optional lead name or phone number to narrow down."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_lead_status",
            "description": "Update the status of a lead in the CRM.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lead": {"type": "string", "description": "The lead's name or phone number as the caller said it."},
                    "lead_id": {"type": "integer", "description": "The lead id, if a previous tool result gave it."},
                    "new_status": {"type": "string", "description": "Pipeline stage ('New', 'Contacted', 'Interested', 'Follow Up', 'Meeting Booked', 'Closed Won', 'Not Interested', 'Do Not Call', 'Closed Lost') or qualification ('Hot', 'Warm', 'Cold')."}
                },
                "required": ["new_status"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_agent_schedule",
            "description": "Check the calling window hours and days when the agent is allowed to make outbound calls.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_callback",
            "description": "THE tool for 'X ko kal 11 baje call karna' / 'schedule a callback for X': books the agent to ring that lead at that time. Name the lead by name or phone; no id needed. Do not send an SMS or email for this.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lead": {"type": "string", "description": "The lead's name or phone number as the caller said it."},
                    "lead_id": {"type": "integer", "description": "The lead id, if a previous tool result gave it."},
                    "date_time": {"type": "string", "description": "The date and time for the callback as 'YYYY-MM-DD HH:MM' in IST (e.g., '2026-10-15 14:30'); convert 'kal 11 baje' using today's date."}
                },
                "required": ["date_time"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_sms",
            "description": "Send an SMS message to a phone number.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "The recipient's phone number."},
                    "message": {"type": "string", "description": "The content of the SMS."}
                },
                "required": ["to", "message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "book_calendar_event",
            "description": "Record a meeting time on the CRM lead with this email (no external calendar is connected).",
            "parameters": {
                "type": "object",
                "properties": {
                    "email": {"type": "string", "description": "The email of the attendee."},
                    "date_time": {"type": "string", "description": "The date and time of the event as 'YYYY-MM-DD HH:MM' in IST."},
                    "duration_minutes": {"type": "integer", "description": "Duration in minutes."}
                },
                "required": ["email", "date_time"]
            }
        }
    }
]

TOOLS += [
    {"type": "function", "function": {"name": "add_lead", "description": "Add a new lead the caller dictates: name and phone, optionally what they want and their city.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "phone": {"type": "string", "description": "Full number with country code, digits only."},
                                                        "requirement": {"type": "string"}, "city": {"type": "string"}}, "required": ["phone"]}}},
    {"type": "function", "function": {"name": "add_note", "description": "Add a note to a lead that the agent reads before every call to them ('Rahul pe note karo: sirf Sunday ko call karna').",
        "parameters": {"type": "object", "properties": {"lead": {"type": "string", "description": "Lead name or phone"}, "note": {"type": "string"}}, "required": ["lead", "note"]}}},
    {"type": "function", "function": {"name": "update_lead_details", "description": "Change a lead's name, email, city, company or requirement. Never the phone number.",
        "parameters": {"type": "object", "properties": {"lead": {"type": "string"}, "name": {"type": "string"}, "email": {"type": "string"}, "city": {"type": "string"},
                                                        "company": {"type": "string"}, "requirement": {"type": "string"}}, "required": ["lead"]}}},
    {"type": "function", "function": {"name": "set_do_not_call", "description": "Mark a lead do-not-call (on=true) or allow calls again (on=false).",
        "parameters": {"type": "object", "properties": {"lead": {"type": "string"}, "on": {"type": "boolean"}}, "required": ["lead", "on"]}}},
    {"type": "function", "function": {"name": "dial_lead", "description": "Call a lead right now (now=true) or queue them for the dialer (now=false). 'Rahul ko abhi call karo' / 'queue mein daal do'.",
        "parameters": {"type": "object", "properties": {"lead": {"type": "string"}, "now": {"type": "boolean"}}, "required": ["lead"]}}},
    {"type": "function", "function": {"name": "set_meeting", "description": "Book or move a meeting / showroom visit for a lead at a day and time (IST).",
        "parameters": {"type": "object", "properties": {"lead": {"type": "string"}, "date_time": {"type": "string", "description": "YYYY-MM-DD HH:MM IST"}}, "required": ["lead", "date_time"]}}},
    {"type": "function", "function": {"name": "set_schedule", "description": "When a scheduled job runs / who gets it: job 'meeting_reminder' with time ('reminder 10 AM pe set karo'), "
                                                                             "job 'daily_report' with time and/or recipient email ('report 9 baje bhejo', 'report ki email badlo'), "
                                                                             "job 'calling_days' with days to add or remove ('Sunday bhi on karo', 'Saturday band karo').",
        "parameters": {"type": "object", "properties": {"job": {"type": "string", "enum": ["meeting_reminder", "daily_report", "calling_days"]},
                                                        "time": {"type": "string", "description": "Hour like '10 AM' or '21:00'"},
                                                        "days": {"type": "string", "description": "Days named, with 'off'/'band' to remove"},
                                                        "recipient": {"type": "string", "description": "Email address as spoken"}}, "required": ["job"]}}},
    {"type": "function", "function": {"name": "set_calling_hours", "description": "Change the calling window: start and/or end hour on the 24h clock, IST ('10 se 7 tak call karo').",
        "parameters": {"type": "object", "properties": {"start": {"type": "integer"}, "end": {"type": "integer"}}}}},
    {"type": "function", "function": {"name": "pending_work", "description": "What is waiting: call queue size, callbacks due today, meetings today. Use for 'kya pending hai', 'aaj kya hai'.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "set_automation_number", "description": "Change a number on the Automation page: calls per run, simultaneous calls, max retries, wait between attempts, dial interval, follow up after (days), follow-ups per lead, reminder hour, report hour. 'ek baar mein 5 call karo'.",
        "parameters": {"type": "object", "properties": {"setting": {"type": "string"}, "value": {"type": "integer"}}, "required": ["setting", "value"]}}},
    {"type": "function", "function": {"name": "run_job_now", "description": "Run a scheduled job right now: the dialer, retries, callbacks, follow-ups, the call queue, meeting reminders or the daily report. 'abhi dialer chala do'.",
        "parameters": {"type": "object", "properties": {"job": {"type": "string"}}, "required": ["job"]}}},
    {"type": "function", "function": {"name": "live_calls", "description": "Calls ringing or talking on this agent right now. 'abhi koi call chal rahi hai'.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "end_call", "description": "Hang up a live call this agent is running. Never ends the call you are on. 'Rahul wali call kaat do'.",
        "parameters": {"type": "object", "properties": {"lead": {"type": "string", "description": "Whose call, by name or number"}, "call_id": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "call_summary", "description": "What happened on a call: summary, outcome and length. 'Rahul se kya baat hui'.",
        "parameters": {"type": "object", "properties": {"lead": {"type": "string"}, "call_id": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "call_routing", "description": "Who answers this line in hours and after hours, whether hand-over is on, and who gets rung. 'inbound kaise set hai'.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "set_call_routing", "description": "Change who answers incoming calls: the AI agent, your team, or (after hours) a message. Also switches hand-over on or off. 'raat ko team pe daal do', 'transfer band karo'.",
        "parameters": {"type": "object", "properties": {
            "when": {"type": "string", "description": "'hours' or 'after hours'"},
            "mode": {"type": "string", "description": "ai | forward (your team) | message"},
            "handover": {"type": "boolean", "description": "Whether the agent hands a caller over when they ask for a person"}}}}},
    {"type": "function", "function": {"name": "add_team_member", "description": "Add a colleague to the ring order for hand-overs: name and full phone number. 'Neha ko bhi ring list mein daal do'.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "phone": {"type": "string"}, "email": {"type": "string"}}, "required": ["phone"]}}},
    {"type": "function", "function": {"name": "lead_details", "description": "Everything on one lead: stage, temperature, what they need, objections, notes, meeting and callback. 'Rahul ka kya scene hai', 'read me the lead'.",
        "parameters": {"type": "object", "properties": {"lead": {"type": "string", "description": "Name, phone or id"}}, "required": ["lead"]}}},
    {"type": "function", "function": {"name": "analytics", "description": "The Insights numbers for this agent: calls, connect rate, meetings and spend over a period. 'is hafte ka kya hisaab hai'.",
        "parameters": {"type": "object", "properties": {"days": {"type": "integer", "description": "7 to 90, default 7"}}}}},
    {"type": "function", "function": {"name": "knowledge_list", "description": "What documents this agent has and whether its search is semantic or keyword only.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "knowledge_search", "description": "What the agent would tell a customer who asked this — read the knowledge base back. Use to check an answer before trusting it.",
        "parameters": {"type": "object", "properties": {"question": {"type": "string", "description": "The question in the customer's own words"}}, "required": ["question"]}}},
    {"type": "function", "function": {"name": "persona", "description": "How this agent introduces itself: name, company, tagline, role, voice, language, objective and call to action.",
        "parameters": {"type": "object", "properties": {}}}},
    # A colleague may pause their own desk; the admin may name another one. Stopping only the dialler,
    # without silencing the agent for inbound callers, is set_automation.
    {"type": "function", "function": {"name": "set_agent_paused", "description": "Pause this agent (no calls at all: manual, auto-dial, retries) or resume it. 'agent band karo' / 'chalu karo'.",
        "parameters": {"type": "object", "properties": {"paused": {"type": "boolean"}}, "required": ["paused"]}}},
    {"type": "function", "function": {"name": "teach_fact", "description": (
        "A colleague tells you something about the business the knowledge base should hold — a price, a policy, an "
        "opening time, a service, a correction ('ab Sunday bhi khula rehta hai', 'premium plan ab 3499 ka hai'). "
        "Checks the knowledge base first and saves only what is new. Returns ALREADY KNOWN (say so, do not save again), "
        "CONFLICTS (tell them what we currently say and ask which is right), or SAVED. Never use it for anything a "
        "customer said, for a lead's own details, or for how to sell."),
        "parameters": {"type": "object", "properties": {
            "fact": {"type": "string", "description": "The fact in one full sentence, in the words the business would use"},
            "topic": {"type": "string", "description": "What it is about, two or three words: 'Premium plan price', 'Sunday opening'"},
        }, "required": ["fact"]}}},
]

ADMIN_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "system_diagnostics",
            "description": "Run basic system diagnostics to check CPU load, memory, and uptime.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "all_agents_overview",
            "description": "One line per agent: calls today, live calls, leads, hot leads, meetings, automation on/off. "
                           "Use for 'how are all agents doing', 'sab agents ka status'.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_agent_automation",
            "description": "Turn automations on or off for ANY agent named by id, name or company (e.g. 'carsindias', "
                           "'Hairscope'). Same switches as set_automation. Use when the admin names another agent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent": {"type": "string", "description": "Agent id, name or company name."},
                    "switches": {"type": "array", "items": {"type": "string", "enum": ["auto_dial", "retry", "speed_to_lead", "nurture", "meeting_reminder", "daily_report", "auto_emails"]}},
                    "on": {"type": "boolean"}
                },
                "required": ["agent", "switches", "on"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "agent_stats",
            "description": "Today's numbers and recent calls for ANY agent named by id, name or company.",
            "parameters": {
                "type": "object",
                "properties": {"agent": {"type": "string", "description": "Agent id, name or company name."}},
                "required": ["agent"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_all_agents",
            "description": "List all agents across the entire platform.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_agent_config",
            "description": "Get the full configuration profile of any specific agent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_agent_id": {"type": "string", "description": "The agent to query: its id, name or company name."}
                },
                "required": ["target_agent_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "pause_agent_automation",
            "description": "Pause the auto-dialer for any specific agent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_agent_id": {"type": "string", "description": "The agent to pause: its id, name or company name."}
                },
                "required": ["target_agent_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_active_calls",
            "description": "Check how many active calls are currently ongoing in the platform.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    }
]

# Tools whose dispatch already honours an `agent` argument ("Hairscope ka auto dial band karo"). The
# argument was read at dispatch but declared in no schema, so the model had no way to send it and every
# cross-agent instruction quietly acted on the desk that answered the phone.
_TARGET_AWARE = {"add_lead", "add_note", "update_lead_details", "set_do_not_call", "dial_lead", "set_meeting",
                 "set_schedule", "set_calling_hours", "pending_work", "teach_fact", "set_automation",
                 "today_stats", "check_agent_schedule", "recent_calls", "check_records", "set_agent_paused",
                 "lead_details", "analytics", "knowledge_list", "knowledge_search", "persona",
                 "call_routing", "set_call_routing", "add_team_member",
                 "live_calls", "end_call", "call_summary", "set_automation_number", "run_job_now"}
_AGENT_ARG = {"type": "string", "description": "Another agent by id, name or company name. Omit to act on this agent."}


def get_tools_for_role(role: str) -> list[dict]:
    """The tools this caller may use. Only an admin is offered the cross-agent argument."""
    if role != "admin":
        return TOOLS
    widened = []
    for tool in TOOLS:
        function = tool["function"]
        if function["name"] not in _TARGET_AWARE:
            widened.append(tool)
            continue
        parameters = function.get("parameters") or {"type": "object", "properties": {}}
        properties = dict(parameters.get("properties") or {})
        properties["agent"] = _AGENT_ARG
        widened.append({**tool, "function": {**function, "parameters": {**parameters, "properties": properties}}})
    return widened + ADMIN_TOOLS

def _tool_schemas() -> dict:
    return {t["function"]["name"]: t["function"].get("parameters", {}).get("properties", {}) for t in TOOLS + ADMIN_TOOLS}


def resolve_tool_name(name: str) -> str:
    """The tool the model meant: exact, else case/underscore-insensitive, else a unique substring match."""
    names = list(_tool_schemas())
    if name in names:
        return name
    key = re.sub(r"[^a-z]", "", str(name or "").lower())
    hits = [n for n in names if re.sub(r"[^a-z]", "", n) == key]
    if not hits:
        hits = [n for n in names if key and (key in re.sub(r"[^a-z]", "", n) or re.sub(r"[^a-z]", "", n) in key)]
    return hits[0] if len(hits) == 1 else name


def _split_list(raw: str) -> list[str]:
    return [n.strip().strip("\"'") for n in re.split(r"[,\s/]+|\band\b|\baur\b", raw) if n.strip()]


def normalize_args(name: str, args: dict) -> dict:
    """Coerce every argument to its schema type: sarvam markup sends lists, ints and booleans as strings."""
    schema = _tool_schemas().get(name) or {}
    out = {}
    for key, value in (args or {}).items():
        spec = schema.get(key) or {}
        kind = spec.get("type")
        if isinstance(value, str):
            raw = value.strip()
            if kind == "array":
                if raw.startswith("["):
                    try:
                        value = [str(v).strip() for v in json.loads(raw)]
                    except json.JSONDecodeError:
                        value = _split_list(raw.strip("[]"))
                else:
                    value = _split_list(raw)
            elif kind == "boolean":
                value = _on_flag(raw)
            elif kind == "integer":
                value = _int(raw, None)
            elif kind == "object" and raw.startswith("{"):
                with contextlib.suppress(json.JSONDecodeError):
                    value = json.loads(raw)
        out[key] = value
    return out


def execute_tool(name: str, arguments: str, agent_id: int, role: str = "team") -> str:
    """Execute a tool by name and return its result, checking permissions.

    Arguments are normalised to the schema and a misnamed tool is resolved first, so the model's
    formatting never fails a colleague's request; a result that still fails is reported as an
    issue the admin can heal (re-run) from the web app.
    """
    try:
        args = json.loads(arguments or "{}") if isinstance(arguments, str) else dict(arguments or {})
        if not isinstance(args, dict):
            args = {}
    except Exception:
        return "Failed to parse arguments."
    name = resolve_tool_name(name)
    args = normalize_args(name, args)
    try:
        result = _dispatch(name, args, agent_id, role)
    except Exception as e:
        log.exception("tool %s failed", name)
        result = f"Tool {name} failed: {e}"
    if name != "send_email" and result.lower().startswith(("failed", "tool ", "access denied", "error")):
        from app.services.heal_service import report
        report("tool_failed", f"{name}({json.dumps(args, ensure_ascii=False)[:300]}) -> {result[:300]}", agent_id=agent_id,
               data={"name": name, "args": args, "agent_id": agent_id, "role": role}, title=f"Call tool failed: {name}")
    return result


def _dispatch(name: str, args: dict, agent_id: int, role: str) -> str:
    if name == "send_email":
        return send_email_tool(args.get("to"), args.get("subject"), args.get("body"), agent_id)
    elif name == "check_records":
        return check_records_tool(args.get("query"), agent_id)
    elif name == "check_email_status":
        return check_email_status_tool(agent_id, _int(args.get("hours"), 24))
    elif name == "check_credits":
        return check_credits_tool()
    elif name == "recent_calls":
        return recent_calls_tool(agent_id, _int(args.get("limit"), 5), lead=args.get("lead") or args.get("name"))
    elif name == "today_stats":
        return today_stats_tool(agent_id)
    elif name == "set_automation":
        flag = args.get("on", args.get("state", args.get("enabled", args.get("action"))))
        if flag is None:
            return "Failed: say whether to switch it on or off."
        return set_automation_tool(agent_id, args.get("switches") or args.get("switch") or args.get("automation") or [], _on_flag(flag))
    elif name == "update_lead_status":
        return update_lead_status_tool(args.get("lead_id"), args.get("new_status"), agent_id, lead=args.get("lead") or args.get("name"))
    elif name == "check_agent_schedule":
        return check_agent_schedule_tool(agent_id)
    elif name == "schedule_callback":
        return schedule_callback_tool(args.get("lead_id"), args.get("date_time"), agent_id, lead=args.get("lead") or args.get("name"))
    elif name == "send_sms":
        return send_sms_tool(args.get("to"), args.get("message"), agent_id, role)
    elif name == "book_calendar_event":
        return book_calendar_event_tool(args.get("email"), args.get("date_time"), args.get("duration_minutes", 30), agent_id)
    # Own-agent actions a colleague needs on a call; an admin naming another agent ("Hairscope ka") works on that one.
    target = agent_id
    if role == "admin" and args.get("agent"):
        target, note = resolve_agent(args.get("agent"), agent_id)
        if note:
            return note
    if name == "set_automation_number":
        return set_automation_number_tool(target, args.get("setting"), args.get("value"))
    elif name == "run_job_now":
        return run_job_now_tool(target, args.get("job"))
    elif name == "live_calls":
        return live_calls_tool(target)
    elif name == "end_call":
        return end_call_tool(target, args.get("call_id"), args.get("lead") or args.get("name"))
    elif name == "call_summary":
        return call_summary_tool(target, args.get("lead") or args.get("name"), args.get("call_id"))
    elif name == "call_routing":
        return call_routing_tool(target)
    elif name == "set_call_routing":
        return set_call_routing_tool(target, args.get("when"), args.get("mode"), args.get("handover"))
    elif name == "add_team_member":
        return add_team_member_tool(target, args.get("name"), args.get("phone"), args.get("email"))
    elif name == "lead_details":
        return lead_details_tool(target, args.get("lead") or args.get("name"), args.get("lead_id"))
    elif name == "analytics":
        return analytics_tool(target, _int(args.get("days"), 7))
    elif name == "knowledge_list":
        return knowledge_list_tool(target)
    elif name == "knowledge_search":
        return knowledge_search_tool(target, args.get("question") or args.get("query") or "")
    elif name == "persona":
        return persona_tool(target)
    elif name == "set_agent_paused":
        return set_agent_paused_tool(target, _on_flag(args.get("paused", True)))
    elif name == "add_lead":
        return add_lead_tool(target, args.get("name"), args.get("phone"), args.get("requirement"), args.get("city"))
    elif name == "add_note":
        return add_note_tool(target, args.get("lead_id"), args.get("note") or args.get("text"), lead=args.get("lead") or args.get("name"))
    elif name == "update_lead_details":
        fields = {k: args.get(k) for k in ("name", "email", "city", "company", "requirement", "requirements") if args.get(k)}
        return update_lead_details_tool(target, args.get("lead_id"), lead=args.get("lead"), **fields)
    elif name == "set_do_not_call":
        return set_do_not_call_tool(target, args.get("lead_id"), _on_flag(args.get("on", True)), lead=args.get("lead") or args.get("name"))
    elif name == "dial_lead":
        return dial_lead_tool(target, args.get("lead_id"), lead=args.get("lead") or args.get("name"), now=_on_flag(args.get("now", True)))
    elif name == "set_meeting":
        return set_meeting_tool(target, args.get("lead_id"), args.get("date_time") or args.get("time") or "", lead=args.get("lead") or args.get("name"))
    elif name == "set_calling_hours":
        return set_calling_hours_tool(target, args.get("start"), args.get("end"))
    elif name == "set_schedule":
        return set_schedule_tool(target, args.get("job") or "", args.get("time") or args.get("hour"), args.get("days") or args.get("day"),
                                 args.get("recipient") or args.get("email"))
    elif name == "pending_work":
        return pending_work_tool(target)
    elif name == "teach_fact":
        return teach_fact_tool(target, args.get("fact") or args.get("text") or "", args.get("topic") or args.get("title"))

    # Admin tools
    if role == "admin":
        if name == "system_diagnostics":
            return system_diagnostics_tool()
        elif name == "list_all_agents":
            return list_all_agents_tool()
        elif name == "all_agents_overview":
            return all_agents_overview_tool()
        elif name == "set_agent_automation":
            target, note = resolve_agent(args.get("agent"), agent_id)
            flag = args.get("on", args.get("state", args.get("enabled", args.get("action"))))
            if flag is None:
                return note or "Failed: say whether to switch it on or off."
            return note or set_automation_tool(target, args.get("switches") or args.get("switch") or args.get("automation") or [], _on_flag(flag))
        elif name == "agent_stats":
            target, note = resolve_agent(args.get("agent"), agent_id)
            return note or (today_stats_tool(target) + "\n" + recent_calls_tool(target, 3))
        elif name == "get_agent_config":
            target, note = resolve_agent(args.get("target_agent_id") or args.get("agent"), agent_id)
            return note or get_agent_config_tool(target)
        elif name == "pause_agent_automation":
            target, note = resolve_agent(args.get("target_agent_id") or args.get("agent"), agent_id)
            return note or pause_agent_automation_tool(target)
        elif name == "check_active_calls":
            return check_active_calls_tool()

    return f"Access Denied or Unknown tool: {name}"
