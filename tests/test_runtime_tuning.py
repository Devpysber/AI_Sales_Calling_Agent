"""Runtime tuning: an admin changes live settings from the panel; validation keeps a bad value off a call."""
from app.core.config import settings


def test_panel_value_overrides_env_and_clears(client):
    default = settings.tts_chars_per_call
    res = client.post("/api/system/runtime", json={"tts_chars_per_call": "700", "llm_providers": "Sarvam, OpenRouter"})
    assert res.status_code == 200, res.text
    assert settings.tts_chars_per_call == 700 and settings.llm_providers == "sarvam,openrouter"
    eff = client.get("/api/system/runtime").json()
    assert eff["tts_chars_per_call"]["source"] == "panel" and eff["tts_chars_per_call"]["value"] == 700
    assert "plivo_phone_number" in eff["_server"]
    client.post("/api/system/runtime", json={"tts_chars_per_call": ""})
    assert settings.tts_chars_per_call == default


def test_bad_values_are_refused_with_a_reason(client):
    for body, word in (({"tts_chars_per_call": "50"}, "between"), ({"llm_providers": "gpt"}, "sarvam"),
                       ({"openrouter_models": "nomodel"}, "provider/model"), ({"secret_key": "x"}, "cannot")):
        res = client.post("/api/system/runtime", json=body)
        assert res.status_code == 400 and word in res.json()["detail"], (body, res.text)


def test_rotated_heal_token_is_the_one_export_checks(client):
    token = client.get("/api/system/issues/token", params={"rotate": "true"}).json()["token"]
    assert settings.heal_export_token == token
    assert client.get("/api/system/issues/export", headers={"X-Heal-Token": token}).status_code == 200
    assert client.get("/api/system/runtime").json()["heal_export_token"]["value"] == "********"
