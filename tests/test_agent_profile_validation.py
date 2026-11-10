"""Profile writes are bounded: an unknown voice/language breaks TTS/playground fallback,
an out-of-range call length or oversized text is stored as-is otherwise."""

import pytest

from app.services import agents


def test_unknown_voice_speaker_is_rejected(base):
    agent_id = int(base.rsplit("/", 1)[1])
    with pytest.raises(ValueError):
        agents.update_profile(agent_id, {"voice_speaker": "not-a-real-voice"}, actor="test")


def test_unknown_language_is_rejected(base):
    agent_id = int(base.rsplit("/", 1)[1])
    with pytest.raises(ValueError):
        agents.update_profile(agent_id, {"default_language": "xx-XX"}, actor="test")


@pytest.mark.parametrize("minutes", [0, -5, 10000])
def test_max_call_minutes_out_of_range_is_rejected(base, minutes):
    agent_id = int(base.rsplit("/", 1)[1])
    with pytest.raises(ValueError):
        agents.update_profile(agent_id, {"max_call_minutes": minutes}, actor="test")


def test_text_fields_are_capped_to_ui_limits(base):
    agent_id = int(base.rsplit("/", 1)[1])
    result = agents.update_profile(agent_id, {"instructions": "x" * 5000, "greeting_en": "y" * 500}, actor="test")
    assert len(result["instructions"]) == 3000
    assert len(result["greeting_en"]) == 200


def test_whatsapp_business_token_is_never_returned_by_get_profile(base):
    """Embedded Signup's business token must round-trip through encrypt/decrypt internally, but
    get_profile() (what every API route hands back to the frontend) must never see the raw or
    encrypted value -- only a boolean flag that one is set."""
    agent_id = int(base.rsplit("/", 1)[1])
    agents.update_profile(agent_id, {"whatsapp_business_token": "EAA_test_123"}, actor="test")

    profile = agents.get_profile(agent_id)
    assert profile.get("whatsapp_business_token_set") is True
    assert "whatsapp_business_token" not in profile
    assert "EAA_test_123" not in str(profile)

    assert agents.get_agent_whatsapp_token(agent_id) == "EAA_test_123"


def test_whatsapp_business_token_empty_string_clears_it(base):
    agent_id = int(base.rsplit("/", 1)[1])
    agents.update_profile(agent_id, {"whatsapp_business_token": "EAA_test_123"}, actor="test")
    agents.update_profile(agent_id, {"whatsapp_business_token": ""}, actor="test")

    assert agents.get_agent_whatsapp_token(agent_id) is None
    assert agents.get_profile(agent_id)["whatsapp_business_token_set"] is False


def test_instagram_business_token_is_never_returned_by_get_profile(base):
    """Manually-pasted Instagram access token must round-trip through encrypt/decrypt internally, but
    get_profile() (what every API route hands back to the frontend) must never see the raw or
    encrypted value -- only a boolean flag that one is set."""
    agent_id = int(base.rsplit("/", 1)[1])
    agents.update_profile(agent_id, {"instagram_business_token": "IGAA_test_123"}, actor="test")

    profile = agents.get_profile(agent_id)
    assert profile.get("instagram_business_token_set") is True
    assert "instagram_business_token" not in profile
    assert "IGAA_test_123" not in str(profile)

    assert agents.get_agent_instagram_token(agent_id) == "IGAA_test_123"


def test_instagram_business_token_empty_string_clears_it(base):
    agent_id = int(base.rsplit("/", 1)[1])
    agents.update_profile(agent_id, {"instagram_business_token": "IGAA_test_123"}, actor="test")
    agents.update_profile(agent_id, {"instagram_business_token": ""}, actor="test")

    assert agents.get_agent_instagram_token(agent_id) is None
    assert agents.get_profile(agent_id)["instagram_business_token_set"] is False


def test_team_member_can_set_a_passcode_on_a_workspace_they_created(client):
    """The admin bypasses every passcode, so the creator setting one locks nobody out; someone else's stays admin-only."""
    import pytest
    from fastapi import HTTPException
    from app.api import agents as agents_api
    from app.core.database import get_db
    from app.models.agent import Agent
    from app.services import agents

    made = client.post("/api/agents", json={"name": "Owned by member", "profile": {"agent_password": "secret1"}}).json()
    other = client.post("/api/agents", json={"name": "Someone elses"}).json()
    try:
        with get_db() as db:
            db.get(Agent, made["id"]).created_by = "m1"
            db.get(Agent, other["id"]).created_by = "m2"

        class Req:
            def __init__(self):
                self.state = type("S", (), {"user": "team", "token_payload": {"u": "team", "team_id": "m1"}})()

        assert agents_api.update_profile({"agent_password": "mine"}, Req(), made["id"])["agent_password"] == "mine"
        with pytest.raises(HTTPException) as e:
            agents_api.update_profile({"agent_password": "theirs"}, Req(), other["id"])
        assert e.value.status_code == 403 and "created this workspace" in e.value.detail
    finally:
        agents.delete(made["id"], actor="admin")
        agents.delete(other["id"], actor="admin")
