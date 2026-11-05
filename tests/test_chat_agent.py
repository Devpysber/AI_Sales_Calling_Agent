"""Chat-channel tool-calling loop: native and JSON-fallback paths, isolation, and permission checks."""

import json

from sqlalchemy import select

from app.core.database import get_db
from app.models.channel import ToolRun
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
    assert names == ["search_knowledge", "schedule_callback", "set_meeting", "get_my_details", "handoff_to_human", "create_support_ticket"]

    args_by_name = {
        "search_knowledge": {"question": "opening hours"},
        "schedule_callback": {"date_time": "2099-01-01 10:00"},
        "set_meeting": {"date_time": "2099-01-01 11:00"},
        "get_my_details": {},
        "handoff_to_human": {"reason": "wants a person"},
        "create_support_ticket": {"title": "Broken widget"},
    }
    for name in names:
        result, status = chat_agent._run_tool(name, args_by_name[name], agent_id, None, lead["id"])
        assert status in ("ok", "error"), f"{name}: {result}"

    runs = _tool_runs(agent_id)
    assert {r.tool_name for r in runs} == set(names)


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
