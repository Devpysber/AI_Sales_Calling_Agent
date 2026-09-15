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
