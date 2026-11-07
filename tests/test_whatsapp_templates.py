"""Meta's 24-hour WhatsApp free-form window: detection, enforcement, templates, admin API, campaigns."""

from datetime import datetime, timedelta

import pytest

from app.core.database import get_db
from app.models.channel import Conversation
from app.models.crm import MessageTemplate
from app.services import whatsapp_cloud_service as wa


def _conv(agent_id, lead_id, last_customer_at):
    with get_db() as db:
        conv = Conversation(agent_id=agent_id, lead_id=lead_id, channel="whatsapp", status="open",
                            last_customer_at=last_customer_at)
        db.add(conv)
        db.flush()
        return conv.id


import itertools

_agent_counter = itertools.count()


def _fresh_agent(client) -> int:
    """A dedicated agent per test: these tests create raw whatsapp Conversation rows directly, which
    would otherwise leak into the shared session-scoped `base` agent and pollute other files' chat-count
    assertions (e.g. test_whatsapp_channel.py) whenever this module happens to run first. Agent names
    must be unique, so each call gets its own suffix."""
    name = f"TemplateWindow {next(_agent_counter)}"
    return client.post("/api/agents", json={"name": name, "profile": {"company_name": name}}).json()["id"]


def test_within_window(client):
    agent_id = _fresh_agent(client)
    conv_id = _conv(agent_id, None, datetime.utcnow() - timedelta(hours=1))
    with get_db() as db:
        conv = db.get(Conversation, conv_id)
        assert wa.is_within_free_form_window(conv)


def test_outside_window(client):
    agent_id = _fresh_agent(client)
    conv_id = _conv(agent_id, None, datetime.utcnow() - timedelta(hours=25))
    with get_db() as db:
        conv = db.get(Conversation, conv_id)
        assert not wa.is_within_free_form_window(conv)


def test_never_contacted_is_outside_window(client):
    agent_id = _fresh_agent(client)
    conv_id = _conv(agent_id, None, None)
    with get_db() as db:
        conv = db.get(Conversation, conv_id)
        assert not wa.is_within_free_form_window(conv)


def test_plain_text_rejected_outside_window(client, monkeypatch):
    agent_id = _fresh_agent(client)
    conv_id = _conv(agent_id, None, datetime.utcnow() - timedelta(hours=48))

    async def send(*a, **k):
        raise AssertionError("should not attempt to send outside the window")
    monkeypatch.setattr(wa, "send_whatsapp_message", send)
    with pytest.raises(wa.OutsideWindowError):
        wa._send("919811100099", "hello", agent_id, None, conv_id=conv_id)


def test_plain_text_allowed_within_window(client, monkeypatch):
    agent_id = _fresh_agent(client)
    conv_id = _conv(agent_id, None, datetime.utcnow() - timedelta(hours=1))
    sent = []

    async def send(to, text, agent_id=None, lead_id=None, call_id=None, phone_number_id=None):
        sent.append((to, text))
        return {"ok": True}
    monkeypatch.setattr(wa, "send_whatsapp_message", send)
    out = wa._send("919811100099", "hello", agent_id, None, conv_id=conv_id)
    assert out["ok"] and sent


def test_send_whatsapp_template_payload_shape(monkeypatch):
    import asyncio
    calls = []

    class Res:
        status_code = 200
        text = ""
        def json(self):
            return {"messages": [{"id": "wamid.T"}]}

    class FakeClient:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, headers=None, json=None):
            calls.append((url, headers, json))
            return Res()

    import httpx as _httpx
    monkeypatch.setattr(_httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(wa.settings, "whatsapp_token", "tok")
    monkeypatch.setattr(wa.settings, "whatsapp_phone_number_id", "555")

    out = asyncio.run(wa.send_whatsapp_template("919811100099", "order_update", "en_US", ["Ravi", "#123"], agent_id=1))
    assert out["ok"]
    _, _, payload = calls[0]
    assert payload["type"] == "template"
    assert payload["template"]["name"] == "order_update"
    assert payload["template"]["language"] == {"code": "en_US"}
    assert payload["template"]["components"][0]["parameters"][0]["text"] == "Ravi"


def _order_update_components():
    return [
        {"type": "HEADER", "format": "TEXT", "text": "Your order {{1}}"},
        {"type": "BODY", "text": "Hi {{1}}, your order #{{2}} has shipped and will arrive by {{3}}.",
         "example": {"body_text": [["Rahul", "12345", "Friday"]]}},
        {"type": "FOOTER", "text": "Reply STOP to unsubscribe"},
        {"type": "BUTTONS", "buttons": [
            {"type": "QUICK_REPLY", "text": "Track order"},
            {"type": "URL", "text": "View details", "url": "https://example.com/orders/{{1}}"},
        ]},
    ]


def test_template_admin_crud_is_agent_scoped(client, base):
    agent_id = int(base.rsplit("/", 1)[1])
    other = client.post("/api/agents", json={"name": "Other", "profile": {"company_name": "Other Co"}}).json()["id"]

    created = client.post(f"{base}/whatsapp/templates", json={
        "name": "order_update", "language": "en_US", "category": "UTILITY",
        "components": _order_update_components(),
    }).json()
    assert created["whatsapp_template_name"] == "order_update"
    assert created["status"] == "draft"
    assert created["header_format"] == "TEXT"
    assert created["components"][1]["type"] == "BODY"

    listed = client.get(f"{base}/whatsapp/templates").json()
    assert any(t["id"] == created["id"] for t in listed)

    other_listed = client.get(f"/api/agents/{other}/whatsapp/templates").json()
    assert not any(t["id"] == created["id"] for t in other_listed)


def test_create_template_requires_body(client, base):
    res = client.post(f"{base}/whatsapp/templates", json={
        "name": "no_body", "category": "UTILITY",
        "components": [{"type": "HEADER", "format": "TEXT", "text": "Hi"}],
    })
    assert res.status_code == 400


def test_submit_template_without_business_account_id_returns_400(client, base):
    created = client.post(f"{base}/whatsapp/templates", json={
        "name": "promo_blast", "category": "MARKETING",
        "components": [{"type": "BODY", "text": "Hi {{1}}, check our sale!"}],
    }).json()
    res = client.post(f"{base}/whatsapp/templates/{created['id']}/submit")
    assert res.status_code == 400
    assert "Business Account ID" in res.json()["detail"]


def test_template_send_endpoint_builds_meta_components(client, base, monkeypatch):
    agent_id = int(base.rsplit("/", 1)[1])
    created = client.post(f"{base}/whatsapp/templates", json={
        "name": "order_update", "language": "en_US", "category": "UTILITY",
        "components": _order_update_components(),
    }).json()

    captured = {}

    async def fake_send(to, name, lang, variables=None, agent_id=None, lead_id=None, phone_number_id=None, components=None):
        captured["components"] = components
        return {"ok": True, "provider": "meta", "data": {}}
    monkeypatch.setattr(wa, "send_whatsapp_template", fake_send)

    res = client.post(f"{base}/whatsapp/templates/{created['id']}/send", json={
        "phone": "919811100099",
        "variables": {"header": ["ORD-123"], "body": ["Rahul", "12345", "Friday"], "button_1": ["12345"]},
    })
    assert res.status_code == 200 and res.json()["ok"]
    sent = captured["components"]
    assert sent[0] == {"type": "header", "parameters": [{"type": "text", "text": "ORD-123"}]}
    assert sent[1] == {"type": "body", "parameters": [{"type": "text", "text": "Rahul"},
                                                       {"type": "text", "text": "12345"},
                                                       {"type": "text", "text": "Friday"}]}
    assert sent[2] == {"type": "button", "sub_type": "url", "index": "1",
                       "parameters": [{"type": "text", "text": "12345"}]}


def test_campaign_whatsapp_sends_template_outside_window(client):
    from app.services import campaigns
    from app.models.lead import Lead

    agent_id = client.post("/api/agents", json={"name": "CampaignTmpl", "profile": {"company_name": "X"}}).json()["id"]
    with get_db() as db:
        lead = Lead(agent_id=agent_id, name="Ravi", phone="+919811100099", source="test")
        db.add(lead)
        db.flush()
        lead_id = lead.id
        tmpl = MessageTemplate(agent_id=agent_id, name="Promo", channel="whatsapp", body="",
                               whatsapp_template_name="promo_blast", whatsapp_template_language="en_US",
                               is_meta_approved=True)
        db.add(tmpl)
    with get_db() as db:
        lead = db.get(Lead, lead_id)
        campaign = {"id": 1, "agent_id": agent_id, "message": "Hi {name}"}
        sent = {}

        async def fake_template(to, name, lang, variables, agent_id=None, lead_id=None, phone_number_id=None):
            sent["name"] = name
            return {"ok": True}
        import app.services.whatsapp_cloud_service as wa_mod
        orig = wa_mod.send_whatsapp_template
        wa_mod.send_whatsapp_template = fake_template
        try:
            result = campaigns._send_whatsapp_lead(campaign, lead)
        finally:
            wa_mod.send_whatsapp_template = orig
        assert result["ok"]
        assert sent["name"] == "promo_blast"


def test_campaign_whatsapp_skips_outside_window_without_template(client):
    from app.services import campaigns
    from app.models.lead import Lead

    agent_id = client.post("/api/agents", json={"name": "CampaignNoTmpl", "profile": {"company_name": "X"}}).json()["id"]
    with get_db() as db:
        lead = Lead(agent_id=agent_id, name="NoTmpl", phone="+919811100098", source="test")
        db.add(lead)
        db.flush()
        lead_id = lead.id
    with get_db() as db:
        lead = db.get(Lead, lead_id)
        campaign = {"id": 2, "agent_id": agent_id, "message": "Hi {name}"}
        result = campaigns._send_whatsapp_lead(campaign, lead)
    assert result is None
