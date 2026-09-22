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
    if not text or "?" in text or re.search(r"\b(kya|kyu|kyun|why|what|kab|when|agar|if)\b|क्या|क्यों|कब|अगर", text, re.I):
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
    if not named:
        return None
    return set_automation_tool(agent_id, named, not off), ("off" if off else "on")


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
    """Query the user's account balance/credit status."""
    return "Account credit balance is unknown: no billing integration is connected, so do not quote a balance."

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
    try:
        crm.update(lead_id, {"callback_at": when, "call_status": "Pending"}, actor="team")
        return f"Callback scheduled for lead {lead_id} at {when} IST."
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

def send_sms_tool(to: str, message: str) -> str:
    """Send an SMS to a phone number."""
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
        return "Failed: I need a full phone number with country code (say it in groups, e.g. 98765 43210)."
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
        from app.services.voice_stream import spoken_email
        data["email"] = spoken_email(data["email"]) or data["email"]
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
    {"type": "function", "function": {"name": "set_calling_hours", "description": "Change the calling window: start and/or end hour on the 24h clock, IST ('10 se 7 tak call karo').",
        "parameters": {"type": "object", "properties": {"start": {"type": "integer"}, "end": {"type": "integer"}}}}},
    {"type": "function", "function": {"name": "pending_work", "description": "What is waiting: call queue size, callbacks due today, meetings today. Use for 'kya pending hai', 'aaj kya hai'.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "set_agent_paused", "description": "Pause this agent (no calls at all: manual, auto-dial, retries) or resume it. 'agent band karo' / 'chalu karo'.",
        "parameters": {"type": "object", "properties": {"paused": {"type": "boolean"}}, "required": ["paused"]}}},
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
                    "target_agent_id": {"type": "integer", "description": "The ID of the agent to query."}
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
                    "target_agent_id": {"type": "integer", "description": "The ID of the agent to pause."}
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

def get_tools_for_role(role: str) -> list[dict]:
    if role == "admin":
        return TOOLS + ADMIN_TOOLS
    return TOOLS

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
        return send_sms_tool(args.get("to"), args.get("message"))
    elif name == "book_calendar_event":
        return book_calendar_event_tool(args.get("email"), args.get("date_time"), args.get("duration_minutes", 30), agent_id)
    # Own-agent actions a colleague needs on a call; an admin naming another agent ("Hairscope ka") works on that one.
    target = agent_id
    if role == "admin" and args.get("agent"):
        target, note = resolve_agent(args.get("agent"), agent_id)
        if note:
            return note
    if name == "add_lead":
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
    elif name == "pending_work":
        return pending_work_tool(target)
    elif name == "set_agent_paused":
        return set_agent_paused_tool(target, _on_flag(args.get("paused", True)))

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
