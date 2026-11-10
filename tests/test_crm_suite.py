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


def test_campaign_run_rejects_already_running(client, base):
    import pytest

    from app.core.database import get_db
    from app.models.crm import Campaign
    from app.services import campaigns as campaigns_mod

    agent_id = int(base.rsplit("/", 1)[1])
    client.post(f"{base}/leads", json={"name": "RaceLead", "phone": "+919844448000", "tags": ["race-test"]})
    camp = client.post(f"{base}/crm/campaigns", json={"name": "Race", "channel": "call",
                                                        "segment": {"tags": ["race-test"]}}).json()
    with get_db() as db:
        db.get(Campaign, camp["id"]).status = "running"
    with pytest.raises(ValueError, match="already running"):
        campaigns_mod.run(agent_id, camp["id"])


def test_claim_running_is_atomic_against_double_claim(client, base):
    from app.core.database import get_db
    from app.models.crm import Campaign
    from app.services import campaigns as campaigns_mod

    agent_id = int(base.rsplit("/", 1)[1])
    camp = client.post(f"{base}/crm/campaigns", json={"name": "Claim", "channel": "call", "segment": {}}).json()
    with get_db() as db:
        db.get(Campaign, camp["id"]).status = "draft"
    assert campaigns_mod._claim_running(camp["id"]) is True
    assert campaigns_mod._claim_running(camp["id"]) is False


def test_call_campaign_preview_excludes_leads_with_no_phone(client, base):
    client.post(f"{base}/leads", json={"name": "HasPhone", "phone": "+919844447000", "tags": ["call-preview"]})
    client.post(f"{base}/leads", json={"name": "NoPhone", "email": "nophone@x.test", "tags": ["call-preview"]})
    preview = client.post(f"{base}/crm/campaigns/preview", json={"segment": {"tags": ["call-preview"]}, "channel": "call"}).json()
    assert preview["count"] == 1  # matches run()'s own phone filter, so the preview promises only what launch can deliver


def test_email_campaign_requires_subject_before_launch(client, base):
    client.post(f"{base}/leads", json={"name": "Camp Email", "email": "campemail@x.test", "tags": ["nosubj"]})
    camp = client.post(f"{base}/crm/campaigns", json={"name": "No Subject", "channel": "email",
                                                       "segment": {"tags": ["nosubj"]}, "message": "Hi {name}"}).json()
    res = client.post(f"{base}/crm/campaigns/{camp['id']}/run")
    assert res.status_code == 400 and "subject" in res.json()["detail"].lower()
    assert client.get(f"{base}/crm/campaigns/{camp['id']}").json()["status"] == "draft"


def test_campaign_cancel_stops_before_next_lead(client, base):
    from app.core.database import get_db
    from app.models.crm import Campaign
    from app.models.lead import Lead
    from app.services import campaigns as campaigns_mod

    agent_id = int(base.rsplit("/", 1)[1])
    client.post(f"{base}/leads", json={"name": "CancelMe", "phone": "+919844446000", "tags": ["cancel-test"]})
    camp = client.post(f"{base}/crm/campaigns", json={"name": "ToCancel", "channel": "whatsapp",
                                                        "segment": {"tags": ["cancel-test"]}, "message": "hi {name}"}).json()

    res = client.post(f"{base}/crm/campaigns/{camp['id']}/cancel")
    assert res.status_code == 400  # a draft has nothing to cancel

    with get_db() as db:
        db.get(Campaign, camp["id"]).status = "running"
    res = client.post(f"{base}/crm/campaigns/{camp['id']}/cancel")
    assert res.status_code == 200
    with get_db() as db:
        assert db.get(Campaign, camp["id"]).status == "cancelling"

    with get_db() as db:
        lead_row = db.query(Lead).filter(Lead.agent_id == agent_id, Lead.phone == "+919844446000").first()
    campaigns_mod._save(camp["id"], stats_json='{"targeted": 1, "sent": 0, "failed": 0, "skipped": 0}')
    campaigns_mod._send({"id": camp["id"], "agent_id": agent_id, "name": "ToCancel", "channel": "whatsapp",
                         "message": "hi {name}"}, [lead_row])
    final = client.get(f"{base}/crm/campaigns/{camp['id']}").json()
    assert final["status"] == "cancelled"
    assert final["stats"]["sent"] == 0


def test_board_counts_match_per_status_with_filters(client):
    agent_id = client.post("/api/agents", json={"name": "BoardCounts", "profile": {"company_name": "BoardCounts"}}).json()["id"]
    base = f"/api/agents/{agent_id}"
    client.post(f"{base}/leads", json={"name": "A", "phone": "+919844441001", "tags": ["vip"]})
    client.post(f"{base}/leads", json={"name": "B", "phone": "+919844441002", "tags": ["vip"]})
    c = client.post(f"{base}/leads", json={"name": "C", "phone": "+919844441003", "tags": ["vip"]}).json()
    client.post(f"{base}/leads", json={"name": "D", "phone": "+919844441004"})
    client.patch(f"{base}/leads/{c['id']}", json={"status": "Contacted"})

    import app.services.crm_service as crm_mod
    statuses = ["New", "Contacted", "Interested", "Meeting", "Won", "Lost", "Do Not Call"]
    board_all = crm_mod.CRMService(agent_id).board(statuses)
    assert sum(c["total"] for c in board_all.values()) == 4
    assert board_all["New"]["total"] == 3
    assert board_all["Contacted"]["total"] == 1

    board_vip = crm_mod.CRMService(agent_id).board(statuses, tag="vip")
    assert sum(c["total"] for c in board_vip.values()) == 3
    assert board_vip["New"]["total"] == 2
    assert board_vip["Contacted"]["total"] == 1


def test_rule_actions_refuse_opted_out_leads(client, base):
    from app.services import rules
    from app.services.whatsapp_service import OPT_OUT_TAG

    agent_id = int(base.rsplit("/", 1)[1])
    dnc = client.post(f"{base}/leads", json={"name": "RuleDNC", "phone": "+919844440010", "email": "rd@x.test"}).json()
    client.patch(f"{base}/leads/{dnc['id']}", json={"do_not_call": True})
    wa_opt_out = client.post(f"{base}/leads", json={"name": "RuleWaOut", "phone": "+919844440011",
                                                     "tags": [OPT_OUT_TAG]}).json()

    rule = {"name": "r", "actions": []}
    res = rules.execute({**rule, "actions": [{"type": "send_email"}]}, agent_id, dnc["id"], {})
    assert res["actions"][0]["ok"] is False and "do-not-call" in res["actions"][0]["error"]

    res = rules.execute({**rule, "actions": [{"type": "send_whatsapp"}]}, agent_id, dnc["id"], {})
    assert res["actions"][0]["ok"] is False and "do-not-call" in res["actions"][0]["error"]

    res = rules.execute({**rule, "actions": [{"type": "send_whatsapp"}]}, agent_id, wa_opt_out["id"], {})
    assert res["actions"][0]["ok"] is False and "opted out" in res["actions"][0]["error"]


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


def test_export_csv_sanitizes_formula_injection(client, base):
    lead = client.post(f"{base}/leads", json={"name": "=cmd|'/ C calc'!A1", "phone": "+919822223333"}).json()
    csv = client.get(f"{base}/leads/export").text
    row = next(line for line in csv.splitlines() if "cmd|" in line)
    assert "'=cmd|" in row and "\n=cmd|" not in csv and not csv.startswith("=cmd|")
    assert lead["id"]


def test_public_demo_request(client):
    res = TestClient(app).post("/api/public/demo-request", json={"name": "Neha", "email": "neha@x.test", "message": "Demo please"})
    assert res.status_code == 200
