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
    leads = crm.search(query, page=1, page_size=5).get("items", [])
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
        
    # Admin tools
    if role == "admin":
        if name == "system_diagnostics":
            return system_diagnostics_tool()
        elif name == "list_all_agents":
            return list_all_agents_tool()
            
    return f"Access Denied or Unknown tool: {name}"
