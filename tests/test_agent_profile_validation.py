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
