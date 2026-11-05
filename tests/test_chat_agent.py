"""Chat-channel tool-calling loop: native and JSON-fallback paths, isolation, and permission checks."""

import json

from sqlalchemy import select

from app.core.database import get_db
from app.models.channel import ToolDefinition, ToolRun
from app.services import agent_tools, chat_agent, llm
from app.services.crm_service import CRMService


def _lead(agent_id):
    return CRMService(agent_id).create({"phone": "9811100099", "name": "Test Customer", "city": "Bhopal"}, actor="system")


def _tool_runs(agent_id):
    with get_db() as db:
        return list(db.scalars(select(ToolRun).where(ToolRun.agent_id == agent_id).order_by(ToolRun.id)))


def test_execute_tool_rejects_team_tool_for_customer_role(base, client):
    agent_id = int(base.rsplit("/", 1)[1])
    result = agent_tools.execute_tool("send_email", json.dumps({"to": "x@y.com", "subject": "s", "body": "b"}), agent_id, role="customer")
    assert "Access Denied" in result or "Unknown tool" in result


def test_crafted_lead_id_is_ignored(base, client, monkeypatch):
    """A tool argument the model invents for lead_id must never override the real conversation's lead."""
    agent_id = int(base.rsplit("/", 1)[1])
    real_lead = _lead(agent_id)
    other_lead = CRMService(agent_id).create({"phone": "9811100098", "name": "Someone Else"}, actor="system")

    result, status = chat_agent._run_tool("get_my_details", {"lead_id": other_lead["id"]}, agent_id, None, real_lead["id"])
    assert status == "ok"
    data = json.loads(result)
    assert data.get("name") == "Test Customer"
    assert "Someone Else" not in result


def test_native_tool_loop_runs_each_tool_and_logs(base, client, monkeypatch):
    agent_id = int(base.rsplit("/", 1)[1])
    lead = _lead(agent_id)

    monkeypatch.setattr(llm, "tools_via_openrouter", lambda: True)

    calls = {"n": 0}

    def fake_stream(messages, max_tokens=300, temperature=0.4, tools=None, deadline=None):
        calls["n"] += 1
        if calls["n"] == 1:
            yield {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "get_my_details", "arguments": "{}"}}]}
        else:
            yield "Here are your details."

    monkeypatch.setattr(llm, "stream", fake_stream)

    res = chat_agent.respond(agent_id, None, [], "what are my details?", lead, channel="whatsapp")
    assert res["reply"] == "Here are your details."
    assert res["tools_used"] == [{"name": "get_my_details", "status": "ok"}]
    runs = _tool_runs(agent_id)
    assert runs and runs[-1].tool_name == "get_my_details" and runs[-1].status == "ok"


def test_all_six_customer_tools_execute_and_log(base, client, monkeypatch):
    agent_id = int(base.rsplit("/", 1)[1])
    lead = _lead(agent_id)
    names = [t["function"]["name"] for t in agent_tools.CUSTOMER_TOOLS]
    assert names[:6] == ["search_knowledge", "schedule_callback", "set_meeting", "get_my_details", "handoff_to_human", "create_support_ticket"]

    args_by_name = {
        "search_knowledge": {"question": "opening hours"},
        "schedule_callback": {"date_time": "2099-01-01 10:00"},
        "set_meeting": {"date_time": "2099-01-01 11:00"},
        "get_my_details": {},
        "handoff_to_human": {"reason": "wants a person"},
        "create_support_ticket": {"title": "Broken widget"},
    }
    for name in names[:6]:
        result, status = chat_agent._run_tool(name, args_by_name[name], agent_id, None, lead["id"])
        assert status in ("ok", "error"), f"{name}: {result}"

    runs = _tool_runs(agent_id)
    assert set(names[:6]) <= {r.tool_name for r in runs}


def test_expanded_customer_tools_execute_and_log(base, client, monkeypatch):
    """Every new (post-Day-3) CUSTOMER_TOOLS tool executes against a realistic context and logs to tool_runs."""
    agent_id = int(base.rsplit("/", 1)[1])
    lead = _lead(agent_id)
    names = [t["function"]["name"] for t in agent_tools.CUSTOMER_TOOLS][6:]
    assert names, "expected new tools beyond the original 6"

    args_by_name = {
        "reschedule_meeting": {"date_time": "2099-01-02 10:00"},
        "cancel_meeting": {"reason": "can't make it"},
        "update_lead_requirement": {"requirement": "3BHK", "budget": "50L", "city": "Bhopal"},
        "check_my_order_or_deal_status": {},
        "send_brochure": {"link": "https://example.com/brochure.pdf"},
        "request_document_from_customer": {"what": "ID proof"},
        "send_quick_replies": {"body": "Pick one", "options": ["A", "B"]},
        "send_options_list": {"body": "Pick one", "options": ["A", "B", "C"]},
        "share_location": {"latitude": 23.25, "longitude": 77.41, "name": "Our office"},
        "send_payment_link": {"amount": "500", "purpose": "booking"},
        "check_ticket_status": {},
        "escalate_urgent": {"reason": "payment stuck"},
        "set_language_preference": {"language": "Hindi"},
        "opt_out": {},
    }
    for name in names:
        result, status = chat_agent._run_tool(name, args_by_name.get(name, {}), agent_id, None, lead["id"])
        assert status in ("ok", "error", "pending_confirm"), f"{name}: {result}"

    runs = _tool_runs(agent_id)
    assert set(names) <= {r.tool_name for r in runs}


def test_send_payment_link_not_configured(client):
    # A fresh agent, not the shared `base` one: `base` is session-scoped and other tests in this file
    # already call send_payment_link on it, which would otherwise leave a stale pending_confirm behind.
    agent_id = client.post("/api/agents", json={"name": "Payment Test Co"}).json()["id"]
    lead = _lead(agent_id)
    result, status = chat_agent._run_tool("send_payment_link", {"amount": "100"}, agent_id, None, lead["id"])
    assert status == "pending_confirm"  # risk="money" asks to confirm first
    # confirm it, now the underlying tool actually runs and reports "not configured"
    result, status = chat_agent._run_tool("send_payment_link", {"amount": "100"}, agent_id, None, lead["id"])
    assert status == "ok"
    assert "not configured" in result.lower()


def test_money_risk_hits_confirmation_gate(client):
    agent_id = client.post("/api/agents", json={"name": "Risk Tier Test Co"}).json()["id"]
    with get_db() as db:
        row = db.query(ToolDefinition).filter(ToolDefinition.agent_id == agent_id, ToolDefinition.name == "send_payment_link").first()
        assert row and row.risk == "money"


def test_crafted_lead_id_ignored_for_new_write_tools(client):
    """Same isolation guarantee as test_crafted_lead_id_is_ignored, re-run against new write-capable tools."""
    agent_id = client.post("/api/agents", json={"name": "Isolation Test Co"}).json()["id"]
    real_lead = _lead(agent_id)
    other_lead = CRMService(agent_id).create({"phone": "9811100097", "name": "Not Me"}, actor="system")

    chat_agent._run_tool("opt_out", {"lead_id": other_lead["id"]}, agent_id, None, real_lead["id"])
    assert CRMService(agent_id).get(real_lead["id"])["do_not_call"] is True
    assert CRMService(agent_id).get(other_lead["id"])["do_not_call"] is False

    chat_agent._run_tool("set_language_preference", {"lead_id": other_lead["id"], "language": "Tamil"}, agent_id, None, real_lead["id"])
    real_lang = CRMService(agent_id).get(real_lead["id"])["language"]
    assert real_lang not in ("en-IN", None, "")
    assert CRMService(agent_id).get(other_lead["id"])["language"] != real_lang

    chat_agent._run_tool("update_lead_requirement", {"lead_id": other_lead["id"], "city": "Indore"}, agent_id, None, real_lead["id"])
    assert CRMService(agent_id).get(real_lead["id"])["city"] == "Indore"
    assert CRMService(agent_id).get(other_lead["id"])["city"] != "Indore"


def test_json_fallback_when_no_native_tools(base, client, monkeypatch):
    agent_id = int(base.rsplit("/", 1)[1])
    lead = _lead(agent_id)

    monkeypatch.setattr(llm, "tools_via_openrouter", lambda: False)

    calls = {"n": 0}

    def fake_complete(messages, json_mode=False, max_tokens=500, temperature=0.3, **_):
        calls["n"] += 1
        if calls["n"] == 1:
            text = json.dumps({"reply": "", "tool": {"name": "get_my_details", "arguments": {}}})
        else:
            text = json.dumps({"reply": "Your name is Test Customer.", "tool": None})
        return llm.LLMResult(text, "fake", "fake-model", 1)

    monkeypatch.setattr(llm, "complete", fake_complete)

    res = chat_agent.respond(agent_id, None, [], "what's my name?", lead, channel="whatsapp")
    assert res["reply"] == "Your name is Test Customer."
    assert res["tools_used"] == [{"name": "get_my_details", "status": "ok"}]
    runs = _tool_runs(agent_id)
    assert runs[-1].tool_name == "get_my_details"
