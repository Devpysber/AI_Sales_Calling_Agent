"""job_meeting_reminder skips leads marked do_not_call even if they have a meeting_at."""

import pytest


@pytest.fixture(scope="module")
def agent(client):
    return client.post("/api/agents", json={"name": "Reminder desk"}).json()["id"]


def test_job_meeting_reminder_skips_do_not_call_lead(agent, monkeypatch):
    from app.services import scheduler
    from app.services.crm_service import CRMService

    leads = [
        {"id": 1, "email": "dnc@example.com", "name": "DNC Lead", "meeting_at": "2026-09-23 10:00", "do_not_call": True},
        {"id": 2, "email": "ok@example.com", "name": "OK Lead", "meeting_at": "2026-09-23 11:00", "do_not_call": False},
    ]
    monkeypatch.setattr(CRMService, "meetings_on", lambda self, date_str: leads)

    sent_to = []
    monkeypatch.setattr(scheduler, "send_email", lambda to, subj, body, **kw: sent_to.append(to) or "sent")
    monkeypatch.setattr(scheduler, "email_sent", lambda status: status == "sent")

    result = scheduler.job_meeting_reminder(agent, {})

    assert sent_to == ["ok@example.com"]
    assert result.startswith("1 reminder(s) sent")
