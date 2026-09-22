"""A colleague teaching the agent on a live call: check first, then save, and say which happened.

The tool is deliberately one call rather than two, so the model cannot save without looking; these
tests pin the three answers the prompt keys off — ALREADY KNOWN, CONFLICTS, SAVED.
"""

import time

import pytest

from app.services import rag
from app.services.agent_tools import _taught_passage, teach_fact_tool


@pytest.fixture(scope="module")
def agent_id(client):
    """One workspace for this module: these tests write to a knowledge base, so they keep it away from
    the shared fixture agent, and creating one per test runs into the workspace-creation guard."""
    created = client.post("/api/agents", json={"name": "Teach test"})
    assert created.status_code == 200, created.text
    return created.json()["id"]


def test_a_new_fact_is_saved_and_findable(agent_id):
    answer = teach_fact_tool(agent_id, "The Premium listing plan costs 3499 rupees for thirty days.", "Premium plan price")
    assert answer.startswith("SAVED"), answer

    # Indexing runs in a background thread, as it does on a call: the fact is searchable a moment later.
    for _ in range(40):
        results = rag.search(agent_id, "premium plan price", top_k=3, use_embeddings=False)
        if any("3499" in r["text"] for r in results):
            return
        time.sleep(0.05)
    raise AssertionError(f"the taught fact never became searchable: {results}")


def test_the_same_fact_twice_is_recognised_not_duplicated(agent_id):
    fact = "The Featured listing plan costs 5999 rupees for thirty days."
    assert teach_fact_tool(agent_id, fact, "Featured plan price").startswith("SAVED")
    again = teach_fact_tool(agent_id, fact, "Featured plan price")
    assert again.startswith("ALREADY KNOWN"), again


def test_a_contradicting_fact_is_flagged_for_a_person(agent_id):
    teach_fact_tool(agent_id, "Our office is open Monday to Saturday, nine to six.", "Office hours")
    answer = teach_fact_tool(agent_id, "Our office is open seven days a week, nine to nine.", "Office hours")
    assert "CONFLICTS" in answer, answer


def test_a_fragment_is_refused_rather_than_saved(agent_id):
    assert teach_fact_tool(agent_id, "3499", "price").startswith("Failed")


def test_the_saved_passage_fits_the_chunker_and_carries_english_keywords():
    passage = _taught_passage("Premium plan ki keemat", "प्रीमियम प्लान की कीमत अब 3499 रुपये है, तीस दिन के लिए।")
    assert len(passage) <= 400                      # one chunk, never split mid-fact
    assert len(rag.chunk_text(passage)) == 1
    assert "price" in passage                        # a Hindi fact still matches an English-expanded query
