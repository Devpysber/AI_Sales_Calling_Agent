"""Tests for the per-rep performance leaderboard (app.services.analytics.rep_leaderboard)."""

import uuid
from datetime import datetime

import pytest

from app.core.database import get_db
from app.models.call import Call
from app.models.lead import Lead
from app.models.user import Organization, User
from app.services import analytics


@pytest.fixture(autouse=True)
def _migrated(client):
    """The client fixture boots the app, which runs Alembic on the test database."""


def _u() -> str:
    return uuid.uuid4().hex[:12]


def test_empty_agent_returns_no_reps():
    agent_id = 92000 + hash(_u()) % 1000
    rep = analytics.rep_leaderboard(agent_id, days=30)
    assert rep == {"reps": []}


def test_rep_with_leads_and_calls_is_scored_and_ranked():
    agent_id = 93000 + hash(_u()) % 1000
    now = datetime.utcnow()

    with get_db() as db:
        org = Organization(name="t")
        db.add(org)
        db.flush()
        rep_a = User(organization_id=org.id, email=f"a-{_u()}@x.com", name="Rep A", role="agent")
        rep_b = User(organization_id=org.id, email=f"b-{_u()}@x.com", name="Rep B", role="agent")
        db.add_all([rep_a, rep_b])
        db.flush()

        lead_a1 = Lead(agent_id=agent_id, phone="910000000001", owner_user_id=rep_a.id, qualification="Hot", lead_score=90)
        lead_a2 = Lead(agent_id=agent_id, phone="910000000002", owner_user_id=rep_a.id, qualification="Warm", lead_score=50)
        lead_b1 = Lead(agent_id=agent_id, phone="910000000003", owner_user_id=rep_b.id, qualification="Cold", lead_score=20)
        db.add_all([lead_a1, lead_a2, lead_b1])
        db.flush()

        db.add_all([
            Call(agent_id=agent_id, lead_id=lead_a1.id, session_id=_u(), status="Completed", duration=120,
                 outcome="meeting_booked", created_at=now),
            Call(agent_id=agent_id, lead_id=lead_a2.id, session_id=_u(), status="No Answer", duration=0,
                 created_at=now),
            Call(agent_id=agent_id, lead_id=lead_b1.id, session_id=_u(), status="Completed", duration=30,
                 outcome="not_interested", created_at=now),
        ])
        rep_a_id, rep_b_id = rep_a.id, rep_b.id

    out = analytics.rep_leaderboard(agent_id, days=30)
    reps = {r["user_id"]: r for r in out["reps"]}

    assert reps[rep_a_id]["name"] == "Rep A"
    assert reps[rep_a_id]["leads_owned"] == 2
    assert reps[rep_a_id]["calls"] == 2
    assert reps[rep_a_id]["connected"] == 1
    assert reps[rep_a_id]["meetings"] == 1
    assert reps[rep_a_id]["hot_leads"] == 1
    assert reps[rep_a_id]["avg_lead_score"] == 70.0

    assert reps[rep_b_id]["leads_owned"] == 1
    assert reps[rep_b_id]["calls"] == 1
    assert reps[rep_b_id]["meetings"] == 0

    # Rep A booked a meeting, so ranks first.
    assert out["reps"][0]["user_id"] == rep_a_id


def test_rep_with_no_activity_in_window_is_excluded():
    agent_id = 94000 + hash(_u()) % 1000
    with get_db() as db:
        org = Organization(name="t")
        db.add(org)
        db.flush()
        rep = User(organization_id=org.id, email=f"c-{_u()}@x.com", name="Rep C", role="agent")
        db.add(rep)
        db.flush()
        # A lead that belongs to a different agent entirely must not leak into this agent's leaderboard.
        db.add(Lead(agent_id=agent_id + 1, phone="910000000099", owner_user_id=rep.id, qualification="Hot"))

    out = analytics.rep_leaderboard(agent_id, days=30)
    assert out == {"reps": []}
