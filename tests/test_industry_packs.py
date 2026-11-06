"""Industry packs: tool enable/disable preset + playbook snippet."""

import pytest

from app.services import agents as agents_service
from app.services.industry_packs import ALL_CUSTOMER_TOOL_NAMES, INDUSTRY_PACKS


@pytest.fixture
def base(client):
    """A fresh agent workspace per test: `base` session fixture is shared/mutated by other test files
    (e.g. instructions filled to its length cap), which would make the playbook-snippet checks here
    order-dependent."""
    import uuid
    res = client.post("/api/agents", json={"name": f"Industry Pack Co {uuid.uuid4().hex[:8]}"})
    agent = res.json()
    assert res.status_code == 200, agent
    return f"/api/agents/{agent['id']}"


def _enabled_tools(client, base):
    rows = client.get(f"{base}/tools").json()
    return {r["name"] for r in rows if r["type"] == "builtin" and r["enabled"]}


def test_list_packs(client, base):
    res = client.get(f"{base}/industry-packs")
    assert res.status_code == 200
    keys = {p["key"] for p in res.json()}
    assert keys == {"real_estate", "automotive", "clinics"}


def test_apply_pack_enables_exactly_its_tools(client, base):
    res = client.post(f"{base}/industry-packs/real_estate/apply")
    assert res.status_code == 200
    enabled = _enabled_tools(client, base)
    expected = set(INDUSTRY_PACKS["real_estate"]["enabled_tools"])
    assert enabled == expected
    assert enabled < set(ALL_CUSTOMER_TOOL_NAMES)


def test_apply_second_pack_is_not_additive(client, base):
    client.post(f"{base}/industry-packs/real_estate/apply")
    client.post(f"{base}/industry-packs/automotive/apply")
    enabled = _enabled_tools(client, base)
    assert enabled == set(INDUSTRY_PACKS["automotive"]["enabled_tools"])


def test_playbook_snippet_lands_in_instructions(client, base):
    agent_id = int(base.rsplit("/", 1)[-1])
    client.post(f"{base}/industry-packs/clinics/apply")
    profile = agents_service.get_profile(agent_id)
    assert INDUSTRY_PACKS["clinics"]["playbook_snippet"] in profile["instructions"]
    assert profile["industry_pack"] == "clinics"


def test_reapplying_different_pack_swaps_snippet_not_piles_up(client, base):
    agent_id = int(base.rsplit("/", 1)[-1])
    client.post(f"{base}/industry-packs/real_estate/apply")
    client.post(f"{base}/industry-packs/automotive/apply")
    profile = agents_service.get_profile(agent_id)
    assert INDUSTRY_PACKS["automotive"]["playbook_snippet"] in profile["instructions"]
    assert INDUSTRY_PACKS["real_estate"]["playbook_snippet"] not in profile["instructions"]


def test_invalid_pack_key_404s(client, base):
    res = client.post(f"{base}/industry-packs/not_a_pack/apply")
    assert res.status_code == 404


def test_agent_scoped(client, base):
    import uuid
    other = client.post("/api/agents", json={"name": f"Other Co {uuid.uuid4().hex[:8]}"}).json()
    other_base = f"/api/agents/{other['id']}"
    # Applying a pack on the "other" agent must not affect "base"'s tools.
    client.post(f"{base}/industry-packs/real_estate/apply")
    client.post(f"{other_base}/industry-packs/automotive/apply")
    assert _enabled_tools(client, base) == set(INDUSTRY_PACKS["real_estate"]["enabled_tools"])
    assert _enabled_tools(client, other_base) == set(INDUSTRY_PACKS["automotive"]["enabled_tools"])
