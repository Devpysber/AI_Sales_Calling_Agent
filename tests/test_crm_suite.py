"""Accounts and roles, CRM records, conversion, automation rules, campaigns, search and the assistant."""

import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import accounts


@pytest.fixture(autouse=True)
def no_rate_limits(monkeypatch):
    """These tests sign in many times from one test client; the shared login bucket belongs to other tests."""
    from app.core import store
    monkeypatch.setattr(store, "rate_limited", lambda *a, **k: False)


def _token(email: str) -> str:
    return accounts.by_email(email).token


def test_signup_verify_approve_login_and_viewer_is_read_only(client, base):
    anon = TestClient(app)
    res = anon.post("/api/auth/signup", json={"email": "Viewer@Example.com", "password": "secret123", "name": "Vee", "organization": "Acme"})
    assert res.status_code == 200
    assert anon.post("/api/auth/signup", json={"email": "viewer@example.com", "password": "secret123"}).status_code == 400
    assert "Verify" in anon.post("/api/auth/login", json={"email": "viewer@example.com", "password": "secret123"}).json()["detail"]

    assert anon.post("/api/auth/verify", json={"token": _token("viewer@example.com")}).json()["status"] == "awaiting_approval"
    assert "approve" in anon.post("/api/auth/login", json={"email": "viewer@example.com", "password": "secret123"}).json()["detail"]

    user = next(u for u in client.get("/api/accounts").json() if u["email"] == "viewer@example.com")
    assert client.patch(f"/api/accounts/{user['id']}", json={"status": "active"}).json()["role"] == "viewer"

    assert anon.post("/api/auth/login", json={"email": "viewer@example.com", "password": "secret123"}).status_code == 200
    me = anon.get("/api/auth/me").json()
    assert me["role_key"] == "viewer" and me["user"] == "member"
    assert anon.get(f"{base}/crm/deals").status_code == 200
    assert anon.post(f"{base}/crm/deals", json={"title": "Nope"}).status_code == 403
    assert anon.get("/api/accounts").status_code == 403

    client.patch(f"/api/accounts/{user['id']}", json={"status": "disabled"})
    assert anon.get(f"{base}/crm/deals").status_code == 401


def test_invite_reset_and_mfa(client, base):
    link = client.post("/api/accounts/invite", json={"email": "rep@example.com", "role": "sales_exec", "name": "Rep"}).json()["link"]
    token = link.split("token=")[1]
    anon = TestClient(app)
    assert anon.get("/api/auth/invitation", params={"token": token}).json()["role"] == "sales_exec"
    assert anon.post("/api/auth/accept-invite", json={"token": token, "password": "repPass123"}).json()["user"] == "member"
    assert anon.post(f"{base}/crm/tasks", json={"title": "Call back"}).status_code == 200
    assert anon.post(f"{base}/crm/rules", json={"name": "x", "trigger": "lead.created"}).status_code == 403

    anon.post("/api/auth/forgot", json={"email": "rep@example.com"})
    assert anon.post("/api/auth/reset", json={"token": _token("rep@example.com"), "password": "newPass456"}).status_code == 200

    secret = anon.post("/api/accounts/me/mfa/setup").json()["secret"]
    code = accounts._totp(secret, time.time())
    assert anon.post("/api/accounts/me/mfa/confirm", json={"code": code}).status_code == 200
    fresh = TestClient(app)
    assert fresh.post("/api/auth/login", json={"email": "rep@example.com", "password": "newPass456"}).json()["detail"] == "MFA_REQUIRED"
    assert fresh.post("/api/auth/login", json={"email": "rep@example.com", "password": "newPass456", "otp": code}).status_code == 200


def test_records_conversion_search_and_deal_events(client, base):
    lead = client.post(f"{base}/leads", json={"name": "Ravi Kumar", "company": "Zenith Labs", "phone": "+919811112222", "email": "ravi@zenith.test"}).json()
    out = client.post(f"{base}/crm/leads/{lead['id']}/convert", json={"deal_value": 50000}).json()
    assert out["contact"]["name"] == "Ravi Kumar" and out["deal"]["value"] == 50000 and out["company_id"]
    assert client.post(f"{base}/crm/leads/{lead['id']}/convert").status_code == 400
    assert client.get(f"{base}/leads/{lead['id']}").json()["status"] == "Converted"

    deal = client.patch(f"{base}/crm/deals/{out['deal']['id']}", json={"stage": "Won"}).json()
    assert deal["probability"] == 100
    assert client.patch(f"{base}/crm/deals/{deal['id']}", json={"stage": "Bogus"}).status_code == 400
    assert client.get(f"{base}/crm/meta").json()["deals"]["won_value"] == 50000

    kinds = {h["kind"] for h in client.get(f"{base}/crm/search", params={"q": "zenith"}).json()["results"]}
    assert {"lead", "company"} <= kinds
    assert any(e["type"] == "deal.won" for e in client.get(f"{base}/activity", params={"type": "deal."}).json())


def test_recurring_task_spawns_next(client, base):
    task = client.post(f"{base}/crm/tasks", json={"title": "Weekly check-in", "due_at": "2026-01-05 10:00", "recurrence": "weekly"}).json()
    client.patch(f"{base}/crm/tasks/{task['id']}", json={"status": "done"})
    titles = [(t["title"], t["due_at"], t["status"]) for t in client.get(f"{base}/crm/tasks").json()]
    assert ("Weekly check-in", "2026-01-12 10:00", "open") in titles


def test_rule_runs_on_new_lead(client, base):
    rule = client.post(f"{base}/crm/rules", json={
        "name": "Follow up web leads", "trigger": "lead.created",
        "conditions": [{"field": "source", "op": "eq", "value": "website"}],
        "actions": [{"type": "create_task", "title": "Call {name}", "due_in_hours": 2}, {"type": "add_tag", "tag": "web"}],
    }).json()
    assert rule["conditions"][0]["value"] == "website"
    client.post(f"{base}/leads", json={"name": "Asha", "phone": "+919822223333", "source": "website"})
    client.post(f"{base}/leads", json={"name": "Manual Mo", "phone": "+919833334444", "source": "manual"})
    for _ in range(50):
        if any(t["title"] == "Call Asha" for t in client.get(f"{base}/crm/tasks").json()):
            break
        time.sleep(0.1)
    titles = [t["title"] for t in client.get(f"{base}/crm/tasks").json()]
    assert "Call Asha" in titles and "Call Manual Mo" not in titles
    assert client.get(f"{base}/crm/rules/{rule['id']}").json()["runs"] == 1


def test_call_campaign_queues_audience(client, base):
    client.post(f"{base}/leads", json={"name": "Camp One", "phone": "+919844445555", "tags": ["diwali"]})
    preview = client.post(f"{base}/crm/campaigns/preview", json={"segment": {"tags": ["diwali"]}, "channel": "call"}).json()
    assert preview["count"] == 1
    camp = client.post(f"{base}/crm/campaigns", json={"name": "Diwali", "channel": "call", "segment": {"tags": ["diwali"]}}).json()
    res = client.post(f"{base}/crm/campaigns/{camp['id']}/run").json()
    assert res["stats"]["sent"] == 1
    assert client.get(f"{base}/crm/campaigns/{camp['id']}").json()["status"] == "completed"


def test_campaign_audience_excludes_opted_out_leads_on_every_channel(client, base):
    from app.services import campaigns
    from app.services.whatsapp_service import OPT_OUT_TAG

    client.post(f"{base}/leads", json={"name": "Reachable", "phone": "+919844440001", "email": "r@x.test", "tags": ["camp"]})
    dnc = client.post(f"{base}/leads", json={"name": "DNC", "phone": "+919844440002", "email": "d@x.test", "tags": ["camp"]}).json()
    client.patch(f"{base}/leads/{dnc['id']}", json={"do_not_call": True})
    client.post(f"{base}/leads", json={"name": "WaOptOut", "phone": "+919844440003", "email": "w@x.test",
                                       "tags": ["camp", OPT_OUT_TAG]})
    agent_id = int(base.rsplit("/", 1)[1])

    for channel in ("call", "whatsapp", "email"):
        names = {l.name for l in campaigns.audience(agent_id, {"tags": ["camp"]}, channel)}
        assert "DNC" not in names, f"do_not_call lead must be excluded from {channel} campaigns"
        if channel == "whatsapp":
            assert "WaOptOut" not in names, "WhatsApp opt-out tag must be excluded from WhatsApp campaigns"
        assert "Reachable" in names


def test_assistant_proposes_and_executes(client, base, monkeypatch):
    from app.services import llm
    lead = client.post(f"{base}/leads", json={"name": "Priya", "phone": "+919855556666"}).json()
    reply = '{"answer": "Priya has no follow-up.", "actions": [{"type": "create_task", "label": "Follow up Priya", "lead_id": %d, "params": {"title": "Follow up Priya", "due_in_hours": 4}}]}' % lead["id"]
    monkeypatch.setattr(llm, "complete", lambda *a, **k: llm.LLMResult(reply, "test", "test-model", 1))
    out = client.post(f"{base}/crm/assistant/chat", json={"message": "Who needs a follow-up?"}).json()
    assert out["answer"].startswith("Priya") and out["actions"][0]["type"] == "create_task"
    assert client.post(f"{base}/crm/assistant/execute", json=out["actions"][0]).status_code == 200
    assert any(t["title"] == "Follow up Priya" and t["lead_id"] == lead["id"] for t in client.get(f"{base}/crm/tasks").json())


def test_public_demo_request(client):
    res = TestClient(app).post("/api/public/demo-request", json={"name": "Neha", "email": "neha@x.test", "message": "Demo please"})
    assert res.status_code == 200
