import re
import time


def _session_id(cid):
    from app.core.database import get_db
    from app.models.call import Call
    with get_db() as db:
        return db.get(Call, cid).session_id


def test_health_and_auth(client, base):
    assert client.get("/api/health").json()["status"] == "ok"
    from fastapi.testclient import TestClient
    from app.main import app
    anon = TestClient(app)
    assert anon.get(f"{base}/leads").status_code == 401
    assert anon.get("/api/agents").status_code == 401
    assert anon.post("/api/auth/login", json={"username": "admin", "password": "wrong"}).status_code == 401


def test_agent_crud(client):
    created = client.post("/api/agents", json={"name": "Temp", "color": "#0e8a5e", "phone_number": "080 1234 5678"}).json()
    assert created["color"] == "#0e8a5e"
    assert created["phone_number"] == "+08012345678" and created["status"] == "active"
    aid = created["id"]
    assert client.patch(f"/api/agents/{aid}", json={"status": "paused"}).json()["status"] == "paused"
    assert client.patch(f"/api/agents/{aid}", json={"status": "nope"}).status_code == 400
    profile = client.put(f"/api/agents/{aid}/profile", json={"agent_name": "Neha"}).json()
    assert profile["agent_name"] == "Neha"
    assert any(a["id"] == aid for a in client.get("/api/agents").json()["agents"])
    assert client.delete(f"/api/agents/{aid}").json()["ok"]
    assert client.get(f"/api/agents/{aid}").status_code == 404
    assert client.get(f"/api/agents/{aid}/leads").status_code == 404


def test_lead_crud_and_import(client, base):
    res = client.post(f"{base}/leads", json={"name": "Rahul", "phone": "98765 43210", "company": "Acme"})
    assert res.status_code == 200, res.text
    lead = res.json()
    assert lead["phone"] == "+919876543210"

    assert client.post(f"{base}/leads", json={"phone": "123"}).status_code == 400
    assert client.patch(f"{base}/leads/{lead['id']}", json={"city": "Pune", "tags": ["vip"]}).json()["tags"] == ["vip"]

    csv = "Full Name,Mobile Number,Company,Language\nPriya,9000000001,Nair,Hindi\nBad,12,X,\nDup,9876543210,Y,\n"
    preview = client.post(f"{base}/leads/import/preview", files={"file": ("l.csv", csv, "text/csv")}).json()
    assert preview["mapping"]["Mobile Number"] == "phone"
    result = client.post(f"{base}/leads/import", files={"file": ("l.csv", csv, "text/csv")},
                         data={"skip_duplicates": "true", "tags": "campaign-1"}).json()
    assert result["created"] == 1 and result["skipped_duplicates"] == 1 and len(result["errors"]) == 1

    page = client.get(f"{base}/leads", params={"search": "Priya"}).json()
    assert page["total"] == 1 and page["items"][0]["language"] == "hi-IN"
    assert any(e["type"] == "lead.updated" for e in client.get(f"{base}/leads/{lead['id']}/activity").json())


def test_workspaces_are_isolated(client, base):
    other = client.post("/api/agents", json={"name": "Sales B"}).json()
    b = f"/api/agents/{other['id']}"
    lead_a = client.post(f"{base}/leads", json={"name": "Only A", "phone": "9333333333"}).json()

    # Same phone is not a duplicate across agents; lead ids never resolve in another workspace.
    assert client.post(f"{b}/leads", json={"name": "Only B", "phone": "9333333333"}).status_code == 200
    assert client.get(f"{b}/leads/{lead_a['id']}").status_code == 404
    assert client.patch(f"{b}/leads/{lead_a['id']}", json={"city": "X"}).status_code == 404
    assert client.post(f"{b}/leads/bulk/delete", json={"ids": [lead_a["id"]]}).json()["deleted"] == 0
    assert client.post(f"{b}/calls", json={"lead_id": lead_a["id"]}).status_code == 400
    assert all(l["name"] != "Only A" for l in client.get(f"{b}/leads", params={"page_size": 200}).json()["items"])
    assert all(e["agent_id"] == other["id"] for e in client.get(f"{b}/activity").json())

    doc = client.post(f"{base}/knowledge/text", json={"title": "A secret", "text": "Agent A only sells the Zebra platinum plan to hospitals."}).json()
    for _ in range(50):
        if client.get(f"{base}/knowledge/{doc['id']}").json()["status"] == "ready":
            break
        time.sleep(0.05)
    assert client.post(f"{base}/knowledge/search", json={"query": "zebra platinum"}).json()["results"]
    assert client.post(f"{b}/knowledge/search", json={"query": "zebra platinum"}).json()["results"] == []
    assert client.get(f"{b}/knowledge/{doc['id']}").status_code == 404
    assert client.get(f"{b}/knowledge").json()["documents"] == []


def test_knowledge_rag(client, base):
    doc = client.post(f"{base}/knowledge/text", json={
        "title": "Pricing", "text": "Our Growth plan costs 40,000 rupees per month and includes 5,000 AI call minutes."}).json()
    for _ in range(50):
        if client.get(f"{base}/knowledge/{doc['id']}").json()["status"] == "ready":
            break
        time.sleep(0.05)
    results = client.post(f"{base}/knowledge/search", json={"query": "growth plan price"}).json()["results"]
    assert results and "Growth plan" in results[0]["text"]

    reply = client.post(f"{base}/playground", json={"message": "What does the growth plan cost?", "speak": True}).json()
    assert reply["reply"] == "Our Growth plan fits you."  # knowledge reached the prompt
    assert reply["audio_url"].startswith("/api/media/audio/")
    assert client.get(reply["audio_url"]).content == b"RIFF-fake-wav"


def test_full_call_flow(client, base):
    lead = client.post(f"{base}/leads", json={"name": "Caller", "phone": "9111111111"}).json()
    call = client.post(f"{base}/calls", json={"lead_id": lead["id"]}).json()
    cid = call["call_id"]
    sid = _session_id(cid)

    xml = client.post(f"/api/plivo/answer?sid={sid}&cid={cid}", data={"CallUUID": "uuid-1"}).text
    assert "<GetInput" in xml and "/api/media/audio/" in xml

    xml = client.post(f"/api/plivo/input?sid={sid}&cid={cid}", data={"Speech": "what is the price"}).text
    for _ in range(10):
        if "/api/plivo/wait" not in xml:
            break
        xml = client.post(f"/api/plivo/wait?sid={sid}&cid={cid}").text
    assert "<GetInput" in xml and re.search(r"/api/media/audio/\w+\.wav", xml)

    live = client.get(f"{base}/calls/{cid}").json()
    assert live["status"] == "In Progress" and len(live["transcript"]) == 3

    client.post("/api/plivo/hangup", data={"cid": cid, "CallUUID": "uuid-1", "CallStatus": "completed", "Duration": "64"})
    for _ in range(50):
        done = client.get(f"{base}/calls/{cid}").json()
        if done.get("summary"):
            break
        time.sleep(0.05)
    assert done["status"] == "Completed" and done["duration"] == 64
    assert done["qualification"] == "Hot" and done["outcome"] == "meeting_booked"

    # The summary lands on the call before the lead update and meeting event finish
    for _ in range(50):
        types = {e["type"] for e in client.get(f"{base}/leads/{lead['id']}/activity").json()}
        if "meeting.booked" in types:
            break
        time.sleep(0.05)
    lead = client.get(f"{base}/leads/{lead['id']}").json()
    assert lead["status"] == "Meeting Booked" and lead["meeting_at"] == "2026-09-20 15:00"
    assert {"call.started", "call.answered", "call.ended", "meeting.booked"} <= types


def test_unanswered_call_increments_retry(client, base):
    lead = client.post(f"{base}/leads", json={"name": "Busy", "phone": "9222222222"}).json()
    cid = client.post(f"{base}/calls", json={"lead_id": lead["id"]}).json()["call_id"]
    client.post("/api/plivo/hangup", data={"cid": cid, "CallStatus": "busy", "Duration": "0"})
    lead = client.get(f"{base}/leads/{lead['id']}").json()
    assert lead["call_status"] == "Busy" and lead["retry_count"] == 1


def test_inbound_routes_by_dialled_number(client):
    agent = client.post("/api/agents", json={"name": "Support line", "phone_number": "+91 80 5555 0000"}).json()
    xml = client.post("/api/plivo/answer", data={"From": "919000000009", "To": "918055550000", "CallUUID": "in-1"}).text
    assert "<GetInput" in xml
    calls = client.get(f"/api/agents/{agent['id']}/calls").json()["items"]
    assert calls and calls[0]["direction"] == "inbound"


def test_signature_enforced(client, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "plivo_validate_signature", True)
    assert client.post("/api/plivo/answer", data={"From": "1"}).status_code == 403


def test_automation_settings(client, base):
    cfg = client.put(f"{base}/automation", json={"max_retries": 5, "calling_days": [0, 1]}).json()
    assert cfg["max_retries"] == 5 and cfg["calling_days"] == [0, 1]
    assert client.put(f"{base}/automation", json={"nope": 1}).status_code == 400
    assert "auto_dial" in client.get(f"{base}/automation").json()["jobs"]


def test_hindi_reply_keeps_supported_asr_language(client, base, monkeypatch):
    """Plivo rejects GetInput language="hi-IN" and drops the call; ASR must stay en-IN."""
    from app.services import llm
    monkeypatch.setattr(llm, "complete", lambda *a, **k: llm.LLMResult(
        '{"reply":"जी हाँ, बताइए","language":"hi-IN","intent":"interested","end_call":false,"crm_update":{}}', "fake", "m", 5))
    lead = client.post(f"{base}/leads", json={"name": "Hindi", "phone": "9444444444", "language": "hi-IN"}).json()
    cid = client.post(f"{base}/calls", json={"lead_id": lead["id"]}).json()["call_id"]
    sid = _session_id(cid)
    xml = client.post(f"/api/plivo/answer?sid={sid}&cid={cid}", data={"CallUUID": "u-hi"}).text
    xml += client.post(f"/api/plivo/input?sid={sid}&cid={cid}", data={"Speech": "haan bataiye"}).text
    for _ in range(10):
        if "/api/plivo/wait" not in xml.split("</Response>")[-2]:
            break
        xml += client.post(f"/api/plivo/wait?sid={sid}&cid={cid}").text
    assert 'language="hi-IN"' not in xml and xml.count('language="en-IN"') >= 2
    assert "hold_short" not in xml


def test_agents_overview(client, base):
    data = client.get("/api/agents/overview").json()
    assert data["agents"] and len(data["series"]) == 14
    first = data["agents"][0]
    assert {"series", "period", "pipeline", "setup", "within_calling_hours"} <= set(first)
    assert all(e["agent_id"] is not None for e in data["activity"])
