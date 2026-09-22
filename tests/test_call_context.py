"""The live call keeps its own context (what to ask a new caller) when the lead is refreshed from the CRM."""

from app.services import agent
from app.services.voice_stream import merge_call_context


def _inbound_lead(**fields):
    lead = {"phone": "+919000000001", "collect": ["name", "requirement", "city"], "call_purpose": "inbound", **fields}
    lead["call_goal"] = agent.call_goal(lead, "inbound_new")
    return lead


def test_crm_refresh_keeps_the_new_caller_questions():
    session_lead = _inbound_lead()
    merged = merge_call_context(session_lead, {"id": 7, "phone": "+919000000001", "name": "", "status": "New"})
    assert merged["id"] == 7                       # fresh CRM fields are used
    assert "their name" in merged["call_goal"]     # and the call's instruction survives
    assert "their city" in merged["call_goal"]


def test_details_already_saved_drop_off_the_ask_list():
    merged = merge_call_context(_inbound_lead(), {"id": 7, "name": "Ashish", "city": "Indore"})
    assert "their name" not in merged["call_goal"]
    assert "their city" not in merged["call_goal"]
    assert "what they are looking for" in merged["call_goal"]


def test_no_crm_row_leaves_the_session_lead_untouched():
    session_lead = _inbound_lead()
    assert merge_call_context(session_lead, None) is session_lead


def test_outbound_goal_is_not_rewritten():
    merged = merge_call_context({"call_goal": "Confirm the booked meeting.", "call_purpose": "confirm_meeting"},
                                {"id": 3, "name": "Neha"})
    assert merged["call_goal"] == "Confirm the booked meeting."


def test_transfer_label_names_the_colleague(monkeypatch):
    from app.services import call_service, team_service

    monkeypatch.setattr(team_service, "members",
                        lambda: [{"name": "Ashish Sharma", "phone": "+919584516352", "email": "a@b.c"}])
    calls = call_service.CallService(None)
    assert calls.transfer_label("+919584516352") == "Ashish Sharma (+919584516352)"
    assert calls.transfer_label("+919000000000") == "+919000000000"   # unknown number stays a number
    assert calls.transfer_label("") == "your team"


def test_ai_cannot_change_the_number_we_dial(client, base):
    """A number a caller says must never become the number we call back on."""
    from app.services.crm_service import CRMService

    crm = CRMService(int(base.rsplit("/", 1)[1]))
    lead = crm.create({"name": "Caller", "phone": "+917879417266"}, actor="system")

    after_ai = crm.update(lead["id"], {"phone": "+919000000123", "summary": "Asked for a callback"}, actor="ai")
    assert after_ai["phone"] == "+917879417266"          # dial target untouched
    assert after_ai["summary"] == "Asked for a callback"  # the rest of the AI update still applies

    after_admin = crm.update(lead["id"], {"phone": "+919000000123"}, actor="admin")
    assert after_admin["phone"] == "+919000000123"       # a person can still correct it


def test_team_member_agent_limit(client, monkeypatch):
    """A team member may create the number of agents the admin allowed, and no more."""
    from app.services import agents as agent_service
    from app.services import team_service

    member = {"id": "member-1", "name": "Ashish", "email": "a@b.c", "phone": "+919584516352", "max_agents": 2}
    monkeypatch.setattr(team_service, "members", lambda: [member])

    assert team_service.agent_limit(member) == 2
    assert team_service.agent_limit({"id": "x"}) == 1          # default when unset
    assert team_service.agent_limit({"max_agents": "oops"}) == 1  # bad value never grants more

    before = agent_service.created_count("member-1")
    agent_service.create({"name": "Limit test"}, actor="team", created_by="member-1")
    assert agent_service.created_count("member-1") == before + 1
    assert agent_service.created_count("someone-else") == 0    # counted per member


def test_no_followup_email_for_a_time_the_lead_never_agreed_to(client, base, monkeypatch):
    """A callback time we invented ourselves (fallback/unclear-time slot) must not be emailed to the
    lead as "we have scheduled a follow-up call with you" — they never agreed to it."""
    import threading
    from app.services.crm_service import CRMService

    started = []
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: started.append(1) or type(
        "T", (), {"start": lambda self: None})())

    crm = CRMService(int(base.rsplit("/", 1)[1]))
    lead = crm.create({"name": "Caller", "phone": "+917879417266", "email": "caller@example.com"}, actor="system")

    crm.update(lead["id"], {"callback_at": "2030-01-01 10:00", "_no_followup_email": True}, actor="ai")
    assert started == []                                # synthetic slot: no email thread started

    crm.update(lead["id"], {"callback_at": "2030-01-01 11:00"}, actor="ai")
    assert started == [1]                                # a customer-agreed time still gets the email
