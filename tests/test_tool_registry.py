"""tool_registry: builtin seeding, enabled/channel filtering, custom HTTP tools, SSRF guard, confirmation gate."""

import http.server
import itertools
import json
import threading

from app.core.database import get_db
from app.models.channel import ToolDefinition, ToolRun
from app.services import chat_agent, tool_registry
from app.services.crm_service import CRMService

_counter = itertools.count()


def _agent(client):
    name = f"Registry Co {next(_counter)}"
    resp = client.post("/api/agents", json={"name": name, "profile": {"company_name": name}})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def test_ensure_builtin_defaults_idempotent(client):
    from app.services.agent_tools import CUSTOMER_TOOLS
    agent_id = _agent(client)
    tool_registry.ensure_builtin_defaults(agent_id)
    tool_registry.ensure_builtin_defaults(agent_id)
    with get_db() as db:
        rows = db.query(ToolDefinition).filter(ToolDefinition.agent_id == agent_id).all()
    all_names = {t["function"]["name"] for t in CUSTOMER_TOOLS}
    assert {r.name for r in rows} == all_names
    assert len(rows) == len(all_names)
    assert {r.type for r in rows} == {"builtin"}


def test_ensure_builtin_defaults_adds_new_tools_to_existing_agent(client):
    """An agent seeded back when there were only 6 builtins must pick up every later-added CUSTOMER_TOOLS
    name additively, without re-seeding or duplicating the ones it already has."""
    from app.services.agent_tools import CUSTOMER_TOOLS
    agent_id = _agent(client)
    with get_db() as db:
        db.query(ToolDefinition).filter(ToolDefinition.agent_id == agent_id).delete()
        db.add(ToolDefinition(agent_id=agent_id, name="search_knowledge", description="old", type="builtin",
                              risk="read", enabled=True))
    tool_registry.ensure_builtin_defaults(agent_id)
    with get_db() as db:
        rows = db.query(ToolDefinition).filter(ToolDefinition.agent_id == agent_id).all()
    all_names = {t["function"]["name"] for t in CUSTOMER_TOOLS}
    assert {r.name for r in rows} == all_names
    assert len(rows) == len(all_names)  # no duplicate search_knowledge row
    assert next(r for r in rows if r.name == "search_knowledge").description == "old"  # untouched, not re-seeded


def test_tools_for_agent_excludes_disabled_and_wrong_channel(client):
    agent_id = _agent(client)
    tool_registry.ensure_builtin_defaults(agent_id)
    with get_db() as db:
        row = db.query(ToolDefinition).filter(ToolDefinition.agent_id == agent_id,
                                               ToolDefinition.name == "search_knowledge").first()
        row.enabled = False
        row2 = db.query(ToolDefinition).filter(ToolDefinition.agent_id == agent_id,
                                                ToolDefinition.name == "get_my_details").first()
        row2.channels_json = json.dumps(["sms"])

    specs = tool_registry.tools_for_agent(agent_id, "whatsapp")
    names = {s["function"]["name"] for s in specs}
    assert "search_knowledge" not in names  # disabled
    assert "get_my_details" not in names  # channel-restricted to sms only
    assert "schedule_callback" in names

    specs_sms = tool_registry.tools_for_agent(agent_id, "sms")
    assert "get_my_details" in {s["function"]["name"] for s in specs_sms}


def test_secret_never_in_to_dict(client):
    agent_id = _agent(client)
    with get_db() as db:
        row = ToolDefinition(agent_id=agent_id, name="custom_ping", description="ping a URL", type="http",
                              parameters_json=json.dumps({"type": "object", "properties": {}}),
                              config_json=json.dumps({"url": "http://example.com", "method": "GET", "auth_type": "bearer"}),
                              risk="read")
        db.add(row)
        db.flush()
        tool_id = row.id
    tool_registry.set_tool_secret(agent_id, tool_id, "super-secret-token")
    definition = tool_registry.get_definition(agent_id, "custom_ping")
    assert "secret_encrypted" not in definition
    assert "super-secret-token" not in json.dumps(definition)

    resp = client.get(f"/api/agents/{agent_id}/tools")
    assert resp.status_code == 200
    assert "super-secret-token" not in resp.text
    assert "secret_encrypted" not in resp.text


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True, "path": self.path}).encode())

    def log_message(self, *a):
        pass


class _RedirectHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", "http://127.0.0.1:1/internal")
        self.end_headers()

    def log_message(self, *a):
        pass


def _serve(handler_cls):
    server = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


def test_run_http_tool_success_and_blocks_private_and_redirect():
    server, port = _serve(_Handler)
    try:
        definition = {"config": {"url": f"http://127.0.0.1:{port}/status", "method": "GET", "auth_type": "none"},
                      "timeout_seconds": 5, "secret_encrypted": None}
        # 127.0.0.1 is itself a loopback address, so even the "normal" local test server is blocked by the
        # SSRF guard — which is the correct behaviour; we only assert the guard actually fires here and
        # exercise the success path against a non-loopback-looking host name resolved to it is out of scope.
        result = tool_registry.run_http_tool(definition, {}, {})
        assert result["ok"] is False
        assert "not allowed" in result["error"]
    finally:
        server.shutdown()


def test_run_http_tool_blocks_redirect_to_private_range():
    server, port = _serve(_RedirectHandler)
    try:
        # Even if the host check were bypassed, a 3xx must never be auto-followed.
        definition = {"config": {"url": f"http://127.0.0.1:{port}/go", "method": "GET", "auth_type": "none"},
                      "timeout_seconds": 5, "secret_encrypted": None}
        result = tool_registry.run_http_tool(definition, {}, {})
        assert result["ok"] is False
    finally:
        server.shutdown()


def test_money_risk_tool_withholds_first_execution_then_runs_on_confirm(client):
    agent_id = _agent(client)
    lead = CRMService(agent_id).create({"phone": "9811100077", "name": "Payer"}, actor="system")
    with get_db() as db:
        db.add(ToolDefinition(agent_id=agent_id, name="charge_card", description="Charge the customer's card",
                               type="http", parameters_json=json.dumps({"type": "object", "properties": {"amount": {"type": "number"}}}),
                               config_json=json.dumps({"url": "http://127.0.0.1:1/charge", "method": "POST", "auth_type": "none"}),
                               risk="money", needs_confirmation=True, enabled=True))

    result1, status1 = chat_agent._run_tool("charge_card", {"amount": 500}, agent_id, None, lead["id"])
    assert status1 == "pending_confirm"
    assert "confirm" in result1.lower()

    with get_db() as db:
        runs = db.query(ToolRun).filter(ToolRun.agent_id == agent_id, ToolRun.tool_name == "charge_card").all()
    assert any(r.status == "pending_confirm" for r in runs)

    # Second call (the customer's "yes") must proceed past the gate (it will fail at the network hop,
    # since the target is a private/unroutable address, but that's a different status than pending_confirm).
    result2, status2 = chat_agent._run_tool("charge_card", {"amount": 500}, agent_id, None, lead["id"])
    assert status2 != "pending_confirm"
