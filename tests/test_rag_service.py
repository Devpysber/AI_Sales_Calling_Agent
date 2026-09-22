"""Targeted checks for the fixes in app/services/rag.py:
- a corrupt/encrypted PDF or DOCX raises ValueError (-> 400), not some unmapped exception (-> 500).
- a mid-ingestion embedding failure keeps the vectors already paid for instead of discarding them.
- prefetch never pays for a query embedding when the agent has no embedded chunks to search.
"""
import time
from collections import Counter

import numpy as np
import pytest

from app.core.database import get_db
from app.models.document import Document, DocumentChunk
from app.services import rag


def test_extract_text_corrupt_pdf_raises_valueerror():
    with pytest.raises(ValueError):
        rag.extract_text("broken.pdf", b"not a real pdf")


def test_extract_text_corrupt_docx_raises_valueerror():
    with pytest.raises(ValueError):
        rag.extract_text("broken.docx", b"not a real docx")


def test_partial_embed_failure_keeps_completed_vectors(client, monkeypatch):
    chunks = [f"chunk number {i} " + "padding text to fill the batch budget. " * 8 for i in range(70)]
    monkeypatch.setattr(rag, "chunk_text", lambda text: chunks)
    first_batch = len(rag._embed_batches(chunks)[0])
    assert first_batch < len(chunks)  # the document must span more than one request

    calls = []

    def fake_embed(batch, timeout=30):
        calls.append(len(batch))
        if len(calls) == 1:
            return [[0.1, 0.2]] * len(batch)
        return None

    monkeypatch.setattr(rag.llm, "embed", fake_embed)

    # A dedicated agent, so writing real vectors here doesn't pollute the shared `base` agent's index.
    agent_id = client.post("/api/agents", json={"name": "RAG partial-embed test"}).json()["id"]

    with get_db() as db:
        doc = Document(agent_id=agent_id, title="t", filename="t.txt", content_type="text/plain",
                       size_bytes=10, chars=10, status="processing")
        db.add(doc)
        db.flush()
        doc_id = doc.id

    rag._process(agent_id, doc_id, "irrelevant, chunk_text is patched", "admin")

    with get_db() as db:
        doc = db.get(Document, doc_id)
        assert doc.status == "ready"
        assert doc.embedded is True  # some chunks were embedded before the failure
        assert doc.error and "Semantic search unavailable" in doc.error

        stored = db.query(DocumentChunk).filter(DocumentChunk.document_id == doc_id).order_by(DocumentChunk.position).all()
        assert len(stored) == 70
        assert all(c.embedding is not None for c in stored[:first_batch])
        assert all(c.embedding is None for c in stored[first_batch:])


def test_prefetch_skips_embed_when_no_embedded_chunks(monkeypatch):
    empty_index = rag._Index(version=1, ids=[], texts=[], titles=[], tfs=[], lengths=np.zeros(0),
                             df=Counter(), vectors=None, has_vector=np.zeros(0, dtype=bool))
    monkeypatch.setattr(rag, "_load_index", lambda agent_id: empty_index)

    embed_calls = []
    monkeypatch.setattr(rag, "_do_embed", lambda query, timeout: embed_calls.append(query))

    query = "how much does it cost"
    rag.prefetch(agent_id=1, query=query)
    deadline = time.monotonic() + 2
    while query in rag._inflight and time.monotonic() < deadline:
        time.sleep(0.01)

    assert embed_calls == []
