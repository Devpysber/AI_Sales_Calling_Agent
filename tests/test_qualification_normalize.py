"""The model's qualification answer used to be matched with exact, case-sensitive string equality
against ("Hot", "Warm", "Cold") in two places (call_service.apply_outcome's post-call summary, and
agent.respond's per-turn live update). Any format drift -- "hot", "Hot.", trailing whitespace --
silently became None with no error anywhere: a genuinely hot lead's qualification was just never
set, with zero visibility that anything went wrong."""

from app.services.agent import _normalize_qualification as agent_normalize
from app.services.call_service import CRMService, CallService
from app.services.call_service import _normalize_qualification as call_normalize


def test_both_normalizers_tolerate_case_whitespace_and_trailing_period():
    for normalize in (agent_normalize, call_normalize):
        assert normalize("Hot") == "Hot"
        assert normalize("hot") == "Hot"
        assert normalize(" Hot. ") == "Hot"
        assert normalize("WARM") == "Warm"
        assert normalize("cold") == "Cold"
        assert normalize(None) is None
        assert normalize("") is None
        assert normalize("Unknown") is None  # deliberately not a recognised qualification
        assert normalize("Hot Lead") is None  # not a real match, don't guess


def test_apply_outcome_sets_qualification_from_a_lowercase_model_answer(client, monkeypatch):
    agent_id = client.post("/api/agents", json={"name": "QualNorm", "profile": {"company_name": "QualNorm"}}).json()["id"]
    lead = CRMService(agent_id).create({"phone": "+919844442001", "name": "Test Lead"}, actor="system")

    from app.services import agent as agent_mod

    def fake_summarize(history):
        return {"summary": "Customer wants a demo", "qualification": "hot.", "outcome": "interested"}
    monkeypatch.setattr(agent_mod, "summarize", fake_summarize)

    svc = CallService(agent_id)
    svc.apply_outcome(None, lead["id"], [{"role": "customer", "text": "hi"}])
    assert CRMService(agent_id).get(lead["id"])["qualification"] == "Hot"
