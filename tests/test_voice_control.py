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


def test_a_colleague_can_read_and_change_who_answers_the_line(agent_id):
    said = run("call_routing", "{}", agent_id)
    assert "In hours" in said and "After hours" in said

    # Forwarding to a team nobody is listed in is refused, before the routing is changed.
    if "nobody is listed to ring" in said:
        assert "transfer number" in run("set_call_routing", '{"when": "after hours", "mode": "team"}', agent_id)
        run("add_team_member", '{"name": "Neha", "phone": "+919812300999"}', agent_id)
    assert "your team" in run("set_call_routing", '{"when": "after hours", "mode": "team"}', agent_id)
    assert "After hours: your team" in run("call_routing", "{}", agent_id)
    # A message instead of answering only makes sense outside hours.
    assert run("set_call_routing", '{"when": "hours", "mode": "message"}', agent_id).startswith("Failed")
    assert "Hand-over off" in run("set_call_routing", '{"handover": false}', agent_id)


def test_a_colleague_can_add_somebody_to_the_ring_order(agent_id):
    # A number of its own: the routing test may already have added Neha to this shared workspace.
    added = run("add_team_member", '{"name": "Vikram", "phone": "+919812300777"}', agent_id)
    assert "Added Vikram" in added
    assert "already on the ring list" in run("add_team_member", '{"name": "Vikram", "phone": "+919812300777"}', agent_id)
    assert run("add_team_member", '{"name": "Short", "phone": "12"}', agent_id).startswith("Failed")


def test_live_calls_and_ending_one_are_honest_when_nothing_is_live(agent_id):
    assert "No calls are live" in run("live_calls", "{}", agent_id)
    assert "No calls are live" in run("end_call", "{}", agent_id)


def test_automation_numbers_are_changed_and_refused_with_the_allowed_range(agent_id):
    assert "is now 5" in run("set_automation_number", '{"setting": "calls per run", "value": 5}', agent_id)
    refused = run("set_automation_number", '{"setting": "calls per run", "value": 500}', agent_id)
    assert refused.startswith("Failed") and "50" in refused      # the caller hears the real limit
    assert run("set_automation_number", '{"setting": "wibble", "value": 2}', agent_id).startswith("Failed")


def test_a_job_can_be_run_on_demand_by_its_everyday_name(agent_id):
    assert "Auto-dial" in run("run_job_now", '{"job": "dialer"}', agent_id)
    assert run("run_job_now", '{"job": "something else"}', agent_id).startswith("Failed")


def test_an_admin_can_run_the_persona_page_from_a_call(client, agent_id):
    """Everything on Persona & playground, spoken: it was unreachable from a call before."""
    assert "voice is now dev" in run("set_persona", '{"setting": "voice", "value": "Dev"}', agent_id).lower()
    assert "couple" in run("set_persona", '{"setting": "calls the person", "value": "couple"}', agent_id)
    assert "recording is now on" in run("set_persona", '{"setting": "recording", "value": "on"}', agent_id).lower()
    assert "3" in run("set_persona", '{"setting": "max call length", "value": "3"}', agent_id)
    assert "hi-IN" in run("set_persona", '{"setting": "language", "value": "Hindi"}', agent_id)

    profile = client.get(f"/api/agents/{agent_id}/profile").json()["profile"]
    assert profile["voice_speaker"] == "dev" and profile["customer_noun"] == "couple"
    assert profile["record_calls"] is True and profile["max_call_minutes"] == 3


def test_a_voice_or_language_we_do_not_have_is_refused_with_what_we_do(agent_id):
    refused = run("set_persona", '{"setting": "voice", "value": "Scarlett"}', agent_id)
    assert refused.startswith("Failed") and "Ashutosh" in refused
    assert run("set_persona", '{"setting": "language", "value": "Klingon"}', agent_id).startswith("Failed")
    assert run("set_persona", '{"setting": "max call length", "value": "90"}', agent_id).startswith("Failed")
    assert run("set_persona", '{"setting": "wibble", "value": "x"}', agent_id).startswith("Failed")
