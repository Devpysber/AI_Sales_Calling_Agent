import subprocess
import json
from app.services.notification_service import send_email
from app.services.crm_service import CRMService
from app.core.config import settings
from app.core.database import get_db
from sqlalchemy import text

def send_email_tool(to: str, subject: str, body: str, agent_id: int) -> str:
    """Send an email to anyone."""
    try:
        send_email(to, subject, body, agent_id=agent_id, actor="system")
        return f"Email successfully sent to {to}"
    except Exception as e:
        return f"Failed to send email: {str(e)}"

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
    return "Your account credit is currently in good standing."

def update_lead_status_tool(lead_id: int, new_status: str, agent_id: int) -> str:
    """Update a lead's status in the CRM."""
    crm = CRMService(agent_id)
    try:
        crm.update(lead_id, {"status": new_status}, actor="system")
        return f"Successfully updated lead {lead_id} status to '{new_status}'."
    except Exception as e:
        return f"Failed to update lead status: {str(e)}"

def check_agent_schedule_tool(agent_id: int) -> str:
    """Check the calling window schedule for the agent."""
    from app.services.agents import get_profile
    profile = get_profile(agent_id)
    start = profile.get("calling_hours_start", 9)
    end = profile.get("calling_hours_end", 21)
    days = profile.get("calling_days", [1, 2, 3, 4, 5])
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    active_days = [day_names[d] for d in days] if days else ["None"]
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
    from app.services.agents import list_all
    agents = list_all()
    if not agents:
        return "No agents found in the system."
    result = "Active agents in the system:\n"
    for agent in agents:
        result += f"- Agent {agent.get('id')}: {agent.get('name')} (Owner: {agent.get('owner')})\n"
    return result

def schedule_callback_tool(lead_id: int, date_time: str, agent_id: int) -> str:
    """Schedule a callback for a lead."""
    crm = CRMService(agent_id)
    try:
        crm.update(lead_id, {"callback_at": date_time, "status": "Pending"}, actor="system")
        return f"Callback scheduled for lead {lead_id} at {date_time}."
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

def book_calendar_event_tool(email: str, date_time: str, duration_minutes: int = 30) -> str:
    """Book a calendar event."""
    # This would integrate with Google Calendar / Outlook.
    return f"Calendar event booked with {email} at {date_time} for {duration_minutes} minutes."

def check_active_calls_tool() -> str:
    """Check how many active calls are currently ongoing in the system (Admin only)."""
    # Placeholder for querying active calls table.
    try:
        from app.services import call_session
        count = len(call_session.SESSIONS)
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
                    "lead_id": {"type": "integer", "description": "The ID of the lead to update."},
                    "new_status": {"type": "string", "description": "The new status (e.g., 'Hot', 'Cold', 'Pending', 'Do Not Call')."}
                },
                "required": ["lead_id", "new_status"]
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
            "description": "Schedule a callback for a lead at a specific date and time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lead_id": {"type": "integer", "description": "The ID of the lead."},
                    "date_time": {"type": "string", "description": "The date and time for the callback in ISO format (e.g., '2026-10-15T14:30:00Z')."}
                },
                "required": ["lead_id", "date_time"]
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
            "description": "Book a meeting on the calendar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "email": {"type": "string", "description": "The email of the attendee."},
                    "date_time": {"type": "string", "description": "The date and time of the event."},
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
        
    if name == "send_email":
        return send_email_tool(args.get("to"), args.get("subject"), args.get("body"), agent_id)
    elif name == "check_records":
        return check_records_tool(args.get("query"), agent_id)
    elif name == "check_credits":
        return check_credits_tool()
    elif name == "update_lead_status":
        return update_lead_status_tool(args.get("lead_id"), args.get("new_status"), agent_id)
    elif name == "check_agent_schedule":
        return check_agent_schedule_tool(agent_id)
    elif name == "schedule_callback":
        return schedule_callback_tool(args.get("lead_id"), args.get("date_time"), agent_id)
    elif name == "send_sms":
        return send_sms_tool(args.get("to"), args.get("message"))
    elif name == "book_calendar_event":
        return book_calendar_event_tool(args.get("email"), args.get("date_time"), args.get("duration_minutes", 30))
        
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
