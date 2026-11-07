from app.core.database import get_db
from app.models.document import Document, DocumentChunk
from app.services import agents, knowledge_profile, llm


def _make_doc(db, agent_id, title, chars):
    doc = Document(agent_id=agent_id, title=title, status="ready", chars=chars)
    db.add(doc)
    db.flush()
    db.add(DocumentChunk(document_id=doc.id, position=0, text="x" * chars))
    return doc.id


def test_corpus_budgets_per_document_so_later_uploads_are_not_starved(base, client):
    agent_id = int(base.rsplit("/", 1)[-1])
    with get_db() as db:
        _make_doc(db, agent_id, "Old huge doc", 40_000)
        _make_doc(db, agent_id, "New small doc", 500)
        db.commit()

    corpus, titles = knowledge_profile._corpus(agent_id, knowledge_profile._topics(agent_id))

    assert "New small doc" in titles
    assert "Old huge doc" in titles
    assert len(corpus) <= knowledge_profile.MAX_CHARS + 200


def test_custom_topic_is_threaded_into_coverage(client, monkeypatch):
    agent_id = client.post("/api/agents", json={"name": "Custom Topic Co"}).json()["id"]
    agents.update_profile(agent_id, {"custom_topics": ["Warranty terms"]})

    with get_db() as db:
        _make_doc(db, agent_id, "Warranty doc", 200)
        db.commit()

    # _topics() must merge the operator's custom topic on top of the fixed six.
    topics = knowledge_profile._topics(agent_id)
    assert "custom:Warranty terms" in topics

    def complete(messages, json_mode=False, max_tokens=500, temperature=0.3, **_):
        return llm.LLMResult(
            '{"custom:Warranty terms": {"summary": "12 month warranty on all plans.", "documents": ["Warranty doc"]}}',
            "fake", "fake-model", 5)

    monkeypatch.setattr(llm, "complete", complete)

    profile = knowledge_profile.rebuild(agent_id)

    assert profile["status"] == "ready"
    assert profile["topics"]["custom:Warranty terms"]["summary"] == "12 month warranty on all plans."
