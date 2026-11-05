"""Source of truth for "which tools can this agent's chat use": the `tool_definitions` table.

Builtin tools (type="builtin") carry only name/risk/config in the row; their full OpenAI-function-schema
(description + parameters) still lives in `agent_tools.CUSTOMER_TOOLS`, looked up by name here, so the
schema isn't duplicated across two places. Custom tools (type="http") carry their own schema in
`parameters_json`/`description` since there is no other source for it.
"""

import ipaddress
import json
import logging
import socket
import time
from urllib.parse import urlsplit

import httpx

from app.core.secrets import decrypt_value, encrypt_value
from app.services.agent_tools import CUSTOMER_TOOLS

log = logging.getLogger(__name__)

MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # 2MB cap, same order of magnitude as the WhatsApp media cap

_BUILTIN_RISK = {
    "search_knowledge": "read",
    "schedule_callback": "write",
    "set_meeting": "write",
    "get_my_details": "read",
    "handoff_to_human": "write",
    "create_support_ticket": "write",
}


def ensure_builtin_defaults(agent_id: int) -> None:
    """Idempotently seed this agent's 6 builtin ToolDefinition rows, if it has none yet."""
    from app.core.database import get_db
    from app.models.channel import ToolDefinition

    with get_db() as db:
        existing = db.query(ToolDefinition.id).filter(ToolDefinition.agent_id == agent_id).first()
        if existing:
            return
        for tool in CUSTOMER_TOOLS:
            name = tool["function"]["name"]
            db.add(ToolDefinition(
                agent_id=agent_id, name=name, description=tool["function"].get("description", ""),
                type="builtin", risk=_BUILTIN_RISK.get(name, "read"), enabled=True,
            ))


def _builtin_schema(name: str) -> dict | None:
    for tool in CUSTOMER_TOOLS:
        if tool["function"]["name"] == name:
            return tool
    return None


def _http_schema(definition) -> dict:
    params = json.loads(definition.parameters_json) if definition.parameters_json else \
        {"type": "object", "properties": {}}
    return {"type": "function", "function": {
        "name": definition.name, "description": definition.description or "", "parameters": params}}


def tools_for_agent(agent_id: int, channel: str) -> list[dict]:
    """OpenAI-function-schema tool specs for every enabled ToolDefinition of this agent on `channel`."""
    from app.core.database import get_db
    from app.models.channel import ToolDefinition

    ensure_builtin_defaults(agent_id)
    specs: list[dict] = []
    with get_db() as db:
        rows = db.query(ToolDefinition).filter(ToolDefinition.agent_id == agent_id, ToolDefinition.enabled.is_(True)).all()
        for row in rows:
            if row.channels_json:
                try:
                    allowed = json.loads(row.channels_json)
                except Exception:
                    allowed = None
                if allowed and channel not in allowed:
                    continue
            if row.type == "builtin":
                schema = _builtin_schema(row.name)
                if schema:
                    specs.append(schema)
            elif row.type == "http":
                specs.append(_http_schema(row))
    return specs


def get_definition(agent_id: int, name: str) -> dict | None:
    """Single lookup used at execution time: the ToolDefinition row (as a dict) for this agent/tool name."""
    from app.core.database import get_db
    from app.models.channel import ToolDefinition

    with get_db() as db:
        row = db.query(ToolDefinition).filter(ToolDefinition.agent_id == agent_id, ToolDefinition.name == name).first()
        return row.to_dict() if row else None


def set_tool_secret(agent_id: int, tool_id: int, secret: str | None) -> None:
    """Encrypt and store a custom tool's auth secret; never handled or returned in plaintext elsewhere."""
    from app.core.database import get_db
    from app.models.channel import ToolDefinition

    with get_db() as db:
        row = db.query(ToolDefinition).filter(ToolDefinition.agent_id == agent_id, ToolDefinition.id == tool_id).first()
        if row:
            row.secret_encrypted = encrypt_value(secret) if secret else None


# ---------------------------------------------------------------------------
# Custom HTTP tool execution
# ---------------------------------------------------------------------------

_PRIVATE_NETS = [
    ipaddress.ip_network("10.0.0.0/8"), ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"), ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"), ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]


def _is_private_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True  # unresolvable: treat as unsafe
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if any(ip in net for net in _PRIVATE_NETS) or ip.is_loopback or ip.is_link_local or ip.is_private:
            return True
    return False


def _substitute(template: str, arguments: dict, context: dict) -> str:
    tokens = {**{k: v for k, v in arguments.items() if isinstance(v, (str, int, float))},
              "lead_phone": context.get("lead_phone", ""), "lead_name": context.get("lead_name", "")}
    try:
        return template.format(**tokens)
    except (KeyError, IndexError):
        return template


def run_http_tool(definition: dict, arguments: dict, context: dict | None = None) -> dict:
    """Execute a type="http" ToolDefinition. `context` carries server-trusted lead_phone/lead_name for the
    reserved {lead_phone}/{lead_name} template tokens; the model's own `arguments` can never override them
    (they are applied after, and only via the template substitution, never as raw argument keys).

    SSRF guard: the target host (and every redirect hop) is resolved and rejected if it lands on a private/
    loopback/link-local range. Redirects are not auto-followed (follow_redirects=False); any 3xx response
    is treated as a failed/blocked hop rather than chased.
    """
    context = context or {}
    config = definition.get("config") or {}
    url_template = config.get("url", "")
    method = (config.get("method") or "GET").upper()
    timeout = definition.get("timeout_seconds") or 10
    url = _substitute(url_template, arguments or {}, context)

    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return {"ok": False, "result": "", "error": "Invalid tool URL."}
    if _is_private_host(parsed.hostname):
        return {"ok": False, "result": "", "error": "Target host is not allowed (private/loopback range)."}

    headers = {}
    secret = decrypt_value(definition.get("secret_encrypted"))
    auth_type = config.get("auth_type", "none")
    if auth_type == "bearer" and secret:
        headers["Authorization"] = f"Bearer {secret}"
    elif auth_type == "api_key" and secret:
        headers[config.get("auth_header", "X-API-Key")] = secret

    kwargs: dict = {"headers": headers, "timeout": timeout}
    if method in ("GET", "DELETE"):
        kwargs["params"] = arguments or {}
    else:
        kwargs["json"] = arguments or {}

    try:
        with httpx.Client(follow_redirects=False) as client:
            resp = client.request(method, url, **kwargs)
    except httpx.TimeoutException:
        return {"ok": False, "result": "", "error": "Tool request timed out."}
    except httpx.HTTPError as e:
        return {"ok": False, "result": "", "error": f"Tool request failed: {e}"}

    if 300 <= resp.status_code < 400:
        return {"ok": False, "result": "", "error": "Redirects are not followed for custom tools."}

    content = resp.content[:MAX_RESPONSE_BYTES]
    text = content.decode(resp.encoding or "utf-8", errors="replace")
    if resp.status_code >= 400:
        return {"ok": False, "result": text[:2000], "error": f"HTTP {resp.status_code}"}
    return {"ok": True, "result": text[:4000], "error": None}
