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
