"""A colleague ringing the agent's own number gets a walkthrough, not a sales call."""

import pytest

from app.services import agent, team_service


@pytest.fixture
def team(monkeypatch):
    monkeypatch.setattr(team_service, "members",
                        lambda: [{"id": "m1", "name": "Ashish Sharma", "phone": "+919584516352", "email": "a@b.c"}])


def test_a_team_line_is_recognised(team):
    assert team_service.is_team_number("+919584516352")
    assert team_service.is_team_number("919584516352")      # however the carrier presents it
    assert not team_service.is_team_number("+917879417266")  # an ordinary caller
    assert not team_service.is_team_number("")


def test_the_brief_tells_the_agent_to_explain_rather_than_sell():
    goal = agent.call_goal({"team_name": "Ashish Sharma"}, "team")
    assert "Ashish Sharma" in goal
    for forbidden in ("Do NOT sell", "do NOT qualify", "do NOT ask for their name"):
        assert forbidden in goal
    # It must offer the things a colleague actually rings to check.
    assert "knowledge base does not cover" in goal
    assert "role-play" in goal


def test_a_customer_brief_is_unchanged():
    goal = agent.call_goal({"collect": ["name", "city"]}, "inbound_new")
    assert "A new caller not yet in our CRM" in goal
    assert "colleague" not in goal.lower()


def test_the_greeting_skips_the_company_pitch(monkeypatch):
    monkeypatch.setattr(agent.agents, "get_profile",
                        lambda _id: {"agent_name": "Ashish", "company_name": "Hairscope",
                                     "greeting_en": "Hi {name}, this is {agent} calling from {company}.",
                                     "greeting_hi": "नमस्ते"})
    spoken = agent.greeting(1, {"call_purpose": "team", "team_name": "Ashish Sharma"}, "en-IN")
    assert "Ashish Sharma" in spoken and "would you like to check" in spoken
    assert "calling from" not in spoken, "a colleague must not be pitched the company"


def test_a_caller_is_never_transferred_to_their_own_line():
    """A colleague ringing in from the transfer number cannot be put through to themselves."""
    from app.services.voice_stream import CallStream

    stream = CallStream.__new__(CallStream)
    stream.persona = {"transfer_number": "+919584516352", "transfer_on_request": True}

    stream.session = {"lead": {"phone": "+917879417266"}}       # an ordinary caller
    assert stream.has_human_line()

    stream.session = {"lead": {"phone": "+919584516352"}}       # the transfer line itself
    assert not stream.has_human_line(), "ringing the line someone is speaking on reaches nobody"

    stream.persona = {"transfer_number": "+919584516352,+919000000111"}
    assert stream.has_human_line(), "a second colleague is still reachable"


def test_a_colleagues_call_never_becomes_a_lead(client, monkeypatch):
    """Trying the agent out must not put the tester in the CRM or the pipeline."""
    from app.services import call_service, team_service
    from app.services.crm_service import CRMService

    agent = client.post("/api/agents", json={"name": "Team check", "phone_number": "+91 80 5555 0002"}).json()
    monkeypatch.setattr(team_service, "members",
                        lambda: [{"id": "m1", "name": "Ashish Sharma", "phone": "+919584516352", "email": "a@b.c"}])

    before = CRMService(agent["id"]).list_leads()["total"]
    session = call_service.CallService().create_inbound("919584516352", "918055550002", "uuid-team")
    assert session is not None

    assert CRMService(agent["id"]).list_leads()["total"] == before, "a colleague must not be saved as a lead"
    assert session["lead_id"] is None
    assert session["lead"]["call_purpose"] == "team"

    # Looked up by id: which agent owns the number depends on what else exists, and this test is
    # about the call's own shape, not about routing.
    call = call_service.CallService().get(session["call_id"])
    assert call["trigger"] == "internal", "the call says what it was"


def test_a_customer_call_still_creates_a_lead(client, monkeypatch):
    from app.services import call_service, team_service
    from app.services.crm_service import CRMService

    agent = client.post("/api/agents", json={"name": "Customer check", "phone_number": "+91 80 5555 0001"}).json()
    monkeypatch.setattr(team_service, "members", lambda: [])

    session = call_service.CallService().create_inbound("917879417266", "918055550001", "uuid-customer")
    assert session["lead_id"], "an ordinary caller is saved so the agent can call them back"
    lead = CRMService(None).get(session["lead_id"])
    assert lead["phone"] == "+917879417266"
    assert call_service.CallService().get(session["call_id"])["trigger"] == "inbound"


def test_the_agent_can_report_on_itself_to_a_colleague(client, monkeypatch):
    """A colleague asking "how is it going?" gets this workspace's real numbers, not a guess."""
    from app.services import agent as agent_service
    from app.services import team_service

    made = client.post("/api/agents", json={"name": "Brief desk"}).json()
    client.post(f"/api/agents/{made['id']}/leads", json={"name": "Asha", "phone": "+919000000401"})

    brief = agent_service.team_brief(made["id"])
    assert "Today:" in brief and "Leads: 1 in total" in brief
    # The gap a colleague most needs to hear about is stated, not hidden.
    assert "Nothing is loaded" in brief

    # And it reaches the prompt only on a colleague's call.
    monkeypatch.setattr(team_service, "members",
                        lambda: [{"id": "m1", "name": "Ashish", "phone": "+919584516352", "email": "a@b.c"}])
    persona = dict(client.get(f"/api/agents/{made['id']}/profile").json()["profile"])

    team_prompt = agent_service._system_prompt(persona, {"call_purpose": "team"}, [], made["id"])
    assert "How this agent is doing right now" in team_prompt

    customer_prompt = agent_service._system_prompt(persona, {"call_purpose": "inbound"}, [], made["id"])
    assert "How this agent is doing right now" not in customer_prompt, "a customer must never hear our numbers"


def test_the_brief_is_cached_within_a_call(client, monkeypatch):
    """team_brief must not re-run its ~9 stats queries on every turn / trim pass."""
    from app.services import agent as agent_service
    from app.services.call_service import CallService

    made = client.post("/api/agents", json={"name": "Cache desk"}).json()

    calls = {"n": 0}
    real_stats = CallService.stats

    def counting_stats(self, *a, **kw):
        calls["n"] += 1
        return real_stats(self, *a, **kw)

    monkeypatch.setattr(CallService, "stats", counting_stats)

    agent_service._memory_cache.clear()
    first = agent_service.team_brief(made["id"])
    second = agent_service.team_brief(made["id"])
    assert first == second
    assert calls["n"] == 1, "a repeat call within the TTL must reuse the cached brief"
