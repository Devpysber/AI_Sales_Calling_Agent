import os
import tempfile
from datetime import datetime, timedelta

import pytest

_tmp = tempfile.mkdtemp()
os.environ.update({
    "DATABASE_URL": f"sqlite:///{_tmp}/test.db",
    "REDIS_URL": "",
    "ADMIN_USERNAME": "admin",
    "ADMIN_PASSWORD": "test-pass",
    "SECRET_KEY": "test-secret",
    "PLIVO_VALIDATE_SIGNATURE": "false",
    "VOICE_MODE": "gather",
    "STT_SILENCE_GATE": "false",
    "PLIVO_AUTH_ID": "MATEST",
    "PLIVO_AUTH_TOKEN": "token",
    "PLIVO_PHONE_NUMBER": "+918000000000",
    "PUBLIC_BASE_URL": "https://agent.test",
    "RUN_SCHEDULER": "false",
    "OPENROUTER_API_KEY": "",
    "SARVAM_API_KEY": "test",
    "EXCEL_FILE": f"{_tmp}/none.xlsx",
})

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        res = c.post("/api/auth/login", json={"username": "admin", "password": "test-pass"})
        assert res.status_code == 200
        yield c


@pytest.fixture(scope="session")
def base(client):
    """API prefix of a first agent workspace."""
    agent = client.post("/api/agents", json={"name": "Sales A", "profile": {"company_name": "Acme"}}).json()
    return f"/api/agents/{agent['id']}"


# A booked time must still be in the future when the summary is applied: tomorrow, fixed hour.
MEETING_AT = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d 15:00")


@pytest.fixture(autouse=True)
def fake_ai(monkeypatch):
    """Deterministic LLM, TTS and Plivo."""
    from app.services import llm, tts

    def complete(messages, json_mode=False, max_tokens=500, temperature=0.3, **_):
        user = messages[-1]["content"]
        if messages[0]["content"].startswith("You are a CRM analyst"):
            text = ('{"summary":"Prospect wants a demo.","qualification":"Hot","outcome":"meeting_booked",'
                    '"sentiment":"positive","status":"Meeting Booked","meeting_at":"' + MEETING_AT + '"}')
        else:
            grounded = "Growth plan" in messages[0]["content"]
            text = ('{"reply":"%s","language":"en-IN","intent":"pricing","qualification":"Warm","sentiment":"positive",'
                    '"end_call":%s,"crm_update":{"requirements":"sales automation"}}'
                    % ("Our Growth plan fits you." if grounded else "Let me check that.", "true" if "bye" in user else "false"))
        return llm.LLMResult(text, "fake", "fake-model", 5)

    monkeypatch.setattr(llm, "complete", complete)

    def stream(messages, max_tokens=160, temperature=0.4):
        user = messages[-1]["content"]
        reply = "Our Growth plan fits you. Shall we book a demo?" + (" <END>" if "bye" in user else "")
        for i in range(0, len(reply), 7):
            yield reply[i:i + 7]

    monkeypatch.setattr(llm, "stream", stream)
    monkeypatch.setattr(llm, "embed", lambda texts, timeout=30: None)
    monkeypatch.setattr(tts, "synthesize", lambda text, language=None, speaker=None: b"RIFF-fake-wav")
    monkeypatch.setattr(tts, "synthesize_pcm", lambda text, language=None, speaker=None: bytes([0, 16]) * 800)

    class FakePlivo:
        def __init__(self):
            pass

        @staticmethod
        def caller_id():
            return "918000000000"

        def dial(self, phone, session_id, call_id, max_minutes, detect_voicemail=False, from_number=None):
            return f"req-{call_id}"

        def hangup(self, uuid):
            pass

    import app.services.plivo_service as ps
    monkeypatch.setattr(ps, "PlivoService", FakePlivo)

    import app.services.call_service as cs
    monkeypatch.setattr(cs, "public_url_reachable", lambda: True)
