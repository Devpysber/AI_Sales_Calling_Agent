"""A team member cannot copy a locked agent's persona nor set a passcode on create."""

from fastapi.testclient import TestClient

from app.core.auth import COOKIE, make_token
from app.main import app
from app.services import team_service


def _team_client(team_id="m1", unlocked=None):
    c = TestClient(app)
    payload = {"u": "team", "team_id": team_id, "exp": 9999999999, "unlocked": unlocked or []}
    c.cookies.set(COOKIE, make_token(payload))
    return c


def test_team_member_cannot_copy_a_locked_agent(client, monkeypatch):
    locked = client.post("/api/agents", json={"name": "Locked donor", "profile": {"company_name": "Acme"}}).json()
    monkeypatch.setattr(team_service, "by_id", lambda mid: {"id": mid, "max_agents": 5})
    monkeypatch.setattr(team_service, "agent_limit", lambda member: 5)

    team = _team_client(unlocked=[])
    res = team.post("/api/agents", json={"name": "Copy attempt", "copy_from": locked["id"]})
    assert res.status_code == 403


def test_team_member_can_copy_an_agent_they_have_unlocked(client, monkeypatch):
    donor = client.post("/api/agents", json={"name": "Unlocked donor", "profile": {"company_name": "Acme"}}).json()
    monkeypatch.setattr(team_service, "by_id", lambda mid: {"id": mid, "max_agents": 5})
    monkeypatch.setattr(team_service, "agent_limit", lambda member: 5)

    team = _team_client(unlocked=[donor["id"]])
    res = team.post("/api/agents", json={"name": "Copy ok", "copy_from": donor["id"]})
    assert res.status_code == 200


def test_team_member_cannot_set_an_agent_password_on_create(client, monkeypatch):
    monkeypatch.setattr(team_service, "by_id", lambda mid: {"id": mid, "max_agents": 5})
    monkeypatch.setattr(team_service, "agent_limit", lambda member: 5)

    team = _team_client(unlocked=[])
    res = team.post("/api/agents", json={"name": "Passcode attempt", "profile": {"agent_password": "1234"}})
    assert res.status_code == 403
