"""LIVE_PROMPT_CHAR_BUDGET: a cost lever that must never reach a colleague's call or the playground."""

from app.core.config import settings
from app.services import agent

LEAD = {"name": "Omkar", "phone": "+919082610087", "language": "hi-IN", "call_purpose": "outbound"}
COLLEAGUE = {"name": "Ashish", "phone": "+919812300111", "call_purpose": "team"}


def _size(agent_id, lead):
    messages, _ = agent.build_messages(agent_id, [], "kitna kharcha hota hai", lead, use_embeddings=False, top_k=3)
    return sum(len(m["content"]) for m in messages)


def test_the_budget_is_off_by_default(base, monkeypatch):
    agent_id = int(base.rsplit("/", 1)[-1])
    assert settings.live_prompt_char_budget == 0
    full = _size(agent_id, LEAD)
    assert full > 10_000      # the shipped prompt is the whole thing


def test_setting_a_budget_shrinks_a_customer_turn(base, monkeypatch):
    agent_id = int(base.rsplit("/", 1)[-1])
    full = _size(agent_id, LEAD)
    monkeypatch.setattr(settings, "live_prompt_char_budget", 12_000, raising=False)
    assert _size(agent_id, LEAD) < full * 0.75


def test_a_colleagues_call_keeps_the_full_prompt(base, monkeypatch):
    """A team check-in runs the agent's own instructions; trimming them would rehearse a different agent."""
    agent_id = int(base.rsplit("/", 1)[-1])
    before = _size(agent_id, COLLEAGUE)
    monkeypatch.setattr(settings, "live_prompt_char_budget", 4_000, raising=False)
    assert _size(agent_id, COLLEAGUE) == before


def test_what_keeps_the_agent_honest_is_never_dropped(monkeypatch):
    """Grounding, the objective, what to decide, the hard rules and the output contract all stay."""
    from app.services import agents

    class Persona(dict):
        """A profile that answers for any key, like the real one does."""
        def __getitem__(self, key):
            return self.get(key, "")

    system = agent._system_prompt(Persona(agents.PROFILE_DEFAULTS), Persona(), [], None)
    trimmed = agent.compact_live_prompt(system, 6_000)
    for heading in ("# Grounding", "# Objective", "# Decide before you speak", "# Hard rules", "# Output"):
        assert heading in trimmed, f"{heading} was dropped"
