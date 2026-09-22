"""run_job reports the crashing job itself; heal() reflects a still-failing job as unhealed."""

import pytest


@pytest.fixture(scope="module")
def agent(client):
    return client.post("/api/agents", json={"name": "Heal desk"}).json()["id"]


def test_run_job_reports_the_crashing_job(agent, monkeypatch):
    from app.services import scheduler, heal_service

    def boom(agent_id, cfg, force=False):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(scheduler.JOBS, "auto_dial", boom)
    reported = {}
    monkeypatch.setattr(heal_service, "report",
                         lambda kind, detail, **kw: reported.update(kind=kind, **kw))

    result = scheduler.run_job(agent, "auto_dial")

    assert result.startswith("error:")
    assert reported["kind"] == "scheduler_error"
    assert reported["data"]["job"] == "auto_dial"
    assert reported["data"]["agent_id"] == agent


def test_heal_scheduler_error_reports_false_when_job_still_fails(agent, monkeypatch):
    from app.services import scheduler, heal_service

    monkeypatch.setattr(scheduler, "run_job", lambda *a, **k: "error: still broken")

    ok, msg = heal_service._heal_one({"kind": "scheduler_error", "agent_id": agent,
                                       "data": {"agent_id": agent, "job": "auto_dial"}})

    assert ok is False
    assert msg.startswith("error:")
