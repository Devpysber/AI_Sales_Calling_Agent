from types import SimpleNamespace

from app.api import agents as agents_api
from app.core import store


def _team_request(team_id=7):
    return SimpleNamespace(state=SimpleNamespace(user="team", token_payload={"team_id": team_id, "unlocked": [1]}))


def test_admin_is_exempt(client):
    req = SimpleNamespace(state=SimpleNamespace(user="admin", token_payload={}))
    assert agents_api.playground_usage(req)["exempt"] is True


def test_team_member_counts_down_to_the_limit(client, monkeypatch):
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


def test_team_check_in_in_the_playground_runs_real_tools(client, base):
    from app.services import agents
    agent_id = int(base.rsplit("/", 1)[1])
    agents.update_automation(agent_id, {"auto_dial_enabled": True}, actor="test")
    res = client.post(f"{base}/playground", json={"message": "auto dial band karo", "purpose": "team", "history": []})
    assert res.status_code == 200, res.text
    body = res.json()
    assert "बंद" in body["reply"] or "off" in body["reply"].lower()
    assert body["tool_result"] and not agents.get_automation(agent_id)["auto_dial_enabled"]
    greeting = client.get(f"{base}/greeting", params={"language": "en-IN", "purpose": "team"}).json()["text"]
    assert "What would you like to check" in greeting or "check" in greeting
