"""Meta WhatsApp CTA-URL button and location-request messages: send functions, builtin tools, inbound parsing."""

import uuid

import pytest

from app.core.database import get_db
from app.models.channel import Conversation
from app.services import agent_tools
from app.services import whatsapp_cloud_service as wa
from app.services.crm_service import CRMService


@pytest.fixture
def agent_id(client):
    name = f"Interactive Co {uuid.uuid4().hex[:8]}"
    return client.post("/api/agents", json={"name": name, "profile": {"company_name": name}}).json()["id"]


def _whatsapp_lead(agent_id, phone="+919811100099"):
    crm = CRMService(agent_id)
    lead = crm.create({"phone": phone, "name": "Interactive Customer"}, actor="system")
    with get_db() as db:
        conv = Conversation(agent_id=agent_id, lead_id=lead["id"], channel="whatsapp", status="open")
        db.add(conv)
        db.flush()
        conv_id = conv.id
    return lead["id"], conv_id


def _instagram_lead(agent_id):
    crm = CRMService(agent_id)
    lead = crm.create({"phone": None, "name": "IG Customer"}, actor="system", allow_no_phone=True)
    with get_db() as db:
        conv = Conversation(agent_id=agent_id, lead_id=lead["id"], channel="instagram", status="open")
        db.add(conv)
        db.flush()
        conv_id = conv.id
    return lead["id"], conv_id


# ---- send functions: exact Meta payload shapes ----

def test_send_whatsapp_cta_url_payload(monkeypatch):
    captured = {}
    monkeypatch.setattr(wa, "_meta_post", lambda payload, *a, **k: (captured.update(payload=payload), {"ok": True})[1])
    wa.send_whatsapp_cta_url("+919811100099", 1, 1, "Ready to book your slot?", "Book now", "https://example.com/book")
    assert captured["payload"] == {
        "messaging_product": "whatsapp", "to": "919811100099", "type": "interactive",
        "interactive": {"type": "cta_url", "body": {"text": "Ready to book your slot?"},
                        "action": {"name": "cta_url",
                                   "parameters": {"display_text": "Book now", "url": "https://example.com/book"}}},
    }


def test_send_whatsapp_location_request_payload(monkeypatch):
    captured = {}
    monkeypatch.setattr(wa, "_meta_post", lambda payload, *a, **k: (captured.update(payload=payload), {"ok": True})[1])
    wa.send_whatsapp_location_request("+919811100099", 1, 1, "Please share your delivery location")
    assert captured["payload"] == {
        "messaging_product": "whatsapp", "to": "919811100099", "type": "interactive",
        "interactive": {"type": "location_request_message", "body": {"text": "Please share your delivery location"},
                        "action": {"name": "send_location"}},
    }


# ---- builtin tools: WhatsApp calls through ----

def test_send_cta_button_calls_through_on_whatsapp(agent_id, monkeypatch):
    lead_id, conv_id = _whatsapp_lead(agent_id, phone="+919811100098")
    calls = []
    monkeypatch.setattr(wa, "send_whatsapp_cta_url",
                        lambda to, aid, lid, body, btn, url: (calls.append((to, aid, lid, body, btn, url)), {"ok": True})[1])
    out = agent_tools.send_cta_button(agent_id, lead_id, conv_id, "Ready to book?", "Book now", "https://example.com/book")
    assert out == "Sent."
    assert calls == [("+919811100098", agent_id, lead_id, "Ready to book?", "Book now", "https://example.com/book")]


def test_send_cta_button_instagram_fallback(agent_id):
    lead_id, conv_id = _instagram_lead(agent_id)
    out = agent_tools.send_cta_button(agent_id, lead_id, conv_id, "Ready to book?", "Book now", "https://example.com/book")
    assert out == "Ready to book? https://example.com/book"


def test_request_customer_location_calls_through_on_whatsapp(agent_id, monkeypatch):
    lead_id, conv_id = _whatsapp_lead(agent_id, phone="+919811100097")
    calls = []
    monkeypatch.setattr(wa, "send_whatsapp_location_request",
                        lambda to, aid, lid, body: (calls.append((to, aid, lid, body)), {"ok": True})[1])
    out = agent_tools.request_customer_location(agent_id, lead_id, conv_id, "Please share your delivery location")
    assert out == "Sent."
    assert calls == [("+919811100097", agent_id, lead_id, "Please share your delivery location")]


def test_request_customer_location_instagram_fallback(agent_id):
    lead_id, conv_id = _instagram_lead(agent_id)
    out = agent_tools.request_customer_location(agent_id, lead_id, conv_id, "Please share your delivery location")
    assert out == "Please share your delivery location"


# ---- inbound: location message parsing ----

def test_parse_webhook_location_message():
    payload = {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"display_phone_number": "918000000099", "phone_number_id": "555"},
        "contacts": [{"profile": {"name": "Ravi"}, "wa_id": "919811100001"}],
        "messages": [{"from": "919811100001", "id": "wamid.LOC1", "timestamp": "1700000000", "type": "location",
                      "location": {"latitude": 12.34, "longitude": 56.78, "name": "Home", "address": "123 Main St"}}]}}]}]}
    [m] = wa.parse_webhook(payload)
    assert m["type"] == "location"
    assert m["text"] == "[location: 12.34,56.78 Home]"
