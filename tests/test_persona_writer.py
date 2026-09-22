"""Drafting a persona from the knowledge base: never invents, never saves, and says why when it can't."""

import time

import pytest

from app.services import persona_writer, rag


@pytest.fixture(scope="module")
def agent_id(client):
    return client.post("/api/agents", json={"name": "Persona draft test"}).json()["id"]


def test_an_agent_with_no_documents_is_told_so_rather_than_given_a_guess(agent_id):
    result = persona_writer.draft(agent_id)
    assert result["fields"] == {}
    assert "no documents" in result["reason"]


def test_the_draft_endpoint_never_writes_to_the_profile(client, agent_id):
    before = client.get(f"/api/agents/{agent_id}/profile").json()["profile"]
    client.post(f"/api/agents/{agent_id}/profile/draft")
    after = client.get(f"/api/agents/{agent_id}/profile").json()["profile"]
    assert before == after


def test_fields_are_capped_to_what_the_profile_accepts(agent_id, monkeypatch):
    rag.add_text(agent_id, "What we do", "We run a wedding vendor marketplace for couples in India. " * 5, actor="test")
    for _ in range(40):
        if rag.stats(agent_id)["chunks"]:
            break
        time.sleep(0.05)

    class _Result:
        provider, model = "test", "test"
        text = '{"objective": "' + "x" * 5000 + '", "customer_noun": "couple"}'

    monkeypatch.setattr(persona_writer.llm, "complete", lambda *a, **k: _Result())
    monkeypatch.setattr(persona_writer.llm, "parse_json", lambda t: {"objective": "x" * 5000, "customer_noun": "couple"})
    drafted = persona_writer.draft(agent_id)["fields"]
    assert len(drafted["objective"]) == 600      # PROFILE_LIMITS["objective"]
    assert drafted["customer_noun"] == "couple"


def test_autofill_writes_only_empty_fields_and_never_the_identity(client, agent_id, monkeypatch):
    """The agent may write its own playbook from the documents, but not over a person's words."""
    base = f"/api/agents/{agent_id}"
    client.put(f"{base}/profile", json={"objective": "Book demos for the Indore showroom.", "agent_name": "Priya"})

    monkeypatch.setattr(persona_writer, "draft", lambda _id: {"fields": {
        "objective": "Something the model made up",
        "call_to_action": "Ask which wedding services they need.",
        "agent_role": "wedding planning advisor",
    }})
    filled = persona_writer.autofill(agent_id)

    profile = client.get(f"{base}/profile").json()["profile"]
    assert "call_to_action" in filled and profile["call_to_action"] == "Ask which wedding services they need."
    assert profile["objective"] == "Book demos for the Indore showroom."   # the person's words, untouched
    assert profile["agent_name"] == "Priya"                                 # identity is never written
    assert "objective" not in filled


def test_autofill_does_nothing_when_the_playbook_is_already_written(agent_id, monkeypatch):
    monkeypatch.setattr(persona_writer, "draft", lambda _id: {"fields": {"objective": "x"}})
    from app.services import agents as agent_service
    agent_service.update_profile(agent_id, {f: "already written" for f in persona_writer.AUTOFILL}, actor="test")
    assert persona_writer.autofill(agent_id) == {}


def test_a_draft_is_repaired_into_the_shape_the_form_and_the_prompt_expect():
    """The model returns these shapes often enough that repairing beats re-asking, which costs a turn."""
    tidy = persona_writer._tidy

    # A field the model returns as a JSON list becomes the lines a person would have typed, rather
    # than Python syntax in the form: ['Open by welcoming them.', 'Ask which city.']
    assert persona_writer._as_text(["Open by welcoming them.", "Ask which city."]) == "Open by welcoming them.\nAsk which city."
    assert persona_writer._as_text({"Is it free?": "Yes, for couples."}) == "Is it free?: Yes, for couples."

    # An objection and its answer split across two lines, under either labelling, become one line each.
    assert tidy("objection_handling", "What they say: Is it free?\nWhat you answer: Yes, for couples.") == "Is it free?: Yes, for couples."
    assert tidy("objection_handling", "Objection: Is it free?\nAnswer: Yes, for couples.") == "Is it free?: Yes, for couples."
    assert tidy("objection_handling", "- Objection: I have a vendor.\n- Answer: We help with the rest.") == "I have a vendor: We help with the rest."
    # Several objections returned as one run-on line are split apart.
    assert tidy("objection_handling", "Too costly: It is free. Already booked: We help with other services.").count("\n") == 1

    # A "next step" that is really a question, however politely phrased, is dropped rather than shown
    # in a field the page labels "the single next step the agent asks for".
    assert tidy("call_to_action", "What are you looking for?") == ""
    assert tidy("call_to_action", "Please tell me what kind of venue you want.") == ""
    assert tidy("call_to_action", "Send the registration link on WhatsApp.") == "Send the registration link on WhatsApp."

    # One word means one word.
    assert tidy("customer_noun", "couple (planning a wedding)") == "couple"
