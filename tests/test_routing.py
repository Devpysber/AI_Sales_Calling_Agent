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
