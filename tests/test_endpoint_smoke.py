"""No GET endpoint should answer with a server error on an ordinary, freshly set up workspace.

Individual endpoints have their own tests; this one exists so a route that nothing else covers
cannot rot unnoticed — it walks the whole app and fails on anything that 500s.
"""

import pytest

from app.main import app

SUBSTITUTIONS = {
    "{lead_id}": "lead", "{call_id}": "call", "{doc_id}": "doc",
    "{member_id}": "missing-member", "{audio_id}": "missing-audio", "{key}": "missing-key",
}


@pytest.fixture(scope="module")
def workspace(client):
    agent = client.post("/api/agents", json={"name": "Smoke", "profile": {"company_name": "Smoke Co"}}).json()
    lead = client.post(f"/api/agents/{agent['id']}/leads",
                       json={"name": "Probe", "phone": "+919000000999"}).json()
    return {"agent": agent["id"], "lead": lead["id"], "call": 999999, "doc": 999999}


def get_routes() -> list[str]:
    """Read the paths off the OpenAPI schema: included routers are nested objects, not flat routes."""
    schema = app.openapi()
    return sorted(path for path, methods in schema.get("paths", {}).items()
                  if "get" in methods and path.startswith("/api")
                  and path not in ("/api/openapi.json", "/api/docs"))


@pytest.mark.parametrize("path", get_routes())
def test_endpoint_does_not_fail(client, workspace, path):
    url = path.replace("{agent_id}", str(workspace["agent"]))
    for token, key in (("{lead_id}", "lead"), ("{call_id}", "call"), ("{doc_id}", "doc")):
        url = url.replace(token, str(workspace[key]))
    for token, placeholder in SUBSTITUTIONS.items():
        url = url.replace(token, placeholder)
    if "{" in url:
        pytest.skip(f"path parameter without a sensible stand-in: {path}")

    response = client.get(url)
    # 502 is this app's deliberate answer when a provider is unreachable, which the test doubles are.
    # 500 is an unhandled crash and is what this test exists to catch.
    assert response.status_code != 500, f"{path} crashed: {response.text[:300]}"
    assert response.status_code < 500 or response.status_code == 502,         f"{path} answered {response.status_code}: {response.text[:300]}"
