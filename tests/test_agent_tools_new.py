"""New customer tools: check_availability, collect_feedback, apply_discount_code, track_shipment,
request_review, transfer_to_department."""

import itertools

from app.core.database import get_db
from app.models.channel import Conversation, ToolDefinition
from app.models.crm import Task
from app.services import agent_tools, tool_registry
from app.services.crm_service import CRMService

_counter = itertools.count()


def _agent(client):
    name = f"NewTools Co {next(_counter)}"
    resp = client.post("/api/agents", json={"name": name, "profile": {"company_name": name}})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _lead(agent_id):
    crm = CRMService(agent_id)
    lead = crm.create({"phone": "+911234567890", "name": "Test Lead"}, actor="team")
    return lead["id"]


def _enable_http_tool(agent_id, name):
    with get_db() as db:
        db.add(ToolDefinition(agent_id=agent_id, name=name, description="x", type="http",
                              parameters_json="{}", config_json="{}", risk="read", enabled=True))


def test_check_availability_not_configured(client):
    agent_id = _agent(client)
    lead_id = _lead(agent_id)
    assert "isn't connected" in agent_tools.check_availability_tool(agent_id, lead_id, "widget").lower()


def test_check_availability_orchestrates_configured_tool(client, monkeypatch):
    agent_id = _agent(client)
    lead_id = _lead(agent_id)
    _enable_http_tool(agent_id, "availability_check")
    monkeypatch.setattr(tool_registry, "run_http_tool", lambda defn, args, ctx: {"ok": True, "result": "In stock"})
    assert agent_tools.check_availability_tool(agent_id, lead_id, "widget") == "In stock"


def test_apply_discount_code_not_configured(client):
    agent_id = _agent(client)
    lead_id = _lead(agent_id)
    assert "no discount" in agent_tools.apply_discount_code_tool(agent_id, lead_id, "SAVE10").lower()


def test_apply_discount_code_orchestrates_configured_tool(client, monkeypatch):
    agent_id = _agent(client)
    lead_id = _lead(agent_id)
    _enable_http_tool(agent_id, "discount_code_check")
    monkeypatch.setattr(tool_registry, "run_http_tool", lambda defn, args, ctx: {"ok": True, "result": "10% off applied"})
    assert agent_tools.apply_discount_code_tool(agent_id, lead_id, "SAVE10") == "10% off applied"


def test_track_shipment_not_configured(client):
    agent_id = _agent(client)
    lead_id = _lead(agent_id)
    assert "isn't connected" in agent_tools.track_shipment_tool(agent_id, lead_id, "REF123").lower()


def test_track_shipment_orchestrates_configured_tool(client, monkeypatch):
    agent_id = _agent(client)
    lead_id = _lead(agent_id)
    _enable_http_tool(agent_id, "shipment_tracking")
    monkeypatch.setattr(tool_registry, "run_http_tool", lambda defn, args, ctx: {"ok": True, "result": "Out for delivery"})
    assert agent_tools.track_shipment_tool(agent_id, lead_id, "REF123") == "Out for delivery"


def test_collect_feedback_creates_task_and_event(client):
    agent_id = _agent(client)
    lead_id = _lead(agent_id)
    result = agent_tools.collect_feedback_tool(agent_id, lead_id, rating="5", comment="Great service")
    assert "noted" in result.lower()
    with get_db() as db:
        task = db.query(Task).filter(Task.agent_id == agent_id, Task.lead_id == lead_id).first()
        assert task is not None
        assert "feedback" in task.title.lower()
        assert task.description == "Great service"
    from app.services import events
    rows = events.list_events(agent_id, type_prefix="feedback", limit=10)
    assert any(e.get("type") == "feedback.received" for e in rows)


def test_request_review_logs_task_without_crashing(client):
    agent_id = _agent(client)
    lead_id = _lead(agent_id)
    result = agent_tools.request_review_tool(agent_id, lead_id)
    assert "review" in result.lower()
    with get_db() as db:
        task = db.query(Task).filter(Task.agent_id == agent_id, Task.lead_id == lead_id, Task.title == "Review requested").first()
        assert task is not None


def test_transfer_to_department_pauses_conversation_and_logs_task(client):
    agent_id = _agent(client)
    lead_id = _lead(agent_id)
    with get_db() as db:
        conv = Conversation(agent_id=agent_id, lead_id=lead_id, channel="whatsapp", status="open")
        db.add(conv)
        db.flush()
        conv_id = conv.id

    result = agent_tools.transfer_to_department_tool(agent_id, lead_id, conv_id, "billing", "wrong charge")
    assert "billing" in result.lower()
    with get_db() as db:
        conv = db.get(Conversation, conv_id)
        assert conv.ai_paused is True
        task = db.query(Task).filter(Task.agent_id == agent_id, Task.title == "Route to billing").first()
        assert task is not None


def test_ensure_builtin_defaults_picks_up_new_tools(client):
    agent_id = _agent(client)
    with get_db() as db:
        db.query(ToolDefinition).filter(ToolDefinition.agent_id == agent_id).delete()
        db.add(ToolDefinition(agent_id=agent_id, name="search_knowledge", description="old", type="builtin",
                              risk="read", enabled=True))
    tool_registry.invalidate_seed_cache(agent_id)
    tool_registry.ensure_builtin_defaults(agent_id)
    with get_db() as db:
        names = {r.name for r in db.query(ToolDefinition).filter(ToolDefinition.agent_id == agent_id).all()}
    for new_name in ("check_availability", "collect_feedback", "apply_discount_code", "track_shipment",
                      "request_review", "transfer_to_department"):
        assert new_name in names
