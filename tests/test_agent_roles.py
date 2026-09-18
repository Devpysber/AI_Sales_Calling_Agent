"""An agent describes the job it was built for, not always a sales call."""

from app.services import agent, agents


class Persona(dict):
    """A profile with every key present, the way get_profile returns one."""

    def __missing__(self, key):
        return ""


def prompt(**overrides) -> str:
    persona = Persona(agents.PROFILE_DEFAULTS)
    persona.update(agent_name="Neha", company_name="City Clinic", **overrides)
    return agent._system_prompt(persona, Persona(), [], None)


def test_the_role_and_the_caller_come_from_the_persona():
    text = prompt(agent_role="clinic front-desk coordinator", customer_noun="patient",
                  objective="Book the patient in with the right doctor.",
                  instructions="Be calm and unhurried. Never diagnose.",
                  qualification_criteria="Hot: wants an appointment now.")
    assert "a clinic front-desk coordinator at City Clinic" in text
    assert "speaking with a patient on a live PHONE CALL" in text
    assert "# Patient" in text
    assert "Earlier conversations with this patient" in text
    assert "sales consultant" not in text and "prospect" not in text.split("# Qualification")[0]


def test_an_empty_role_falls_back_instead_of_reading_oddly():
    text = prompt(agent_role="", customer_noun="")
    assert "a senior sales consultant at City Clinic" in text
    assert "speaking with a customer on a live PHONE CALL" in text


def test_every_template_role_builds_a_sensible_prompt():
    # The shapes the new-agent templates send: each must read as a real job title on the first line.
    for role, caller in (("order support specialist", "customer"), ("admissions counsellor", "student"),
                         ("reservations host", "guest"), ("property consultant", "prospect")):
        text = prompt(agent_role=role, customer_noun=caller)
        assert text.startswith(f"You are Neha, a {role} at City Clinic")
        assert f"speaking with a {caller}" in text


def test_a_new_agent_records_when_it_was_created(client):
    """The column was added without a default once, so every new workspace saved a null date."""
    created = agents.create({"name": "Created-at check"}, actor="admin")
    try:
        assert agents.get(created["id"])["created_at"], "a new agent must carry its creation date"
    finally:
        agents.delete(created["id"], actor="admin")
