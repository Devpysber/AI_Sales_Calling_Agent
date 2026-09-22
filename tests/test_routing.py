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
    assert "<Record" in xml and "<Dial" not in xml
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
    assert "<Stream" not in xml and "<Record" in xml


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


def test_inbound_collect_setting_and_cross_agent_recognition(client, base, monkeypatch):
    agent_id = int(base.rsplit("/", 1)[1])
    saved = client.put(f"{base}/profile", json={"inbound_collect": ["name", "email", "budget"]}).json()
    assert saved["inbound_collect"] == ["name", "email", "budget"]

    other = client.post("/api/agents", json={"name": "Second site"}).json()
    client.post(f"/api/agents/{other['id']}/leads", json={"name": "Known Elsewhere", "phone": "9812300088", "city": "Delhi"})
    monkeypatch.setattr("app.services.agents.for_inbound", lambda to, lead_agent_id=None: agent_id)
    client.post("/api/plivo/answer", data={"From": "919812300088", "To": "918000000000", "CallUUID": "cross-1"})
    lead = client.get(f"{base}/leads", params={"search": "9812300088"}).json()["items"][0]
    assert lead["name"] == "Known Elsewhere" and lead["city"] == "Delhi" and lead["source"] == "inbound call"


def test_known_caller_reaches_their_own_agent(client, base, monkeypatch):
    """A number some agent already knows is answered by that agent, not by the first agent on the line."""
    from app.services import agents

    other = client.post("/api/agents", json={"name": "Caller's own agent"}).json()
    client.post(f"/api/agents/{other['id']}/leads", json={"name": "Returning Caller", "phone": "9812300099"})
    monkeypatch.setattr("app.services.call_service.within_calling_hours", lambda cfg, now=None: True)

    assert agents.for_inbound("918000000000") != other["id"]                           # unknown caller: dialled-number routing
    assert agents.for_inbound("918000000000", lead_agent_id=other["id"]) == other["id"]  # known caller: their agent wins

    client.post("/api/plivo/answer", data={"From": "919812300099", "To": "918000000000", "CallUUID": "own-1"})
    calls = client.get(f"/api/agents/{other['id']}/calls", params={"direction": "inbound"}).json()["items"]
    assert calls and calls[0]["lead_id"] and calls[0]["agent_id"] == other["id"]
    assert not client.get(f"{base}/leads", params={"search": "9812300099"}).json()["items"]  # no duplicate lead on the other agent


def test_caller_known_to_two_agents_is_asked_which_desk(client, base, monkeypatch):
    from app.services import agent, agents, call_session

    a = client.post("/api/agents", json={"name": "Cars desk"}).json()
    b = client.post("/api/agents", json={"name": "Homes desk"}).json()
    client.put(f"/api/agents/{a['id']}/profile", json={"company_name": "Acme Cars"})
    client.put(f"/api/agents/{b['id']}/profile", json={"company_name": "Blue Homes"})
    client.post(f"/api/agents/{a['id']}/leads", json={"name": "Two Desks", "phone": "9812300111"})
    client.post(f"/api/agents/{b['id']}/leads", json={"name": "Two Desks", "phone": "9812300111", "language": "hi-IN"})
    monkeypatch.setattr("app.services.call_service.within_calling_hours", lambda cfg, now=None: True)

    choices = agents.inbound_choices([a["id"], b["id"]])
    assert [c["label"] for c in choices] == ["Acme Cars", "Blue Homes"]
    assert agent.choose_agent("I'm calling about blue homes", choices) == b["id"]
    assert agent.choose_agent("acme", choices) == a["id"]

    from app.services.call_service import CallService

    session = CallService(None).create_inbound("919812300111", "918000000000", "two-1")
    assert session["agent_id"] == a["id"] and session["lead"]["call_purpose"] == "inbound_choose"
    assert session["language"] == "hi-IN"  # last spoken language wins, whichever desk it was recorded on
    assert call_session.get(session["id"])["lead"]["choices"] == choices
    text = agent.greeting(a["id"], session["lead"], "en-IN")
    assert "Acme Cars or Blue Homes" in text and "Two Desks" in text


def test_recent_calls_tool_reports_real_durations(client, base):
    from app.services import agent_tools

    agent_id = int(base.rsplit("/", 1)[1])
    out = agent_tools.execute_tool("recent_calls", '{"limit": 3}', agent_id, role="team")
    assert "Latest calls" in out or "No calls found" in out
    assert "recent_calls" in {t["function"]["name"] for t in agent_tools.get_tools_for_role("team")}


def test_quick_action_patterns_and_team_tools(client, base):
    from app.services import agent_tools
    from app.services.voice_stream import DETAILS_REQUEST, DETAILS_WORDS, DNC_NOT_NOW, DNC_REQUEST

    # Explicit do-not-call, in either language, without a scheduling word
    for said in ("dobara call mat karna", "please don't call me again", "मेरा number हटा दो", "remove my number"):
        assert DNC_REQUEST.search(said) and not DNC_NOT_NOW.search(said), said
    # "Call later" is a callback, never a DNC
    for said in ("abhi call mat karo, shaam ko karna", "don't call now, call tomorrow", "अभी नहीं, कल कॉल करना"):
        assert not DNC_REQUEST.search(said) or DNC_NOT_NOW.search(said), said
    # Details on WhatsApp / SMS
    for said in ("details WhatsApp par bhej do", "can you send me the link on sms", "व्हाट्सएप पर जानकारी भेज दो"):
        assert DETAILS_REQUEST.search(said) and DETAILS_WORDS.search(said), said
    assert not (DETAILS_REQUEST.search("I will message you later") and DETAILS_WORDS.search("I will message you later"))

    agent_id = int(base.rsplit("/", 1)[1])
    names = {t["function"]["name"] for t in agent_tools.get_tools_for_role("team")}
    assert {"today_stats", "set_automation", "recent_calls"} <= names
    assert "Today:" in agent_tools.execute_tool("today_stats", "{}", agent_id, role="team")
    assert "off" in agent_tools.execute_tool("set_automation", '{"switch": "auto_dial", "on": false}', agent_id, role="team")
    multi = agent_tools.execute_tool("set_automation", '{"switches": ["retry", "nurture", "speed_to_lead"], "on": false}', agent_id, role="team")
    assert "Retry calls" in multi and "Follow-up calls" in multi and "Speed to lead" in multi
    from app.services.voice_stream import is_caller_closing
    assert is_caller_closing("ठीक है, बाय") and is_caller_closing("okay bye") and is_caller_closing("chalo tata")
    assert "Failed" in agent_tools.execute_tool("set_automation", '{"switch": "warp_drive", "on": true}', agent_id, role="team")
    assert client.get(f"{base}/automation").json()["settings"]["auto_dial_enabled"] is False


def test_live_feed_carries_latest_event_stamp(client, base):
    from app.services import agent_tools

    agent_id = int(base.rsplit("/", 1)[1])
    before = client.get("/api/agents/live").json()["latest_event"]
    agent_tools.execute_tool("set_automation", '{"switches": ["retry"], "on": true}', agent_id, role="team")
    after = client.get("/api/agents/live").json()["latest_event"]
    assert after and after["type"] == "settings.updated" and after["actor"] == "team"
    assert not before or after["id"] > before["id"]


def test_admin_tools_target_other_agents_by_name(client, base):
    from app.services import agent_tools

    agent_id = int(base.rsplit("/", 1)[1])
    other = client.post("/api/agents", json={"name": "Hairscope desk"}).json()
    names = {t["function"]["name"] for t in agent_tools.get_tools_for_role("admin")}
    assert {"set_agent_automation", "agent_stats", "all_agents_overview"} <= names
    assert "set_agent_automation" not in {t["function"]["name"] for t in agent_tools.get_tools_for_role("team")}

    out = agent_tools.execute_tool("set_agent_automation", '{"agent": "hairscope", "switches": ["auto_dial"], "on": true}', agent_id, role="admin")
    assert "Auto-dialer now on" in out
    assert client.get(f"/api/agents/{other['id']}/automation").json()["settings"]["auto_dial_enabled"] is True
    assert "Failed" in agent_tools.execute_tool("set_agent_automation", '{"agent": "nobody-here", "switches": ["retry"], "on": false}', agent_id, role="admin")
    assert "Today:" in agent_tools.execute_tool("agent_stats", '{"agent": "Hairscope desk"}', agent_id, role="admin")
    assert "Hairscope desk" in agent_tools.execute_tool("all_agents_overview", "{}", agent_id, role="admin")
    assert "Access Denied" in agent_tools.execute_tool("set_agent_automation", '{"agent": "hairscope", "switches": ["retry"], "on": false}', agent_id, role="team")


def test_team_turns_only_pay_the_tool_round_for_commands():
    from app.services.agent import wants_tool

    for said in ("auto dialer band kar do", "speed to lead off karo", "kitni calls hui aaj", "Omkar ko email bhej do",
                 "last call kitne minute chali", "सब agents का status बताओ", "retry calls on karo"):
        assert wants_tool(said), said
    for said in ("ठीक है, बाय", "tum kya kar sakte ho", "haan theek hai", "customer ko kaise handle karte ho", "bye"):
        assert not wants_tool(said), said


def test_colleague_on_two_agents_is_asked_which_one_and_stays_internal(client, base, monkeypatch):
    from app.services import agent, call_session, team_service
    from app.services.call_service import CallService

    a = client.post("/api/agents", json={"name": "Cars team"}).json()
    b = client.post("/api/agents", json={"name": "Homes team"}).json()
    member = {"name": "Priya", "role": "Sales", "phone": "+919812399001"}
    client.put(f"/api/agents/{a['id']}/profile", json={"company_name": "Acme Cars", "team_members": [member]})
    client.put(f"/api/agents/{b['id']}/profile", json={"company_name": "Blue Homes", "team_members": [member]})
    # Some desk also has her number as a lead: she is still a colleague, never a customer.
    client.post(f"/api/agents/{b['id']}/leads", json={"name": "Priya", "phone": "9812399001"})
    monkeypatch.setattr("app.services.call_service.within_calling_hours", lambda cfg, now=None: True)

    assert team_service.agents_for("+919812399001") == [a["id"], b["id"]]
    session = CallService(None).create_inbound("919812399001", "918000000000", "team-2")
    ctx = session["lead"]
    assert ctx["call_purpose"] == "team" and [c["label"] for c in ctx["choices"]] == ["Acme Cars", "Blue Homes"]
    assert session["lead_id"] is None, "a colleague's check-in never becomes a lead"
    assert "Which agent" in agent.greeting(session["agent_id"], ctx, "en-IN")
    assert agent.choose_agent("blue homes wala", ctx["choices"]) == b["id"]
    from app.services.voice_stream import CallStream
    stream = CallStream.__new__(CallStream)
    stream.session, stream.session_id, stream.agent_id = call_session.get(session["id"]), session["id"], session["agent_id"]
    stream.save_session = lambda **k: call_session.save(stream.session)
    switched = stream.switch_agent(b["id"])
    assert switched["call_purpose"] == "team" and "choices" not in switched and stream.session["lead_id"] is None


def test_colleague_on_one_agent_goes_straight_there(client, base):
    from app.services.call_service import CallService
    a = client.post("/api/agents", json={"name": "Only desk"}).json()
    client.put(f"/api/agents/{a['id']}/profile", json={"team_members": [{"name": "Raj", "role": "Sales", "phone": "+919812399002"}]})
    session = CallService(None).create_inbound("919812399002", "918000000000", "team-1")
    assert session["agent_id"] == a["id"] and session["lead"]["call_purpose"] == "team" and "choices" not in session["lead"]


def test_unknown_caller_asking_for_the_other_desk_reaches_that_desk(client, base, monkeypatch):
    from app.services import agents, llm
    from app.services.call_service import CallService
    agents._desks_cache["at"] = 0.0
    a = client.post("/api/agents", json={"name": "Cars A"}).json()
    b = client.post("/api/agents", json={"name": "Hair B"}).json()
    client.put(f"/api/agents/{a['id']}/profile", json={"company_name": "Acme Cars"})
    client.put(f"/api/agents/{b['id']}/profile", json={"company_name": "Hairscope", "team_members": [{"name": "Neha", "role": "Sales", "phone": "+919812399100", "email": "neha@hairscope.test"}]})
    agents._desks_cache["at"] = 0.0
    from app.services import agent
    text = agent._system_prompt(agents.get_profile(a["id"]), {"call_purpose": "inbound"}, [], a["id"])
    assert "Other desks of ours" in text and "Hairscope" in text
    sent = []
    monkeypatch.setattr("app.services.notification_service.send_email", lambda to, subject, body, **k: sent.append((to, subject)) or "sent via test")
    monkeypatch.setattr(llm, "complete", lambda *a, **k: llm.LLMResult(
        '{"summary":"Caller wants Hairscope hair treatment pricing.","qualification":"Unknown","outcome":"other","sentiment":"neutral",'
        '"team_action":"Hairscope team to call back about hair treatment pricing","urgent":false}', "fake", "m", 5))
    lead = client.post(f"/api/agents/{a['id']}/leads", json={"name": "Wrong Desk", "phone": "9812399101"}).json()
    CallService(a["id"])._summarize_inner(0, lead["id"], [{"role": "customer", "text": "Hairscope ke baare mein call kiya"}])
    assert any(to == "neha@hairscope.test" and "[Hairscope]" in subject for to, subject in sent), sent
