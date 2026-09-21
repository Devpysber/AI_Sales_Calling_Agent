"""Pure logic behind live calls: language requests, callback times, reply filtering, spoken dates."""

from datetime import datetime, timedelta

from app.services.agent import call_goal, spoken_datetime
from app.services.call_service import IST, _valid_callback
from app.services.voice_stream import ReplyFilter, requested_language


def test_requested_language_last_mention_wins():
    assert requested_language("ગુજરાતી માં બોલો ના હિન્દી માં કહો") == "hi-IN"
    assert requested_language("please speak in English") == "en-IN"
    assert requested_language("हिंदी में बात करो") == "hi-IN"
    assert requested_language("yes confirm") is None


def test_valid_callback_normalises_and_bounds():
    soon = (datetime.now(IST) + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M")
    assert _valid_callback(soon + " (24h IST)") == soon
    assert _valid_callback("") is None
    assert _valid_callback("tomorrow") is None
    assert _valid_callback("2001-01-01 10:00") is None  # in the past
    far = (datetime.now(IST) + timedelta(days=90)).strftime("%Y-%m-%d %H:%M")
    assert _valid_callback(far) is None


def test_reply_filter_tool_call_does_not_end_call():
    f = ReplyFilter()
    spoken = f.feed("<tool_call>crm_update\n<arg_key>end_call</arg_key><arg_value>true</arg_value>") + f.flush()
    assert spoken.strip() == ""
    assert f.end_call is False


def test_reply_filter_end_mark():
    f = ReplyFilter()
    spoken = f.feed("धन्यवाद, आपका दिन शुभ हो। <END>") + f.flush()
    assert "धन्यवाद" in spoken and f.end_call is True


def test_spoken_datetime():
    assert spoken_datetime("2026-09-16 11:00", hindi=True) == "16 September, सुबह 11 बजे"
    assert spoken_datetime("2026-09-16 17:30", hindi=False) == "16 September at 5:30 PM"
    assert spoken_datetime("next week", hindi=True) == "next week"


def test_call_goal():
    assert "2026-09-16 11:00" in call_goal({"meeting_at": "2026-09-16 11:00"}, "confirm_meeting")
    assert call_goal({}, "confirm_meeting") is None
    assert call_goal({}, None) is None


def test_ai_stage_moves_forward_only():
    from app.services.call_service import _ai_may_move
    assert _ai_may_move("New", "Interested")
    assert _ai_may_move("Follow Up", "Meeting Booked")
    assert not _ai_may_move("Meeting Booked", "Contacted")
    assert _ai_may_move("Interested", "Not Interested")
    assert not _ai_may_move("Closed Won", "Not Interested")
    assert not _ai_may_move("Not Interested", "Contacted")
    assert _ai_may_move(None, "Contacted")


def test_usage_cost_estimate(monkeypatch):
    from types import SimpleNamespace

    from app.core.config import settings
    from app.services.analytics import usage
    from app.services.call_service import _usage_fields

    assert _usage_fields({"tts_chars": 120.0, "stt_seconds": 33.333, "llm_requests": 4}) == {
        "tts_chars": 120, "stt_seconds": 33.3, "llm_requests": 4, "llm_input_tokens": None, "llm_output_tokens": None}
    assert _usage_fields(None) == {}
    monkeypatch.setattr("app.services.settings_service.SettingsService.get_state", lambda self, key: {})
    monkeypatch.setattr(settings, "cost_per_call_minute", 1.0)
    monkeypatch.setattr(settings, "cost_per_10k_tts_chars", 10.0)
    monkeypatch.setattr(settings, "cost_per_stt_hour", 30.0)
    monkeypatch.setattr(settings, "cost_per_llm_request", 0.01)
    rows = [SimpleNamespace(status="Completed", duration=120, tts_chars=10_000, stt_seconds=1800, llm_requests=10),
            SimpleNamespace(status="No Answer", duration=0, tts_chars=0, stt_seconds=0, llm_requests=0)]
    u = usage(rows)
    assert u["cost"] == {"telephony": 2.0, "tts": 10.0, "stt": 15.0, "llm": 0.1}
    assert u["total_cost"] == 27.1 and u["cost_per_connected_call"] == 27.1 and u["rates_configured"]


def test_spoken_name_and_collect_goal():
    from app.services.agent import call_goal
    from app.services.voice_stream import spoken_name

    assert spoken_name("Hi, my name is Neha Gupta") == "Neha Gupta"
    assert spoken_name("मेरा नाम नेहा है") == "नेहा"
    assert spoken_name("मैं राहुल बोल रहा हूँ") == "राहुल"
    assert spoken_name("I am interested") is None
    goal = call_goal({"collect": ["name", "city"]}, "inbound_new")
    assert "their name, their city" in goal
    assert "complete" in call_goal({"collect": ["name"], "name": "Neha"}, "inbound_new")


def test_spoken_email():
    from app.services.voice_stream import spoken_email
    assert spoken_email("my email is neha at the rate gmail dot com") == "neha@gmail.com"
    assert spoken_email("mail neha.g@yahoo.co.in") == "neha.g@yahoo.co.in"
    assert spoken_email("I will be at home") is None


def test_usage_bills_llm_by_tokens_when_measured(monkeypatch):
    from types import SimpleNamespace

    from app.core.config import settings
    from app.services import llm
    from app.services.analytics import usage
    from app.services.call_service import _usage_fields

    # Token counts ride on the usage dict into the call record
    fields = _usage_fields({"tts_chars": 800, "stt_seconds": 180, "llm_requests": 10, "llm_input_tokens": 70_000, "llm_output_tokens": 600})
    assert fields["llm_input_tokens"] == 70_000 and fields["llm_output_tokens"] == 600
    # Provider usage frame wins; otherwise a character estimate
    assert llm.turn_usage([{"content": "x" * 360}], None, 36, {"prompt_tokens": 95, "completion_tokens": 12}) == {"input_tokens": 95, "output_tokens": 12, "estimated": False}
    est = llm.turn_usage([{"content": "x" * 360}], None, 36, None)
    assert est["estimated"] and est["input_tokens"] == 100 and est["output_tokens"] == 10

    monkeypatch.setattr("app.services.settings_service.SettingsService.get_state", lambda self, key: {})
    monkeypatch.setattr(settings, "cost_per_call_minute", 0.38)
    monkeypatch.setattr(settings, "cost_per_10k_tts_chars", 30.0)
    monkeypatch.setattr(settings, "cost_per_stt_hour", 30.0)
    monkeypatch.setattr(settings, "cost_per_llm_request", 0.209352)
    monkeypatch.setattr(settings, "cost_per_1m_llm_input", 29.28)
    monkeypatch.setattr(settings, "cost_per_1m_llm_output", 73.20)
    measured = SimpleNamespace(status="Completed", duration=180, tts_chars=800, stt_seconds=180, llm_requests=10,
                               llm_input_tokens=70_000, llm_output_tokens=600, qualification=None, outcome=None)
    legacy = SimpleNamespace(status="Completed", duration=180, tts_chars=800, stt_seconds=180, llm_requests=10,
                             llm_input_tokens=None, llm_output_tokens=None, qualification=None, outcome=None)
    u = usage([measured, legacy])
    assert u["llm_billing"] == "tokens" and u["token_calls"] == 1
    # measured call by tokens (2.0496 + 0.0439) + legacy call by flat rate (2.09352)
    assert u["cost"]["llm"] == round(70_000 / 1e6 * 29.28 + 600 / 1e6 * 73.20 + 10 * 0.209352, 2)
    assert u["per_call"]["llm_requests"] == 10 and u["per_call"]["llm_input_tokens"] == 70_000
    assert u["per_call"]["llm_input_tokens_per_request"] == 7_000
    assert u["per_call"]["total_cost"] == u["cost_per_connected_call"]
