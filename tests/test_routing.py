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
    assert "passed your request" in call_session.get(session["id"])["history"][-1]["text"]

    client.put(f"/api/agents/{support}/profile", json={"forward_fallback": "message"})
    xml = client.post(f"/api/plivo/transfer-done?sid={session['id']}", data={"DialStatus": "busy"}).text
    assert "<Stream" not in xml and "<Record" not in xml and "<Hangup" in xml  # told the team will ring back, then ends


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
    # One desk on the line: a new caller is helped straight away instead of being asked which desk.
    monkeypatch.setattr("app.services.agents.on_line", lambda number: [agent_id])
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
    a = client.post("/api/agents", json={"name": "Cars A"}).json()
    b = client.post("/api/agents", json={"name": "Hair B"}).json()
    client.put(f"/api/agents/{a['id']}/profile", json={"company_name": "Acme Cars"})
    client.put(f"/api/agents/{b['id']}/profile", json={"company_name": "Hairscope", "team_members": [{"name": "Neha", "role": "Sales", "phone": "+919812399100", "email": "neha@hairscope.test"}]})
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


def test_desks_cache_is_invalidated_on_profile_update(client, base):
    from app.services import agents

    a = client.post("/api/agents", json={"name": "Cache Co A"}).json()
    client.put(f"/api/agents/{a['id']}/profile", json={"company_name": "Old Name"})
    assert any(d["id"] == a["id"] and d["company_name"] == "Old Name" for d in agents.desks())

    client.put(f"/api/agents/{a['id']}/profile", json={"company_name": "New Name"})
    assert any(d["id"] == a["id"] and d["company_name"] == "New Name" for d in agents.desks())


def test_shared_family_phone_is_not_greeted_by_one_desks_name(client, base, monkeypatch):
    from app.services import agent
    from app.services.call_service import CallService
    a = client.post("/api/agents", json={"name": "Desk A"}).json()
    b = client.post("/api/agents", json={"name": "Desk B"}).json()
    client.put(f"/api/agents/{a['id']}/profile", json={"company_name": "Alpha Cars"})
    client.put(f"/api/agents/{b['id']}/profile", json={"company_name": "Beta Hair"})
    client.post(f"/api/agents/{a['id']}/leads", json={"name": "Rahul", "phone": "9812300222"})
    client.post(f"/api/agents/{b['id']}/leads", json={"name": "Priya", "phone": "9812300222"})
    monkeypatch.setattr("app.services.call_service.within_calling_hours", lambda cfg, now=None: True)
    session = CallService(None).create_inbound("919812300222", "918000000000", "fam-1")
    text = agent.greeting(session["agent_id"], session["lead"], "en-IN")
    assert "Rahul" not in text and "Priya" not in text and "Alpha Cars or Beta Hair" in text


def test_mid_call_desk_change_needs_a_cue_and_a_name():
    from app.services import agent
    from app.services.voice_stream import DESK_SWITCH_CUE
    choices = [{"agent_id": 1, "label": "Alpha Cars", "company": "Alpha Cars", "agent_name": "Ashish"},
               {"agent_id": 2, "label": "Beta Hair", "company": "Beta Hair", "agent_name": "Neha"}]
    assert DESK_SWITCH_CUE.search("actually beta hair ke baare mein") and not DESK_SWITCH_CUE.search("haan beta hair se hi liya tha")
    assert agent.choose_agent("actually beta hair ke baare mein", choices, use_llm=False) == 2
    assert agent.choose_agent("the second one please", choices, use_llm=False) is None  # ordinals need the model; not for a mid-call switch


def test_colleague_tools_add_note_dnc_queue_meeting_hours(client, base, monkeypatch):
    from app.services import agent_tools, agents
    agent_id = int(base.rsplit("/", 1)[1])
    run = lambda name, args: agent_tools.execute_tool(name, args, agent_id, role="team")
    assert "Added" in run("add_lead", '{"name": "Sonu Verma", "phone": "98765 43299", "requirement": "Swift under 5 lakh"}')
    assert "Already there" in run("add_lead", '{"phone": "+919876543299"}')
    assert "Noted" in run("add_note", '{"lead": "Sonu", "note": "only call on Sunday"}')
    lead = client.get(f"{base}/leads", params={"search": "Sonu"}).json()["items"][0]
    assert "only call on Sunday" in (lead.get("notes") or "") and lead["requirements"] == "Swift under 5 lakh"
    assert "Updated" in run("update_lead_details", '{"lead": "Sonu", "email": "sonu at gmail dot com", "city": "Bhopal"}')
    lead = client.get(f"{base}/leads/{lead['id']}").json()
    assert lead["email"] == "sonu@gmail.com" and lead["city"] == "Bhopal"
    assert "queued" in run("dial_lead", '{"lead": "Sonu", "now": "false"}').lower()
    assert client.get(f"{base}/leads/{lead['id']}").json()["call_status"] == "Pending"
    from datetime import datetime, timedelta
    when = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d 11:00")
    assert "Meeting" in run("set_meeting", f'{{"lead": "Sonu", "date_time": "{when}"}}')
    assert client.get(f"{base}/leads/{lead['id']}").json()["status"] == "Meeting Booked"
    assert "will not be called" in run("set_do_not_call", '{"lead": "Sonu", "on": "true"}')
    assert client.get(f"{base}/leads/{lead['id']}").json()["do_not_call"] is True
    assert "Failed" in run("dial_lead", '{"lead": "Sonu"}')
    assert "10:00 to 19:00" in run("set_calling_hours", '{"start": "10", "end": "7 baje shaam"}')
    assert "9:00 to 20:00" in run("set_calling_hours", '{"start": 9, "end": 8}')  # a bare closing "8" means evening
    assert "in the call queue" in run("pending_work", "{}")
    assert "paused" in run("set_agent_paused", '{"paused": true}')
    assert agents.get(agent_id)["status"] == "paused"
    run("set_agent_paused", '{"paused": false}')
    assert agents.get(agent_id)["status"] == "active"
    names = {t["function"]["name"] for t in agent_tools.get_tools_for_role("team")}
    assert {"add_lead", "add_note", "dial_lead", "set_meeting", "pending_work", "set_agent_paused"} <= names


def test_designated_agent_is_only_the_fallback_on_a_shared_number(client, base, monkeypatch):
    """'This agent answers' = who takes callers the router cannot place; CRM identity and team lists come first."""
    from app.services import agents
    from app.services.call_service import CallService
    cars = client.post("/api/agents", json={"name": "Cars desk X"}).json()
    hair = client.post("/api/agents", json={"name": "Hair desk X"}).json()
    client.put(f"/api/agents/{cars['id']}/profile", json={"company_name": "CarsIndias X"})
    client.put(f"/api/agents/{hair['id']}/profile", json={"company_name": "Hairscope X",
                                                          "team_members": [{"name": "Neha", "role": "Sales", "phone": "+919812400004"}]})
    agents.set_inbound_owner("918000000000", cars["id"])
    client.post(f"/api/agents/{hair['id']}/leads", json={"name": "Hair customer", "phone": "9812400001"})
    client.post(f"/api/agents/{cars['id']}/leads", json={"name": "Both", "phone": "9812400002"})
    client.post(f"/api/agents/{hair['id']}/leads", json={"name": "Both", "phone": "9812400002"})
    monkeypatch.setattr("app.services.call_service.within_calling_hours", lambda cfg, now=None: True)
    try:
        route = lambda n, uuid: CallService(None).create_inbound(n, "918000000000", uuid)
        s = route("919812400001", "d-1")      # known only to Hairscope -> Hairscope, although CarsIndias is designated
        assert s["agent_id"] == hair["id"] and s["lead"]["call_purpose"] == "inbound"
        s = route("919812400002", "d-2")      # known to both -> designated desk asks which
        assert s["agent_id"] == cars["id"] and s["lead"]["call_purpose"] == "inbound_choose"
        # Unknown on a line several desks share: the designated desk greets and asks which one they want,
        # and nothing is written to a CRM until they say (the lead is created on the desk they choose).
        s = route("919812400003", "d-3")
        assert s["agent_id"] == cars["id"] and s["lead_id"] is None and s["lead"]["call_purpose"] == "inbound_choose"
        assert {c["agent_id"] for c in s["lead"]["choices"]} >= {cars["id"], hair["id"]}
        s = route("919812400004", "d-4")      # Hairscope's colleague -> Hairscope in check-in mode, no lead
        assert s["agent_id"] == hair["id"] and s["lead"]["call_purpose"] == "team" and s["lead_id"] is None
    finally:
        agents.set_inbound_owner("918000000000", None)


def test_inbound_owner_card_follows_the_agents_own_number(client, base):
    from app.services import agents
    own = client.post("/api/agents", json={"name": "Own line", "phone_number": "+918111111111"}).json()
    shared = client.post("/api/agents", json={"name": "Shared line"}).json()
    try:
        mine = client.get(f"/api/agents/{own['id']}/inbound-owner").json()
        assert mine["number"] == "918111111111" and mine["own_number"] and mine["owner_id"] == own["id"]
        assert [a["id"] for a in mine["sharing"]] == [own["id"]]
        theirs = client.get(f"/api/agents/{shared['id']}/inbound-owner").json()
        assert theirs["number"] == "918000000000" and not theirs["own_number"]
        assert own["id"] not in [a["id"] for a in theirs["sharing"]] and shared["id"] in [a["id"] for a in theirs["sharing"]]
    finally:
        agents.delete(own["id"], actor="admin"); agents.delete(shared["id"], actor="admin")


def test_a_new_agent_with_its_own_number_is_wired_on_plivo(client, monkeypatch):
    from app.services import agents, plivo_service
    from app.core import store
    from app.services import heal_service
    wired = []
    class Plivo:
        def __init__(self): pass
        def inbound_status(self, number=None):
            return {"number": "+" + number, "connected": number in wired, "app_name": "old_flow", "app_id": "x", "previous_app": None}
        def connect_inbound(self, number=None):
            wired.append(number); return self.inbound_status(number)
    monkeypatch.setattr(plivo_service, "PlivoService", Plivo)
    made = client.post("/api/agents", json={"name": "Own number wire", "phone_number": "+918122222222"}).json()
    try:
        assert wired == ["918122222222"]
        events = client.get(f"/api/agents/{made['id']}/activity", params={"limit": 5}).json()
        assert any(e["type"] == "inbound.connected" for e in events)
        assert client.get("/api/system/inbound", params={"number": "918122222222"}).json()["connected"]
        # A Plivo failure never loses the agent: it is saved and Health & heal carries the line.
        class Broken(Plivo):
            def inbound_status(self, number=None): raise RuntimeError("404 number not found")
        monkeypatch.setattr(plivo_service, "PlivoService", Broken)
        client.patch(f"/api/agents/{made['id']}", json={"phone_number": "+918133333333"})
        assert agents.get(made["id"])["phone_number"].endswith("8133333333")
        assert any(i["kind"] == "inbound_disconnected" and "8133333333" in i["detail"] for i in heal_service.list_issues())
    finally:
        agents.delete(made["id"], actor="admin")


def test_colleague_switch_commands_run_without_the_model(client, base):
    from app.services import agent_tools, agents
    agent_id = int(base.rsplit("/", 1)[1])
    agents.update_automation(agent_id, {k: True for k in ("auto_dial_enabled", "retry_enabled", "speed_to_lead_enabled", "nurture_enabled")}, actor="test")
    result, state = agent_tools.team_quick_action(agent_id, "हाँ, सारे ऑटोमेशन्स को ऑफ कर दो यार अभी के लिए।")
    assert state == "off" and not result.startswith("Failed")
    cfg = agents.get_automation(agent_id)
    assert not cfg["auto_dial_enabled"] and not cfg["retry_enabled"] and not cfg["speed_to_lead_enabled"] and not cfg["nurture_enabled"]
    result, state = agent_tools.team_quick_action(agent_id, "auto dial chalu karo")
    assert state == "on" and agents.get_automation(agent_id)["auto_dial_enabled"]
    assert agent_tools.team_quick_action(agent_id, "auto dial kyun band hai?") is None       # a question goes to the model
    assert agent_tools.team_quick_action(agent_id, "aaj kitni calls hui") is None           # not a switch command
    assert agent_tools.team_quick_action(agent_id, "retry band karo agar koi issue ho") is None  # conditional


def test_colleague_sets_reminder_hour_calling_days_and_report_email(client, base):
    from app.services import agent_tools, agents
    agent_id = int(base.rsplit("/", 1)[1])
    run = lambda args: agent_tools.execute_tool("set_schedule", args, agent_id, role="team")
    assert "meeting reminders at 10:00" in run('{"job": "meeting_reminder", "time": "10 ए एम"}')
    assert agents.get_automation(agent_id)["meeting_reminder_hour"] == 10
    assert "daily report at 21:00" in run('{"job": "daily_report", "time": "9 pm"}')
    out = run('{"job": "daily_report", "recipient": "ashish sharma one two zero five one two at the rate gmail dot com"}')
    assert "daily report to ashishsharma120512@gmail.com" in out, out
    assert agents.get_automation(agent_id)["daily_report_email"] == "ashishsharma120512@gmail.com"
    agents.update_automation(agent_id, {"calling_days": [0, 1, 2, 3, 4, 5]}, actor="test")
    assert "Sun" in run('{"job": "calling_days", "days": "Sunday ke liye on kar do"}')
    assert agents.get_automation(agent_id)["calling_days"] == [0, 1, 2, 3, 4, 5, 6]
    assert "Sat" not in run('{"job": "calling_days", "days": "Saturday band karo"}')
    assert 5 not in agents.get_automation(agent_id)["calling_days"]
    assert "Failed" in run('{"job": "calling_days", "days": "kal"}')


def test_a_paused_agent_does_not_silence_a_line_it_shares(client):
    """Several agents dial from one number: one of them on hold must not stop the others answering."""
    from app.services import agents
    paused = client.post("/api/agents", json={"name": "Paused desk"}).json()
    live = client.post("/api/agents", json={"name": "Live desk"}).json()
    own = client.post("/api/agents", json={"name": "Own line desk", "phone_number": "+918222222222"}).json()
    agents.set_inbound_owner("918000000000", paused["id"])
    try:
        assert agents.for_inbound("918000000000") == paused["id"]
        client.patch(f"/api/agents/{paused['id']}", json={"status": "paused"})
        answering = agents.for_inbound("918000000000")
        assert answering not in (paused["id"], own["id"])          # not the paused desk, not the desk on another line
        assert answering in agents.on_line("918000000000")
        assert agents.for_inbound("918000000000", lead_agent_id=paused["id"]) == answering  # its own caller too
        assert client.get(f"/api/agents/{live['id']}/inbound-owner").json()["answering_id"] == answering
        # A number only that agent answers on stays its own, paused or not.
        assert agents.on_line("918222222222") == [own["id"]]
    finally:
        client.patch(f"/api/agents/{paused['id']}", json={"status": "active"})
        agents.set_inbound_owner("918000000000", None)


def test_an_agent_with_no_team_of_its_own_transfers_only_to_the_member_who_made_it(client, monkeypatch):
    """A colleague belongs to the workspaces they created, not to every agent in the account."""
    from app.api import plivo as webhooks
    from app.services import agent, agents, team_service

    monkeypatch.setattr(team_service, "members",
                        lambda: [{"id": "maker", "name": "Neha", "phone": "+919812500001", "email": "n@b.c"}])
    mine = agents.create({"name": "Member desk"}, created_by="maker")
    admins = agents.create({"name": "Admin desk"}, created_by="admin")
    persona = client.get(f"/api/agents/{mine['id']}/profile").json()["profile"]
    # The member who made it is its first colleague, so its callers reach a person from the start.
    assert [m["phone"] for m in persona["team_members"]] == ["+919812500001"]
    assert webhooks.transfer_numbers(persona, mine["id"]) == ["919812500001"]
    persona = {**persona, "team_members": [], "transfer_number": ""}   # cleared by hand: the fallback still holds
    assert webhooks.transfer_line(persona, mine["id"]) == "+919812500001"
    assert agent.can_transfer({**persona, "transfer_on_request": True}, mine["id"])
    # The other workspace never dials that colleague, and still refuses to forward with nobody set.
    assert webhooks.transfer_numbers(persona, admins["id"]) == []
    assert not agent.can_transfer({**persona, "transfer_on_request": True}, admins["id"])
    assert client.put(f"/api/agents/{admins['id']}/profile", json={"inbound_mode": "forward"}).status_code == 400
    # Forwarding is allowed on the member's own desk; the page says whose number it reaches.
    assert client.put(f"/api/agents/{mine['id']}/profile", json={"inbound_mode": "forward"}).status_code == 200
    data = client.get(f"/api/agents/{mine['id']}/profile").json()
    assert data["transfer_contacts"][0] == {"phone": "+919812500001", "name": "Neha", "source": "agent"}


def test_a_member_is_a_colleague_only_on_the_workspaces_they_made(client, monkeypatch):
    """Their own desk gets the check-in; someone else's desk treats them as the customer they are."""
    from app.services import agents, team_service
    from app.services.call_service import CallService

    monkeypatch.setattr(team_service, "members",
                        lambda: [{"id": "maker2", "name": "Vikram", "phone": "+919812500003", "email": "v@b.c"}])
    monkeypatch.setattr("app.services.call_service.within_calling_hours", lambda cfg, now=None: True)
    mine = agents.create({"name": "Vikram desk", "phone_number": "+918333333333"}, created_by="maker2")
    other = agents.create({"name": "Someone else desk", "phone_number": "+918444444444"}, created_by="admin")
    assert team_service.agents_for("919812500003") == [mine["id"]]
    assert team_service.is_team_number("919812500003", mine["id"])
    assert not team_service.is_team_number("919812500003", other["id"])
    s = CallService(None).create_inbound("919812500003", "918444444444", "scope-1")
    assert s["agent_id"] == other["id"] and s["lead"]["call_purpose"] == "inbound" and s["lead_id"]
    s = CallService(None).create_inbound("919812500003", "918333333333", "scope-2")
    assert s["agent_id"] == mine["id"] and s["lead"]["call_purpose"] == "team" and s["lead_id"] is None


def test_saving_the_routing_page_does_not_wipe_a_legacy_transfer_number(client):
    """A profile saved with no team-member rows keeps the number unless it is cleared explicitly."""
    agent_id = client.post("/api/agents", json={"name": "Legacy number desk"}).json()["id"]
    url = f"/api/agents/{agent_id}/profile"
    client.put(url, json={"transfer_number": "+91 98125 00002"})
    saved = client.put(url, json={"team_members": [], "notify_missed_calls": False}).json()
    assert saved["transfer_number"] == "+919812500002"
    cleared = client.put(url, json={"team_members": [], "transfer_number": ""}).json()
    assert cleared["transfer_number"] == ""


def test_an_unknown_inbound_caller_is_saved_unnamed_and_still_asked_for_a_name(client, monkeypatch):
    """A placeholder name used to end the "may I have your name" step for every later call."""
    from app.services import agents
    from app.services.call_service import CallService
    from app.services.crm_service import CRMService

    monkeypatch.setattr("app.services.call_service.within_calling_hours", lambda cfg, now=None: True)
    desk = agents.create({"name": "Unknown caller desk", "phone_number": "+918555555555"}, created_by="admin")
    session = CallService(None).create_inbound("919812500009", "918555555555", "unknown-1")
    assert session["agent_id"] == desk["id"] and session["lead_id"]
    assert session["lead"]["call_purpose"] == "inbound"
    assert "A new caller not yet in our CRM" in session["lead"]["call_goal"]   # the name is still to be asked

    crm = CRMService(desk["id"])
    for junk in ("Unknown", "unknown caller", "N/A", "+919812500009"):
        crm.update(session["lead_id"], {"name": junk}, actor="ai")
        assert crm.get(session["lead_id"])["name"] is None, junk
    crm.update(session["lead_id"], {"name": "  Ashish Sharma "}, actor="ai")
    assert crm.get(session["lead_id"])["name"] == "Ashish Sharma"
    # Named now: the next call greets them by name instead of treating them as a stranger again.
    again = CallService(None).create_inbound("919812500009", "918555555555", "unknown-2")
    assert again["lead"]["name"] == "Ashish Sharma" and again["lead_id"] == session["lead_id"]


def test_a_call_is_answered_by_the_agent_whose_number_was_dialled(client, monkeypatch):
    """A customer another desk knows, or a designation left behind, must not pull the call off this line."""
    from app.services import agents
    from app.services.call_service import CallService

    monkeypatch.setattr("app.services.call_service.within_calling_hours", lambda cfg, now=None: True)
    mine = agents.create({"name": "Own number desk", "phone_number": "+918666666666"}, created_by="admin")
    theirs = agents.create({"name": "Other desk", "phone_number": "+918777777777"}, created_by="admin")
    client.post(f"/api/agents/{theirs['id']}/leads", json={"name": "Their customer", "phone": "9812500011"})

    assert agents.for_inbound("918666666666", lead_agent_id=theirs["id"]) == mine["id"]
    session = CallService(None).create_inbound("919812500011", "918666666666", "line-1")
    assert session["agent_id"] == mine["id"]

    # A designation made before the agent had a line of its own no longer captures that number.
    agents.set_inbound_owner("918777777777", mine["id"])
    try:
        assert agents.for_inbound("918777777777") == theirs["id"]
    finally:
        agents.set_inbound_owner("918777777777", None)


def test_an_agent_number_saved_without_its_country_code_still_answers(client):
    """"9584516352" saved on the agent and "+919584516352" dialled by Plivo are the same line."""
    from app.services import agents

    desk = agents.create({"name": "National form desk", "phone_number": "080 1234 5679"}, created_by="admin")
    assert desk["phone_number"] == "+918012345679"
    assert agents.on_line("+918012345679") == [desk["id"]]
    assert agents.for_inbound("918012345679") == desk["id"]


def test_a_new_caller_on_a_shared_line_is_asked_which_desk_and_becomes_that_desks_lead(client, monkeypatch):
    """One number, many agents: the caller picks, and only then is a lead written — on the desk they picked."""
    from app.services import agents, call_session
    from app.services.call_service import CallService
    from app.services.crm_service import CRMService
    from app.services.voice_stream import CallStream

    monkeypatch.setattr("app.services.call_service.within_calling_hours", lambda cfg, now=None: True)
    cars = agents.create({"name": "Shared cars"}, created_by="admin")
    homes = agents.create({"name": "Shared homes"}, created_by="admin")
    client.put(f"/api/agents/{cars['id']}/profile", json={"company_name": "Shared Cars"})
    client.put(f"/api/agents/{homes['id']}/profile", json={"company_name": "Shared Homes"})
    agents.set_inbound_owner("918000000000", cars["id"])
    try:
        session = CallService(None).create_inbound("919812500021", "918000000000", "choose-1")
        assert session["lead"]["call_purpose"] == "inbound_choose" and session["lead_id"] is None
        assert not CRMService(cars["id"]).find_by_phone("919812500021"), "nothing is saved before they choose"

        stream = CallStream.__new__(CallStream)
        stream.session = call_session.get(session["id"])
        stream.session_id = session["id"]
        stream.agent_id = session["agent_id"]
        stream.persona = agents.get_profile(session["agent_id"])
        stream.usage = {}
        context = stream.switch_agent(homes["id"])
        assert context["call_purpose"] == "inbound"
        lead = CRMService(homes["id"]).find_by_phone("919812500021")
        assert lead and lead["source"] == "inbound call"
        assert not CRMService(cars["id"]).find_by_phone("919812500021"), "the desk that only greeted keeps no lead"
    finally:
        agents.set_inbound_owner("918000000000", None)


def test_the_brief_for_a_new_caller_does_not_claim_we_know_them():
    from app.services import agent as agent_service

    choices = [{"agent_id": 1, "label": "Shared Cars", "company": "Shared Cars", "agent_name": "Ashish", "about": ""},
               {"agent_id": 2, "label": "Shared Homes", "company": "Shared Homes", "agent_name": "Omkar", "about": ""}]
    new = agent_service.call_goal({"choices": choices, "new_caller": True}, "inbound_choose")
    assert "new to us" in new and "known to more than one" not in new
    known = agent_service.call_goal({"choices": choices}, "inbound_choose")
    assert "known to more than one of our desks" in known
