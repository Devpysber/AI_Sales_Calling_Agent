"""Meta WhatsApp Flows (native multi-screen forms, navigate-only): CRUD, publish, send payload shape,
and nfm_reply webhook parsing."""

import json

from app.services import whatsapp_cloud_service as wa

SCREENS = [
    {"id": "WELCOME", "title": "Get a quote", "terminal": False,
     "layout": {"type": "SingleColumnLayout", "children": [
         {"type": "TextHeading", "text": "Tell us about your needs"},
         {"type": "TextInput", "name": "full_name", "label": "Full name", "required": True},
         {"type": "Footer", "label": "Continue",
          "on-click-action": {"name": "navigate", "next": {"type": "screen", "name": "CONFIRM"},
                               "payload": {"full_name": "${form.full_name}"}}}]}},
    {"id": "CONFIRM", "title": "Thanks!", "terminal": True,
     "layout": {"type": "SingleColumnLayout", "children": [
         {"type": "TextBody", "text": "We've got your details."},
         {"type": "Footer", "label": "Done",
          "on-click-action": {"name": "complete", "payload": {"full_name": "${form.full_name}"}}}]}},
]


def test_flow_crud(client, base):
    created = client.post(f"{base}/whatsapp/flows", json={
        "name": "Get a quote", "category": "LEAD_GENERATION", "screens": SCREENS,
    }).json()
    assert created["status"] == "draft"
    assert created["screens"][0]["id"] == "WELCOME"

    listed = client.get(f"{base}/whatsapp/flows").json()
    assert any(f["id"] == created["id"] for f in listed)

    got = client.get(f"{base}/whatsapp/flows/{created['id']}").json()
    assert got["name"] == "Get a quote"

    res = client.delete(f"{base}/whatsapp/flows/{created['id']}")
    assert res.status_code == 200
    assert client.get(f"{base}/whatsapp/flows/{created['id']}").status_code == 404


def test_create_flow_requires_screens(client, base):
    res = client.post(f"{base}/whatsapp/flows", json={"name": "Empty", "screens": []})
    assert res.status_code == 400


def test_publish_flow_without_waba_id_returns_400(client, base):
    created = client.post(f"{base}/whatsapp/flows", json={
        "name": "No WABA Flow", "screens": SCREENS,
    }).json()
    res = client.post(f"{base}/whatsapp/flows/{created['id']}/publish")
    assert res.status_code == 400
    assert "Business Account ID" in res.json()["detail"]


def test_send_unpublished_flow_rejected(client, base):
    created = client.post(f"{base}/whatsapp/flows", json={
        "name": "Draft Flow", "screens": SCREENS,
    }).json()
    res = client.post(f"{base}/whatsapp/flows/{created['id']}/send", json={"phone": "919811100099"})
    assert res.status_code == 400


def test_send_whatsapp_flow_payload_shape(monkeypatch):
    captured = {}

    def fake_meta_post(payload, agent_id, lead_id, phone_number_id=None):
        captured["payload"] = payload
        return {"ok": True, "data": {}}
    monkeypatch.setattr(wa, "_meta_post", fake_meta_post)

    out = wa.send_whatsapp_flow("919811100099", 1, None, "flow123", "Let's get you a quote",
                                cta_text="Start", first_screen_id="WELCOME")
    assert out["ok"]
    payload = captured["payload"]
    assert payload["type"] == "interactive"
    assert payload["interactive"]["type"] == "flow"
    assert payload["interactive"]["body"]["text"] == "Let's get you a quote"
    action = payload["interactive"]["action"]
    assert action["name"] == "flow"
    params = action["parameters"]
    assert params["flow_message_version"] == "3"
    assert params["flow_id"] == "flow123"
    assert params["flow_cta"] == "Start"
    assert params["flow_action"] == "navigate"
    assert params["flow_action_payload"] == {"screen": "WELCOME"}


def test_parse_webhook_nfm_reply_extracts_fields():
    payload = {
        "entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": "pid1", "display_phone_number": "911234567890"},
            "contacts": [{"wa_id": "919811100099", "profile": {"name": "Ravi"}}],
            "messages": [{
                "id": "wamid.FLOW1", "from": "919811100099", "type": "interactive",
                "interactive": {
                    "type": "nfm_reply",
                    "nfm_reply": {
                        "response_json": json.dumps({"full_name": "Ravi Kumar", "phone": "9876543210"}),
                    },
                },
            }],
        }}]}]
    }
    out = wa.parse_webhook(payload)
    assert len(out) == 1
    item = out[0]
    assert item["type"] == "flow_response"
    assert item["flow_data"] == {"full_name": "Ravi Kumar", "phone": "9876543210"}


def test_flow_response_writes_lead_fields(client, base):
    from app.core.database import get_db
    from app.models.lead import Lead
    from sqlalchemy import select

    agent_id = int(base.rsplit("/", 1)[1])
    msg = {
        "from": "919822200088", "type": "flow_response", "wamid": "wamid.FLOWTEST",
        "text": "", "name": "New Contact",
        "flow_data": {"full_name": "Priya Shah", "phone": "9822200088"},
        "phone_number_id": "pid1", "display_phone_number": "",
    }
    ctx, added = wa._record_turn(msg, agent_id, "wamid.FLOWTEST", "919822200088")
    assert added
    with get_db() as db:
        lead = db.scalar(select(Lead).where(Lead.agent_id == agent_id, Lead.phone == "+919822200088"))
        assert lead is not None
        assert lead.name == "Priya Shah"
