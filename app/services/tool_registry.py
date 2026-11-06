"""Source of truth for "which tools can this agent's chat use": the `tool_definitions` table.

Builtin tools (type="builtin") carry only name/risk/config in the row; their full OpenAI-function-schema
(description + parameters) still lives in `agent_tools.CUSTOMER_TOOLS`, looked up by name here, so the
schema isn't duplicated across two places. Custom tools (type="http") carry their own schema in
`parameters_json`/`description` since there is no other source for it.
"""

import contextlib
import ipaddress
import json
import logging
import re
import socket
import threading
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
    "reschedule_meeting": "write",
    "cancel_meeting": "write",
    "update_lead_requirement": "write",
    "check_my_order_or_deal_status": "read",
    "send_brochure": "write",
    "request_document_from_customer": "write",
    "send_quick_replies": "write",
    "send_options_list": "write",
    "share_location": "read",
    "send_payment_link": "money",
    "check_ticket_status": "read",
    "escalate_urgent": "write",
    "set_language_preference": "write",
    "opt_out": "write",
}


_seeded_cache: set[int] = set()
_SEEDED_CACHE_LOCK = threading.Lock()


def invalidate_seed_cache(agent_id: int | None = None) -> None:
    """Drop `agent_id` (or everything, if None) from the seeded-agents cache. Test-reset hook only."""
    with _SEEDED_CACHE_LOCK:
        if agent_id is None:
            _seeded_cache.clear()
        else:
            _seeded_cache.discard(agent_id)


def ensure_builtin_defaults(agent_id: int) -> None:
    """Additively seed every builtin ToolDefinition name in CUSTOMER_TOOLS this agent doesn't have yet.

    Idempotent for names already present (never re-seeds/duplicates them), but still picks up any new
    CUSTOMER_TOOLS name added later - an agent created back when there were only 6 builtins gets the
    rest added here too, instead of being stuck on the original set.
    """
    if agent_id in _seeded_cache:
        return

    from app.core.database import get_db
    from app.models.channel import ToolDefinition

    with get_db() as db:
        existing = {row[0] for row in db.query(ToolDefinition.name).filter(ToolDefinition.agent_id == agent_id)}
        for tool in CUSTOMER_TOOLS:
            name = tool["function"]["name"]
            if name in existing:
                continue
            db.add(ToolDefinition(
                agent_id=agent_id, name=name, description=tool["function"].get("description", ""),
                type="builtin", risk=_BUILTIN_RISK.get(name, "read"), enabled=True,
            ))

    with _SEEDED_CACHE_LOCK:
        _seeded_cache.add(agent_id)


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


def _resolve_pinned_ip(host: str) -> str | None:
    """Resolve `host` once and return a single safe IP literal to connect to, or None if the host is
    unresolvable or any of its answers land in a private/loopback/link-local range (same "any bad answer
    disqualifies the whole host" rule _is_private_host used). Pinning this IP and connecting to it later
    (rather than re-resolving at connect time) closes the DNS-rebinding/TOCTOU window: an attacker who
    controls DNS for the host cannot swap in a private/metadata IP between the check and the real request.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return None
    first_public: str | None = None
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if any(ip in net for net in _PRIVATE_NETS) or ip.is_loopback or ip.is_link_local or ip.is_private:
            return None
        if first_public is None:
            first_public = addr
    return first_public


_DNS_PIN_LOCK = threading.Lock()


@contextlib.contextmanager
def _pinned_dns(hostname: str, ip: str):
    """Force every `socket.getaddrinfo(hostname, ...)` call made while this context is open to resolve to
    the single, already-validated `ip` — so the connection httpx makes is guaranteed to reach the host we
    actually checked, not whatever DNS answers at that later moment. Process-wide and lock-serialized
    (simple and correct over clever: a custom httpx transport/resolver would avoid the lock but is far more
    fragile across httpx/httpcore versions) — an outbound custom-tool call is not latency-critical enough
    for this short, rare serialization to matter.
    """
    with _DNS_PIN_LOCK:
        original = socket.getaddrinfo

        def _patched(host, port=None, *args, **kwargs):
            if host == hostname:
                family = socket.AF_INET6 if ":" in ip else socket.AF_INET
                return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port or 0))]
            return original(host, port, *args, **kwargs)

        socket.getaddrinfo = _patched
        try:
            yield
        finally:
            socket.getaddrinfo = original


_AUTHORITY_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://([^/]*)")


def template_host_is_fixed(template: str) -> bool:
    """A tool's URL template may only use {placeholder} substitution in the path/query/body — never in the
    scheme or host. Otherwise the model (shaped by whatever the customer typed in chat) could choose which
    host the platform's own server connects to, with only the private-IP guard as a backstop. Returns False
    (host is NOT fixed, i.e. reject the template) if a `{` appears in the scheme+authority portion.
    """
    match = _AUTHORITY_RE.match(template or "")
    authority = match.group(1) if match else (template or "")
    return "{" not in authority


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

    SSRF guard: the tool's scheme+host must be fixed by the tool's author (no {placeholder} there — only
    path/query/body may be templated from arguments), the target host is resolved to a single IP and
    validated against private/loopback/link-local ranges, and the actual connection is pinned to that same
    validated IP rather than re-resolved at request time (closes the DNS-rebinding/TOCTOU window). Redirects
    are not auto-followed (follow_redirects=False); any 3xx response is treated as a failed/blocked hop
    rather than chased.
    """
    context = context or {}
    config = definition.get("config") or {}
    url_template = config.get("url", "")
    method = (config.get("method") or "GET").upper()
    timeout = definition.get("timeout_seconds") or 10

    if not template_host_is_fixed(url_template):
        return {"ok": False, "result": "", "error": "Tool URL may not template the host (misconfigured tool)."}
    url = _substitute(url_template, arguments or {}, context)

    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return {"ok": False, "result": "", "error": "Invalid tool URL."}
    pinned_ip = _resolve_pinned_ip(parsed.hostname)
    if pinned_ip is None:
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
        with _pinned_dns(parsed.hostname, pinned_ip):
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
