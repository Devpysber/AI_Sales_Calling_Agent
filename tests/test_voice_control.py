"""What a colleague or admin can actually do by voice, and what a team member must not reach."""

import json

import pytest

from app.services import agent_tools


@pytest.fixture(scope="module")
def agent_id(client):
    return client.post("/api/agents", json={"name": "Voice control test"}).json()["id"]


def run(name: str, args: str, agent_id: int, role: str = "team") -> str:
    return agent_tools.execute_tool(name, json.loads(args), agent_id, role=role)


def test_a_colleague_can_read_one_lead_back(client, agent_id):
    base = f"/api/agents/{agent_id}"
    client.post(f"{base}/leads", json={"name": "Rahul Verma", "phone": "+919812300123",
                                       "requirements": "Wants a 2BHK in Indore"})
    said = run("lead_details", '{"lead": "Rahul"}', agent_id)
    assert "Rahul Verma" in said and "+919812300123" in said


def test_a_colleague_can_ask_what_the_agent_would_tell_a_customer(agent_id):
    from app.services import rag
    rag.add_text(agent_id, "Pricing", "The Premium listing plan costs 2999 rupees for thirty days.", actor="test")
    for _ in range(60):
        if rag.stats(agent_id)["chunks"]:
            break
        import time
        time.sleep(0.05)
    assert "2999" in run("knowledge_search", '{"question": "what does premium cost"}', agent_id)


def test_the_persona_and_document_list_are_readable(agent_id):
    assert "Voice control test" in run("persona", "{}", agent_id) or "Ashish" in run("persona", "{}", agent_id)
    assert "document" in run("knowledge_list", "{}", agent_id).lower()


def test_a_team_member_cannot_text_a_stranger(agent_id):
    refused = run("send_sms", '{"to": "+447700900123", "message": "hello"}', agent_id)
    assert "not one of this agent" in refused


def test_an_admin_is_offered_the_cross_agent_argument_and_a_team_member_is_not():
    team = {t["function"]["name"]: t for t in agent_tools.get_tools_for_role("team")}
    admin = {t["function"]["name"]: t for t in agent_tools.get_tools_for_role("admin")}
    assert "agent" not in team["dial_lead"]["function"]["parameters"]["properties"]
    assert "agent" in admin["dial_lead"]["function"]["parameters"]["properties"]
    # ...and the admin-only tools stay admin-only.
    assert "all_agents_overview" in admin and "all_agents_overview" not in team


def test_an_agent_named_by_voice_is_resolved_not_silently_ignored():
    """A misheard name used to be coerced to None and the command landed on the wrong desk."""
    schema = {t["function"]["name"]: t for t in agent_tools.ADMIN_TOOLS}
    for name in ("get_agent_config", "pause_agent_automation"):
        kind = schema[name]["function"]["parameters"]["properties"]["target_agent_id"]["type"]
        assert kind == "string", f"{name} still takes an integer, so a spoken name becomes None"
