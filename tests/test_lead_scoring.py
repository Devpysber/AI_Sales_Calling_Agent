"""Lead.lead_score_effective / lead_score_sentiment_trend and their shared helper."""

from datetime import datetime, timedelta

from app.models.lead import Lead, effective_lead_score


def test_effective_lead_score_decays_with_staleness():
    assert effective_lead_score(None, None) is None
    assert effective_lead_score(80, None) == 80  # never contacted: no decay
    now = datetime.utcnow()
    assert effective_lead_score(80, now) == 80  # contacted today
    assert effective_lead_score(80, now - timedelta(days=30)) == 70  # 30 // 3 = 10 lost
    assert effective_lead_score(20, now - timedelta(days=300)) == 0  # decay floors at 0, not negative


def test_lead_to_dict_uses_the_same_helper_and_exposes_sentiment_trend():
    lead = Lead(agent_id=1, phone="+919800040001", qualification="Hot", lead_score=95)
    d = lead.to_dict()
    assert d["lead_score"] == 95
    assert d["lead_score_effective"] == 95  # never contacted
    assert d["lead_score_sentiment_trend"] == 5  # (95-85)/2 = 5, within the +-5 clamp

    cold_but_boosted = Lead(agent_id=1, phone="+919800040002", qualification="Cold", lead_score=20)
    assert cold_but_boosted.to_dict()["lead_score_sentiment_trend"] == 0  # base case, no trend

    unqualified = Lead(agent_id=1, phone="+919800040003", lead_score=50)
    assert unqualified.to_dict()["lead_score_sentiment_trend"] == 0  # no baseline to compare against
