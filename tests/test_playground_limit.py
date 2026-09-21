from types import SimpleNamespace

from app.api import agents as agents_api
from app.core import store


def _team_request(team_id=7):
    return SimpleNamespace(state=SimpleNamespace(user="team", token_payload={"team_id": team_id, "unlocked": [1]}))


def test_admin_is_exempt():
    req = SimpleNamespace(state=SimpleNamespace(user="admin", token_payload={}))
    assert agents_api.playground_usage(req)["exempt"] is True


def test_team_member_counts_down_to_the_limit(monkeypatch):
    monkeypatch.setattr(agents_api.settings, "playground_monthly_limit", 2)
    req = _team_request(team_id=4242)
    usage = agents_api.playground_usage(req)
    assert (usage["used"], usage["remaining"], usage["exempt"]) == (0, 2, False)
    agents_api._count_playground_try(req, usage)
    agents_api._count_playground_try(req, agents_api.playground_usage(req))
    usage = agents_api.playground_usage(req)
    assert (usage["used"], usage["remaining"]) == (2, 0)
    assert usage["resets_at"] > "2000"


def test_usage_endpoint_for_admin(client, base):
    res = client.get(f"{base}/playground/usage").json()
    assert res["exempt"] is True and res["limit"] == 5
