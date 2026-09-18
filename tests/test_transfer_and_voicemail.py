"""Forwarding a call to a person, and what happens when nobody is reachable."""

from app.api.plivo import dial_human, int_or_none, inbound_route, transfer_targets
from plivo import plivoxml


PERSONA = {"transfer_number": "919584516352, 918989763858"}


def test_the_line_someone_is_calling_from_is_never_rung_back():
    session = {"lead": {"phone": "+91 89897 63858"}}
    assert transfer_targets(PERSONA, session) == ["919584516352"]
    assert transfer_targets(PERSONA) == ["919584516352", "918989763858"]


def test_transfer_done_index_matches_the_list_that_was_dialled():
    """The idx in the action URL must point at the next number of the same filtered list."""
    session = {"lead": {"phone": "+919584516352"}, "call_id": 7, "id": "s-1"}
    r = plivoxml.ResponseElement()
    assert dial_human(r, PERSONA, "918000000000", session) is True
    assert "918989763858" in r.to_string()
    assert "idx=1" in r.to_string()
    # idx=1 is past the end of the one-number list: nothing left to ring.
    assert dial_human(plivoxml.ResponseElement(), PERSONA, "918000000000", session, idx=1) is False


def test_a_colleague_calling_their_own_agent_gets_the_ai(monkeypatch):
    """Forwarding would ring the busy line they are speaking on."""
    from app.api import plivo

    monkeypatch.setattr(plivo.agents, "get_automation", lambda _id: {})
    persona = {"transfer_number": "919584516352", "inbound_mode": "forward"}
    assert inbound_route(persona, 1, "+919584516352") == "ai"
    assert inbound_route({"transfer_number": "", "inbound_mode": "forward"}, 1, "+917879417266") == "ai"


def test_int_or_none_survives_missing_and_literal_none_params():
    assert int_or_none("12") == 12
    assert int_or_none(None) is None
    assert int_or_none("None") is None
    assert int_or_none("") is None


def test_voicemail_is_stored_and_ends_the_call(client, base, monkeypatch):
    from app.api import plivo

    stored = {}
    monkeypatch.setattr(plivo, "_attach_voicemail", lambda cid, url: stored.update(cid=cid, url=url))
    res = client.post("/api/plivo/voicemail?cid=5", data={"RecordUrl": "https://rec/1.mp3"})
    assert res.status_code == 200
    assert stored == {"cid": 5, "url": "https://rec/1.mp3"}
    assert "<Hangup" in res.text


def test_live_calls_feed_is_cheap_and_shaped_for_the_banner(client, base):
    res = client.get("/api/agents/live")
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body["live_calls"], list)
    for call in body["live_calls"]:
        assert call["status"] in ("Queued", "Ringing", "In Progress")
        assert "agent_name" in call
