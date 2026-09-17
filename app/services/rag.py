"""
Knowledge base (RAG), one isolated knowledge base per agent.

Ingest: PDF / DOCX / TXT / MD / CSV -> text -> overlapping chunks ->
optional embeddings (OpenRouter) -> stored in the database.

Retrieve: hybrid ranking = BM25 keyword score + cosine similarity
(when embeddings exist), cached in memory per process and agent, and
invalidated by a per-agent version counter so every replica sees new documents.
"""

import io
import math
import re
import threading
from collections import Counter
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select

from app.core import store
from app.core.database import get_db
from app.core.logging import get_logger
from app.models.document import Document, DocumentChunk
from app.services import events, llm

log = get_logger(__name__)

CHUNK_CHARS = 400
CHUNK_OVERLAP = 150
MAX_DOC_CHARS = 2_000_000
TOKEN_RE = re.compile(r"[\wऀ-ॿ]+", re.UNICODE)
STOPWORDS = set("a an and are as at be by for from has have i in is it its of on or our that the this to we what with you your".split())

# Callers speak Hindi and Hinglish; knowledge bases are almost always written in English, so keyword
# search finds nothing and the agent answers "a specialist will confirm". Embeddings bridge languages
# but need a network round trip that can time out mid-call, so the query is also expanded locally:
# every Hindi/romanised term here adds its English equivalent before BM25 runs. Purely additive —
# the original words are kept, so an English query is unaffected.
QUERY_GLOSS = {
    # price and money
    "कीमत": "price cost", "दाम": "price cost", "कितने": "how much price", "कितनी": "how much price",
    "रुपये": "price rupees", "लाख": "lakh price budget", "बजट": "budget", "सस्ता": "cheap low price",
    "महंगा": "expensive price", "छूट": "discount offer", "किस्त": "emi instalment finance",
    "kitna": "how much price", "kitni": "how much price", "kimat": "price cost", "daam": "price cost",
    "paisa": "price money", "budget": "budget", "sasta": "cheap low price", "emi": "emi finance loan",
    # product and service
    "गाड़ी": "car vehicle", "कार": "car vehicle", "गाडी": "car vehicle", "डीजल": "diesel",
    "पेट्रोल": "petrol", "पुरानी": "used second hand", "नई": "new", "सेवा": "service",
    "सुविधा": "service feature", "गारंटी": "warranty guarantee", "बीमा": "insurance",
    "gaadi": "car vehicle", "gadi": "car vehicle", "purani": "used second hand", "nayi": "new",
    "service": "service", "warranty": "warranty guarantee", "bima": "insurance",
    # process
    "मीटिंग": "meeting appointment", "अपॉइंटमेंट": "meeting appointment", "टेस्ट": "test drive",
    "शोरूम": "showroom branch office", "जगह": "location address city", "पता": "address location",
    "समय": "time timing hours", "दस्तावेज": "documents paperwork", "कागज": "documents paperwork",
    "भुगतान": "payment", "वापसी": "refund return", "शिकायत": "complaint support",
    "meeting": "meeting appointment", "showroom": "showroom branch office", "pata": "address location",
    "samay": "time timing hours", "kagaz": "documents paperwork", "kagzat": "documents paperwork",
    # question words that hint at intent
    "कैसे": "how process", "क्या": "what", "कहाँ": "where location", "कब": "when time",
    "kaise": "how process", "kahan": "where location", "kab": "when time",
}


def expand_query(query: str) -> str:
    """Add English equivalents for Hindi/Hinglish terms so keyword search works on an English corpus."""
    extra = [gloss for token in tokenize(query) if (gloss := QUERY_GLOSS.get(token))]
    return f"{query} {' '.join(extra)}" if extra else query


# ---------------- extraction ----------------

def extract_text(filename: str, content: bytes) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(content))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    if name.endswith(".docx"):
        import docx
        document = docx.Document(io.BytesIO(content))
        parts = [p.text for p in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                parts.append(" | ".join(cell.text for cell in row.cells))
        return "\n".join(parts)
    if name.endswith((".txt", ".md", ".csv")):
        for encoding in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                return content.decode(encoding)
            except UnicodeDecodeError:
                continue
    raise ValueError("Unsupported file type. Upload PDF, DOCX, TXT, MD or CSV.")


def chunk_text(text: str) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    chunks, current = [], ""
    for para in paragraphs:
        while len(para) > CHUNK_CHARS:  # split very long paragraphs on sentence boundaries
            cut = max(para.rfind(". ", 0, CHUNK_CHARS), para.rfind("\n", 0, CHUNK_CHARS))
            cut = cut + 1 if cut > CHUNK_CHARS // 2 else CHUNK_CHARS
            if current:
                chunks.append(current)
                current = ""
            chunks.append(para[:cut].strip())
            para = para[max(cut - CHUNK_OVERLAP, 1):].strip()
        if len(current) + len(para) + 2 <= CHUNK_CHARS:
            current = f"{current}\n\n{para}" if current else para
        else:
            if current:
                chunks.append(current)
            tail = current[-CHUNK_OVERLAP:] if current else ""
            current = f"{tail}\n{para}" if tail else para
    if current:
        chunks.append(current)
    return [c for c in chunks if len(c) > 20]


def tokenize(text: str) -> list[str]:
    return [t for t in (m.group(0).lower() for m in TOKEN_RE.finditer(text)) if t not in STOPWORDS and len(t) > 1]


# ---------------- ingestion ----------------

def add_document(agent_id: int, title: str, filename: str, content: bytes, content_type: str | None,
                 actor: str = "admin") -> dict:
    text = extract_text(filename, content)[:MAX_DOC_CHARS]
    if len(text.strip()) < 30:
        raise ValueError("No readable text found in the document (scanned PDFs need OCR first).")

    with get_db() as db:
        doc = Document(agent_id=agent_id, title=title or filename, filename=filename, content_type=content_type,
                       size_bytes=len(content), chars=len(text), status="processing")
        db.add(doc)
        db.flush()
        doc_id = doc.id

    threading.Thread(target=_process, args=(agent_id, doc_id, text, actor), daemon=True).start()
    return get_document(agent_id, doc_id)


def add_text(agent_id: int, title: str, text: str, actor: str = "admin") -> dict:
    return add_document(agent_id, title, f"{title[:60] or 'note'}.txt", text.encode("utf-8"), "text/plain", actor)


def _process(agent_id: int, doc_id: int, text: str, actor: str):
    try:
        chunks = chunk_text(text)
        # Embed in batches; keyword search still works if this fails
        collected = []
        for batch in (chunks[i:i + 64] for i in range(0, len(chunks), 64)):
            result = llm.embed(batch)
            if result is None:
                collected = None
                break
            collected.extend(result)
        vectors = collected

        with get_db() as db:
            for i, chunk in enumerate(chunks):
                vector = np.asarray(vectors[i], dtype=np.float32).tobytes() if vectors else None
                db.add(DocumentChunk(document_id=doc_id, position=i, text=chunk, embedding=vector))
            doc = db.get(Document, doc_id)
            doc.chunk_count = len(chunks)
            doc.embedded = bool(vectors)
            doc.status = "ready"
            title = doc.title
        _bump_version(agent_id)
        from app.services import knowledge_profile
        knowledge_profile.rebuild_async(agent_id)
        events.record("document.added", f"Knowledge added: {title}",
                      f"{len(chunks)} chunks · {'semantic + keyword' if vectors else 'keyword'} search",
                      agent_id=agent_id, actor=actor, data={"document_id": doc_id})
    except Exception as e:
        log.exception("Document %s processing failed", doc_id)
        with get_db() as db:
            doc = db.get(Document, doc_id)
            if doc:
                doc.status, doc.error = "failed", str(e)


def _owned(db, agent_id: int, doc_id: int) -> Document | None:
    doc = db.get(Document, doc_id)
    return doc if doc and doc.agent_id == agent_id else None


def delete_document(agent_id: int, doc_id: int, actor: str = "admin") -> bool:
    with get_db() as db:
        doc = _owned(db, agent_id, doc_id)
        if not doc:
            return False
        db.query(DocumentChunk).filter(DocumentChunk.document_id == doc_id).delete()
        db.delete(doc)
        title = doc.title
    _bump_version(agent_id)
    from app.services import knowledge_profile
    knowledge_profile.rebuild_async(agent_id)
    events.record("document.deleted", f"Knowledge removed: {title}", agent_id=agent_id, actor=actor)
    return True


def list_documents(agent_id: int) -> list[dict]:
    with get_db() as db:
        return [d.to_dict() for d in db.scalars(select(Document).where(Document.agent_id == agent_id).order_by(Document.id.desc()))]


def get_document(agent_id: int, doc_id: int, with_chunks: bool = False) -> dict | None:
    with get_db() as db:
        doc = _owned(db, agent_id, doc_id)
        if not doc:
            return None
        data = doc.to_dict()
        if with_chunks:
            data["chunks"] = [c.text for c in db.scalars(
                select(DocumentChunk).where(DocumentChunk.document_id == doc_id).order_by(DocumentChunk.position))]
        return data


# ---------------- retrieval ----------------

@dataclass
class _Index:
    version: int
    ids: list[int]
    texts: list[str]
    titles: list[str]
    tfs: list[Counter]
    lengths: np.ndarray
    df: Counter
    vectors: np.ndarray | None  # normalised, rows aligned with ids (zeros when missing)
    has_vector: np.ndarray


_indexes: dict[int, _Index] = {}
_index_lock = threading.Lock()


def _version(agent_id: int) -> int:
    return int(store.get_json(f"rag:version:{agent_id}", 0))


def _bump_version(agent_id: int):
    store.set_json(f"rag:version:{agent_id}", _version(agent_id) + 1)


def _load_index(agent_id: int) -> _Index:
    version = _version(agent_id)
    index = _indexes.get(agent_id)
    if index is not None and index.version == version:
        return index
    with _index_lock:
        index = _indexes.get(agent_id)
        if index is not None and index.version == version:
            return index
        with get_db() as db:
            rows = db.execute(
                select(DocumentChunk.id, DocumentChunk.text, DocumentChunk.embedding, Document.title)
                .join(Document, Document.id == DocumentChunk.document_id)
                .where(Document.status == "ready", Document.agent_id == agent_id)
            ).all()
        ids = [r[0] for r in rows]
        texts = [r[1] for r in rows]
        titles = [r[3] for r in rows]
        tfs = [Counter(tokenize(t)) for t in texts]
        df = Counter()
        for tf in tfs:
            df.update(tf.keys())
        dims = next((len(r[2]) // 4 for r in rows if r[2]), 0)
        vectors, has_vector = None, np.zeros(len(rows), dtype=bool)
        if dims:
            vectors = np.zeros((len(rows), dims), dtype=np.float32)
            for i, r in enumerate(rows):
                if r[2] and len(r[2]) // 4 == dims:
                    v = np.frombuffer(r[2], dtype=np.float32)
                    vectors[i] = v / (np.linalg.norm(v) or 1)
                    has_vector[i] = True
        index = _Index(version, ids, texts, titles, tfs,
                       np.array([sum(tf.values()) for tf in tfs] or [0], dtype=np.float32), df, vectors, has_vector)
        _indexes[agent_id] = index
        return index


def search(agent_id: int, query: str, top_k: int = 4, use_embeddings: bool = True, embed_timeout: float = 2.5) -> list[dict]:
    index = _load_index(agent_id)
    if not index.ids or not query.strip():
        return []

    n = len(index.ids)
    avg_len = float(index.lengths.mean()) or 1.0
    k1, b = 1.5, 0.75
    terms = tokenize(expand_query(query))
    bm25 = np.zeros(n, dtype=np.float32)
    for term in set(terms):
        df = index.df.get(term)
        if not df:
            continue
        idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
        for i, tf in enumerate(index.tfs):
            f = tf.get(term)
            if f:
                bm25[i] += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * index.lengths[i] / avg_len))
    if bm25.max() > 0:
        bm25 = bm25 / bm25.max()

    scores = bm25
    # Keyword search is language-bound: a Hindi question never overlaps an English knowledge base, so
    # BM25 alone returns nothing on Hindi calls. Embeddings match across languages, so pay for them
    # when keywords found nothing — the live path skips them only as a latency optimisation.
    if not use_embeddings and bm25.max() <= 0 and index.vectors is not None and index.has_vector.any():
        use_embeddings = True
        # Nothing to lose: without this the turn has no knowledge at all, so allow a longer round trip.
        embed_timeout = max(embed_timeout, 1.5)
    if use_embeddings and index.vectors is not None and index.has_vector.any():
        qv = llm.embed([query], timeout=embed_timeout)
        if qv:
            q = np.asarray(qv[0], dtype=np.float32)
            q = q / (np.linalg.norm(q) or 1)
            cosine = np.where(index.has_vector, index.vectors @ q, 0)
            scores = 0.35 * bm25 + 0.65 * np.clip(cosine, 0, 1)

    order = np.argsort(-scores)[:top_k]
    return [{"chunk_id": index.ids[i], "title": index.titles[i], "text": index.texts[i], "score": round(float(scores[i]), 3)}
            for i in order if scores[i] > 0.05]


def stats(agent_id: int) -> dict:
    index = _load_index(agent_id)
    return {"chunks": len(index.ids), "documents": len(set(index.titles)),
            "semantic": bool(index.vectors is not None and index.has_vector.any())}
