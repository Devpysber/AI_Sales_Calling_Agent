"""The WhatsApp/Composio credential fields in Settings -> Secrets used to be accepted by the API and
stored, but never routed to the encrypted row app/core/config.py's Settings.__getattribute__ actually
reads -- saving a value there did nothing for the real settings.whatsapp_token etc. Locks in the fix:
these keys must round-trip through the real config override, not just come back unmasked next GET.

These credentials live in a session-wide encrypted row (not per-test-agent data), so every test here
clears what it sets: other test files (e.g. test_whatsapp*.py, alphabetically after this one) must see
settings.whatsapp_token/composio_api_key back at their env-default ("") in a fresh test run.
"""
import pytest
from app.core.config import settings


@pytest.fixture(autouse=True)
def _clear_whatsapp_secrets(client):
    """Runs after every test in this file: guarantees no credential set here leaks into later files."""
    yield
    client.post("/api/system/secrets", json={
        "whatsapp_token": "", "whatsapp_phone_number_id": "", "whatsapp_app_id": "", "composio_api_key": "",
    })


def test_whatsapp_and_composio_secrets_actually_override_settings(client):
    res = client.post("/api/system/secrets", json={
        "whatsapp_token": "EAA_test_token",
        "whatsapp_phone_number_id": "1000000000",
        "whatsapp_app_id": "123456789012345",
        "composio_api_key": "comp_test_key",
    })
    assert res.status_code == 200

    # The whole point of the fix: Settings.__getattribute__'s database override must see these values.
    assert settings.whatsapp_token == "EAA_test_token"
    assert settings.whatsapp_phone_number_id == "1000000000"
    assert settings.whatsapp_app_id == "123456789012345"
    assert settings.composio_api_key == "comp_test_key"

    # And the GET response masks them like every other stored credential, not plaintext.
    got = client.get("/api/system/secrets").json()
    assert got["whatsapp_token"] == "********"
    assert got["whatsapp_app_id"] == "********"
    assert got["composio_api_key"] == "********"


def test_system_status_reports_whatsapp(client, monkeypatch):
    """The main Integrations & System health page had no WhatsApp check at all -- added one mirroring
    the existing Plivo/OpenRouter/Sarvam pattern (a live call against the configured token). whatsapp_token
    is a DB-overridden credential (see test above), so it's set through the real POST /api/system/secrets
    endpoint here rather than monkeypatching settings directly, which Settings.__getattribute__'s
    database-override check would just ignore."""
    import app.api.system as system_mod

    class FakeResponse:
        status_code = 200
        def json(self):
            return {"id": "123", "name": "Test WABA Business"}

    monkeypatch.setattr(system_mod.httpx, "get", lambda *a, **k: FakeResponse())
    res = client.post("/api/system/secrets", json={"whatsapp_token": "fake-token", "whatsapp_phone_number_id": "100200300"})
    assert res.status_code == 200

    res = client.get("/api/system/status")
    assert res.status_code == 200
    whatsapp = res.json()["whatsapp"]
    assert whatsapp["ok"] is True
    assert whatsapp["account"] == "Test WABA Business"
    assert whatsapp["phone_number_id"] == "100200300"


def test_system_status_whatsapp_not_configured(client):
    res = client.get("/api/system/status")
    assert res.json()["whatsapp"]["ok"] is False
