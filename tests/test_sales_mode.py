"""sales_mode / sales_playbook: the agent is an active seller by default, on both voice and chat."""

import json

from app.services import agent, agents, chat_agent, llm
from app.services.crm_service import CRMService


class Persona(dict):
    """A profile with every key present, the way get_profile returns one."""

    def __missing__(self, key):
        return ""


def voice_prompt(**overrides) -> str:
    persona = Persona(agents.PROFILE_DEFAULTS)
    persona.update(agent_name="Neha", company_name="Acme", **overrides)
    return agent._system_prompt(persona, Persona(), [], None)


def test_sales_mode_on_adds_directive_and_playbook_to_voice_prompt():
    text = voice_prompt(sales_mode=True, sales_playbook="Upsell the annual plan to monthly subscribers.")
    assert "Sales mode" in text
    assert "propose ONE next step" in text
    assert "This business's sales notes: Upsell the annual plan to monthly subscribers." in text


def test_sales_mode_off_omits_directive_and_playbook_from_voice_prompt():
    text = voice_prompt(sales_mode=False, sales_playbook="Upsell the annual plan to monthly subscribers.")
    assert "Sales mode" not in text
    assert "Upsell the annual plan to monthly subscribers." not in text


def _lead(agent_id):
    return CRMService(agent_id).create({"phone": "9811100077", "name": "Chat Customer"}, actor="system")


def _captured_system(agent_id, lead, monkeypatch):
    captured = {}

    def fake_complete(messages, json_mode=False, max_tokens=300, temperature=0.3, **_):
        captured["system"] = messages[0]["content"]
        return llm.LLMResult(json.dumps({"reply": "ok", "tool": None}), "fake", "fake-model", 1)

    monkeypatch.setattr(llm, "tools_via_openrouter", lambda: False)
    monkeypatch.setattr(llm, "complete", fake_complete)
    chat_agent.respond(agent_id, None, [], "do you have anything for me?", lead, channel="whatsapp")
    return captured["system"]


def test_sales_mode_on_adds_directive_and_playbook_to_chat_prompt(base, client, monkeypatch):
    agent_id = int(base.rsplit("/", 1)[1])
    lead = _lead(agent_id)
    agents.update_profile(agent_id, {"sales_mode": True, "sales_playbook": "Cross-sell the care plan with every device sale."})
    try:
        system = _captured_system(agent_id, lead, monkeypatch)
        assert "Sales mode" in system
        assert "Cross-sell the care plan with every device sale." in system
        assert "chat-sized" in system
    finally:
        agents.update_profile(agent_id, {"sales_mode": True, "sales_playbook": ""})


def test_sales_mode_off_omits_directive_and_playbook_from_chat_prompt(base, client, monkeypatch):
    agent_id = int(base.rsplit("/", 1)[1])
    lead = _lead(agent_id)
    agents.update_profile(agent_id, {"sales_mode": False, "sales_playbook": "Cross-sell the care plan with every device sale."})
    try:
        system = _captured_system(agent_id, lead, monkeypatch)
        assert "Sales mode" not in system
        assert "Cross-sell the care plan with every device sale." not in system
        assert "chat-sized" not in system
    finally:
        agents.update_profile(agent_id, {"sales_mode": True, "sales_playbook": ""})
