import subprocess
import json
from datetime import datetime, timezone, timedelta
from app.services.notification_service import send_email
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
        send_email(to, subject, body, agent_id=agent_id, actor="system")
        return f"Email successfully sent to {to}"
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
    value = str(new_status or "").strip()
    if value in QUALIFICATIONS:
        field = "qualification"
    elif value in JOURNEY or value in EXIT_STAGES:
        field = "status"
    else:
        allowed = ", ".join(list(JOURNEY) + sorted(EXIT_STAGES) + sorted(QUALIFICATIONS))
        return f"Invalid status '{new_status}'. Allowed values: {allowed}."
    try:
        crm.update(lead_id, {field: value}, actor="system")
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
    query = str(lead or "").strip()
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
        crm.update(lead_id, {"callback_at": when, "call_status": "Pending"}, actor="system")
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
        crm.update(lead["id"], {"meeting_at": when}, actor="system")
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

def execute_tool(name: str, arguments: str, agent_id: int, role: str = "team") -> str:
    """Execute a tool by name and return its result, checking permissions."""
    try:
        args = json.loads(arguments)
    except Exception:
        return "Failed to parse arguments."
    try:
        return _dispatch(name, args, agent_id, role)
    except Exception as e:
        log.exception("tool %s failed", name)
        return f"Tool {name} failed: {e}"


def _dispatch(name: str, args: dict, agent_id: int, role: str) -> str:
    if name == "send_email":
        return send_email_tool(args.get("to"), args.get("subject"), args.get("body"), agent_id)
    elif name == "check_records":
        return check_records_tool(args.get("query"), agent_id)
    elif name == "check_email_status":
        return check_email_status_tool(agent_id, int(args.get("hours") or 24))
    elif name == "check_credits":
        return check_credits_tool()
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

    # Admin tools
    if role == "admin":
        if name == "system_diagnostics":
            return system_diagnostics_tool()
        elif name == "list_all_agents":
            return list_all_agents_tool()
        elif name == "get_agent_config":
            return get_agent_config_tool(args.get("target_agent_id"))
        elif name == "pause_agent_automation":
            return pause_agent_automation_tool(args.get("target_agent_id"))
        elif name == "check_active_calls":
            return check_active_calls_tool()

    return f"Access Denied or Unknown tool: {name}"
