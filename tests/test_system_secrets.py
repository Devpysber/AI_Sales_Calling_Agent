"""The WhatsApp/Composio credential fields in Settings -> Secrets used to be accepted by the API and
stored, but never routed to the encrypted row app/core/config.py's Settings.__getattribute__ actually
reads -- saving a value there did nothing for the real settings.whatsapp_token etc. Locks in the fix:
these keys must round-trip through the real config override, not just come back unmasked next GET."""
from app.core.config import settings


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
