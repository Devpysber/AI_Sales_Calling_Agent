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
