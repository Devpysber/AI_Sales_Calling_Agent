"""AI nurture: LLM decides per stale lead whether/how to re-engage (call, WhatsApp, email, skip)."""

from app.services import ai_nurture, llm
from app.services.crm_service import CRMService


def test_decide_skips_on_llm_error(monkeypatch, base):
    agent_id = int(base.rsplit("/", 1)[1])

    def boom(*a, **k):
        raise llm.LLMError("provider down")

    monkeypatch.setattr(llm, "complete", boom)
    lead = {"id": 1, "name": "Ravi", "status": "Interested"}
    result = ai_nurture.decide(agent_id, lead)
    assert result["action"] == "skip"
    assert "provider down" in result["reason"]


def test_run_respects_max_attempts_without_deciding(monkeypatch, base):
    agent_id = int(base.rsplit("/", 1)[1])
    lead = {"id": 101, "name": "Capped", "do_not_call": False}
    monkeypatch.setattr(CRMService, "nurture_candidates", lambda self, after_days, limit: [lead])

    from app.services.settings_service import SettingsService
    SettingsService().set_state(f"ai_nurture.{lead['id']}", 2)  # already at nurture_max_attempts

    called = []
    monkeypatch.setattr(ai_nurture, "decide", lambda aid, ld: called.append(ld) or {"action": "skip", "message": "", "reason": ""})

    cfg = {"nurture_after_days": 3, "nurture_max_attempts": 2, "ai_nurture_max_per_run": 10}
    result = ai_nurture.run(agent_id, cfg)
    assert called == []
    assert result == "no leads to review"


def test_run_skips_do_not_call_leads(monkeypatch, base):
    agent_id = int(base.rsplit("/", 1)[1])
    lead = {"id": 102, "name": "Blocked", "do_not_call": True}
    monkeypatch.setattr(CRMService, "nurture_candidates", lambda self, after_days, limit: [lead])

    called = []
    monkeypatch.setattr(ai_nurture, "decide", lambda aid, ld: called.append(ld) or {"action": "skip", "message": "", "reason": ""})

    cfg = {"nurture_after_days": 3, "nurture_max_attempts": 2, "ai_nurture_max_per_run": 10}
    result = ai_nurture.run(agent_id, cfg)
    assert called == []
    assert result == "no leads to review"
