"""Meta WhatsApp Business product catalog messaging: send functions and builtin tools."""

import uuid

import pytest

from app.core.database import get_db
from app.models.channel import Conversation
from app.services import agent_tools
from app.services import agents as agent_service
from app.services import whatsapp_cloud_service as wa
from app.services.crm_service import CRMService


@pytest.fixture
def agent_id(client):
    name = f"Catalog Co {uuid.uuid4().hex[:8]}"
    return client.post("/api/agents", json={"name": name, "profile": {"company_name": name}}).json()["id"]


def _whatsapp_lead(agent_id, phone="+919811100099"):
    crm = CRMService(agent_id)
    lead = crm.create({"phone": phone, "name": "Catalog Customer"}, actor="system")
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

def test_send_whatsapp_product_payload(monkeypatch):
    captured = {}
    monkeypatch.setattr(wa, "_meta_post", lambda payload, *a, **k: (captured.update(payload=payload), {"ok": True})[1])
    wa.send_whatsapp_product("+919811100099", 1, 1, "CAT123", "SKU1", "Check this out!")
    assert captured["payload"] == {
        "messaging_product": "whatsapp", "to": "919811100099", "type": "interactive",
        "interactive": {"type": "product", "body": {"text": "Check this out!"},
                        "action": {"catalog_id": "CAT123", "product_retailer_id": "SKU1"}},
    }


def test_send_whatsapp_product_list_payload(monkeypatch):
    captured = {}
    monkeypatch.setattr(wa, "_meta_post", lambda payload, *a, **k: (captured.update(payload=payload), {"ok": True})[1])
    wa.send_whatsapp_product_list("+919811100099", 1, 1, "CAT123", "Our best sellers", "Pick one to learn more",
                                  [{"title": "Bestsellers", "product_retailer_ids": ["sku1", "sku2"]}])
    assert captured["payload"] == {
        "messaging_product": "whatsapp", "to": "919811100099", "type": "interactive",
        "interactive": {"type": "product_list",
                        "header": {"type": "text", "text": "Our best sellers"},
                        "body": {"text": "Pick one to learn more"},
                        "action": {"catalog_id": "CAT123",
                                   "sections": [{"title": "Bestsellers",
                                                 "product_items": [{"product_retailer_id": "sku1"},
                                                                    {"product_retailer_id": "sku2"}]}]}},
    }


def test_send_whatsapp_catalog_payload(monkeypatch):
    captured = {}
    monkeypatch.setattr(wa, "_meta_post", lambda payload, *a, **k: (captured.update(payload=payload), {"ok": True})[1])
    wa.send_whatsapp_catalog("+919811100099", 1, 1, "CAT123", "Browse our catalog", "sku1")
    assert captured["payload"] == {
        "messaging_product": "whatsapp", "to": "919811100099", "type": "interactive",
        "interactive": {"type": "catalog_message", "body": {"text": "Browse our catalog"},
                        "action": {"name": "catalog_message", "parameters": {"thumbnail_product_retailer_id": "sku1"}}},
    }


# ---- builtin tools: honest fallback when no catalog configured ----

def test_show_product_not_configured(agent_id):
    lead_id, conv_id = _whatsapp_lead(agent_id)
    out = agent_tools.show_product_tool(agent_id, lead_id, conv_id, "sku1")
    assert out == "No product catalog is connected for this business yet."


def test_show_product_list_not_configured(agent_id):
    lead_id, conv_id = _whatsapp_lead(agent_id)
    out = agent_tools.show_product_list_tool(agent_id, lead_id, conv_id, "Our products",
                                             [{"title": "A", "product_ids": ["sku1"]}])
    assert out == "No product catalog is connected for this business yet."


def test_browse_catalog_not_configured(agent_id):
    lead_id, conv_id = _whatsapp_lead(agent_id)
    out = agent_tools.browse_catalog_tool(agent_id, lead_id, conv_id)
    assert out == "No product catalog is connected for this business yet."


# ---- builtin tools: configured + whatsapp channel calls through ----

def test_show_product_calls_through_when_configured(agent_id, monkeypatch):
    agent_service.update_profile(agent_id, {"whatsapp_catalog_id": "CAT123"})
    lead_id, conv_id = _whatsapp_lead(agent_id, phone="+919811100098")
    calls = []
    monkeypatch.setattr(wa, "send_whatsapp_product",
                        lambda to, aid, lid, cat, pid, msg="Check this out!": (calls.append((to, aid, lid, cat, pid, msg)),
                                                                                {"ok": True})[1])
    out = agent_tools.show_product_tool(agent_id, lead_id, conv_id, "sku1", "Look at this")
    assert out == "Product sent."
    assert calls == [("+919811100098", agent_id, lead_id, "CAT123", "sku1", "Look at this")]


def test_show_product_list_calls_through_when_configured(agent_id, monkeypatch):
    agent_service.update_profile(agent_id, {"whatsapp_catalog_id": "CAT123"})
    lead_id, conv_id = _whatsapp_lead(agent_id, phone="+919811100097")
    calls = []
    monkeypatch.setattr(wa, "send_whatsapp_product_list",
                        lambda to, aid, lid, cat, header, body, sections: (calls.append((to, aid, lid, cat, header, body, sections)),
                                                                            {"ok": True})[1])
    out = agent_tools.show_product_list_tool(agent_id, lead_id, conv_id, "Our products",
                                             [{"title": "A", "product_ids": ["sku1", "sku2"]}])
    assert out == "Product list sent."
    assert calls == [("+919811100097", agent_id, lead_id, "CAT123", "Our products", "Pick one to learn more",
                      [{"title": "A", "product_retailer_ids": ["sku1", "sku2"]}])]


def test_browse_catalog_calls_through_when_configured(agent_id, monkeypatch):
    agent_service.update_profile(agent_id, {"whatsapp_catalog_id": "CAT123"})
    lead_id, conv_id = _whatsapp_lead(agent_id, phone="+919811100096")
    calls = []
    monkeypatch.setattr(wa, "send_whatsapp_catalog",
                        lambda to, aid, lid, cat, msg: (calls.append((to, aid, lid, cat, msg)), {"ok": True})[1])
    out = agent_tools.browse_catalog_tool(agent_id, lead_id, conv_id, "Browse our full catalog")
    assert out == "Catalog sent."
    assert calls == [("+919811100096", agent_id, lead_id, "CAT123", "Browse our full catalog")]


# ---- Instagram channel: WhatsApp-only fallback once catalog is configured ----

def test_show_product_instagram_fallback(agent_id):
    agent_service.update_profile(agent_id, {"whatsapp_catalog_id": "CAT123"})
    lead_id, conv_id = _instagram_lead(agent_id)
    out = agent_tools.show_product_tool(agent_id, lead_id, conv_id, "sku1")
    assert out == "The product catalog can only be shown on WhatsApp today."


def test_show_product_list_instagram_fallback(agent_id):
    agent_service.update_profile(agent_id, {"whatsapp_catalog_id": "CAT123"})
    lead_id, conv_id = _instagram_lead(agent_id)
    out = agent_tools.show_product_list_tool(agent_id, lead_id, conv_id, "Our products",
                                             [{"title": "A", "product_ids": ["sku1"]}])
    assert out == "The product catalog can only be shown on WhatsApp today."
