"""Inbound routing (AI / forward / closed message), live transfer XML and supervision auth."""

import pytest


@pytest.fixture(scope="module")
def support(client):
    agent = client.post("/api/agents", json={"name": "Routing desk", "phone_number": "+91 80 7777 0000"}).json()
    return agent["id"]


def _answer(client, uuid):
    return client.post("/api/plivo/answer", data={"From": "919000000077", "To": "918077770000", "CallUUID": uuid}).text


def test_routing_validation(client, support):
    url = f"/api/agents/{support}/profile"
    assert client.put(url, json={"inbound_mode": "forward"}).status_code == 400          # no transfer number yet
    assert client.put(url, json={"transfer_number": "12345"}).status_code == 400          # too short
    assert client.put(url, json={"inbound_mode": "voicemail"}).status_code == 400         # unknown mode
    saved = client.put(url, json={"transfer_number": "+91 98765 43210"}).json()
    assert saved["transfer_number"] == "+919876543210"


def test_forward_and_closed_message(client, support, monkeypatch):
    from app.api import plivo as webhooks

    url = f"/api/agents/{support}/profile"
    client.put(url, json={"transfer_number": "+91 98765 43210", "inbound_mode": "forward", "after_hours_mode": "message",
                          "after_hours_message": "We are closed."})

    monkeypatch.setattr("app.services.call_service.within_calling_hours", lambda cfg, now=None: True)
    xml = _answer(client, "fwd-1")
    assert "<Dial" in xml and "919876543210" in xml
    calls = client.get(f"/api/agents/{support}/calls", params={"direction": "inbound"}).json()["items"]
    assert calls[0]["trigger"] == "forwarded"

    monkeypatch.setattr("app.services.call_service.within_calling_hours", lambda cfg, now=None: False)
    xml = _answer(client, "closed-1")
    assert "<Hangup" in xml and "<Dial" not in xml
    assert webhooks.inbound_route({"after_hours_mode": "forward", "transfer_number": ""}, support) == "ai"

    # Unanswered transfer: caller hears an apology instead of dead air
    done = client.post("/api/plivo/transfer-done", data={"DialStatus": "no-answer"}).text
    assert "<Speak" in done and "<Hangup" in done


def test_transfer_xml_dials_team(client, support):
    from app.services import call_session

    client.put(f"/api/agents/{support}/profile", json={"transfer_number": "+91 98765 43210", "inbound_mode": "ai"})
    session = call_session.create(agent_id=support, lead_id=None, lead={}, language="en-IN")
    xml = client.post(f"/api/plivo/transfer?sid={session['id']}").text
    assert "<Dial" in xml and "919876543210" in xml


def test_supervision_socket_requires_login(support):
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from app.main import app

    anonymous = TestClient(app)
    with pytest.raises(WebSocketDisconnect):
        with anonymous.websocket_connect(f"/api/agents/{support}/calls/1/monitor") as ws:
            ws.receive_json()


def test_unanswered_forward_falls_back_to_ai(client, support, monkeypatch):
    from app.core.config import settings
    from app.services import call_session

    monkeypatch.setattr(settings, "voice_mode", "stream")
    client.put(f"/api/agents/{support}/profile", json={"transfer_number": "9876543210", "forward_fallback": "ai"})
    assert client.get(f"/api/agents/{support}/profile").json()["profile"]["transfer_number"] == "+919876543210"
    session = call_session.create(agent_id=support, lead_id=None, lead={"phone": "+919000000077"}, language="en-IN")
    xml = client.post(f"/api/plivo/transfer-done?sid={session['id']}", data={"DialStatus": "no-answer"}).text
    assert "<Stream" in xml
    assert call_session.get(session["id"])["history"][-1]["text"].startswith("Sorry, our team is busy")

    client.put(f"/api/agents/{support}/profile", json={"forward_fallback": "message"})
    xml = client.post(f"/api/plivo/transfer-done?sid={session['id']}", data={"DialStatus": "busy"}).text
    assert "<Stream" not in xml and "<Hangup" in xml


def test_alerts_summary_and_snooze(client, monkeypatch):
    from app.services import alerts

    monkeypatch.setattr(alerts, "_plivo", lambda: {"provider": "Plivo", "label": "x", "value": "0.10", "level": "critical", "detail": "≈ 20 min left",
                                                     "facts": [], "action": {"label": "Top up", "url": "https://console.plivo.com/billing/"}, "balance": 0.1, "unit": "credits"})
    monkeypatch.setattr(alerts, "_openrouter", lambda: None)
    monkeypatch.setattr(alerts, "_routing", lambda: [])
    data = client.get("/api/system/alerts", params={"refresh": True}).json()
    assert data["popup"]["provider"] == "Plivo" and any(i["kind"] == "credit" for i in data["items"])
    assert client.post("/api/system/alerts/snooze", json={"key": "popup:Plivo", "hours": 1}).status_code == 200
    assert client.get("/api/system/alerts").json()["popup"] is None


def test_unknown_inbound_caller_becomes_lead_and_queue_dials(client, base, monkeypatch):
    from app.services import agent as agent_service
    from app.services import scheduler
    from app.services.call_service import CallService

    agent_id = int(base.rsplit("/", 1)[1])
    client.put(f"{base}/profile", json={"inbound_mode": "ai"})
    monkeypatch.setattr("app.services.agents.for_inbound", lambda to, lead_agent_id=None: agent_id)
    client.post("/api/plivo/answer", data={"From": "919812300077", "To": "918000000000", "CallUUID": "new-caller-1"})
    lead = client.get(f"{base}/leads", params={"search": "9812300077"}).json()["items"][0]
    assert lead["source"] == "inbound call" and not lead["name"]
    assert "not yet in our CRM" in agent_service.call_goal({}, "inbound_new")

    # Summary fills the new caller's details without touching known fields
    monkeypatch.setattr(agent_service, "summarize", lambda history: {"summary": "Asked about prices.", "name": "Neha Gupta", "city": "Pune"})
    CallService(agent_id)._summarize(0, lead["id"], [{"role": "customer", "text": "I am Neha from Pune"}])
    lead = client.get(f"{base}/leads/{lead['id']}").json()
    assert lead["name"] == "Neha Gupta" and lead["city"] == "Pune"

    # Queue: pending leads are dialled when a slot is free, even with auto-dial off
    queued = client.post(f"{base}/leads/bulk/queue", json={"ids": [lead["id"]]}).json()
    assert queued["queued"] == 1 and "eta" in queued
    placed = []
    monkeypatch.setattr(scheduler, "within_calling_hours", lambda cfg, now=None: True)
    monkeypatch.setattr(CallService, "start", lambda self, lead_id, trigger="manual", actor="admin", purpose=None: placed.append((lead_id, trigger)))
    scheduler.job_queue(agent_id, {"max_concurrent_calls": 50})  # oldest queued leads go first
    assert (lead["id"], "queue") in placed


def test_voice_preview_accepts_language(client, base):
    assert client.post(f"{base}/voice-preview", json={"text": "नमस्ते", "language": "hi-IN"}).status_code == 200
