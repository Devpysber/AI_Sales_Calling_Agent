from app.core.database import get_db
from app.models.document import Document, DocumentChunk
from app.services import knowledge_profile


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

    corpus, titles = knowledge_profile._corpus(agent_id)

    assert "New small doc" in titles
    assert "Old huge doc" in titles
    assert len(corpus) <= knowledge_profile.MAX_CHARS + 200
