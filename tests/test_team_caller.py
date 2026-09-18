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
    assert "MUST collect these details" in goal
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
