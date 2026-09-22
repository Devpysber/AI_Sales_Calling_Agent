"""
Knowledge coverage: an LLM reads an agent's documents and fills what they say about the
six topics prospects ask about most. Topics the documents don't cover stay empty, so the
Knowledge page shows exactly what is missing. Recomputed in the background after every
upload or delete; runs on the summary (free-first) models.
"""

import threading
from datetime import datetime

from sqlalchemy import select

from app.core.config import settings
from app.core.database import get_db
from app.core.logging import get_logger
from app.models.document import Document, DocumentChunk
from app.services import llm
from app.services.settings_service import SettingsService

log = get_logger(__name__)

TOPICS = {
    "overview": "Company overview: who they are, history, locations, team",
    "services": "Services & products: what they sell and who it is for",
    "pricing": "Pricing: prices, packages, fees, payment terms",
    "faq": "FAQs: common customer questions with their answers",
    "proof": "Case studies: clients, results, testimonials, numbers",
    "policy": "Process & policies: how they work, onboarding, support, refunds, contracts",
}
MAX_CHARS = 30_000  # of document text sent to the model
# Retry size when the full corpus is refused. Free-tier models cap a prompt near 2,500 tokens, so a
# knowledge base of any size fails outright on them; a shorter read fills most topics rather than none.
RETRY_CHARS = 6_000

PROMPT = """You audit a company's knowledge base for a phone sales agent. Read the documents and, for each topic, write what they
actually say: 1-3 short factual sentences (max 350 characters) using concrete details (names, numbers, prices).
If the documents do not cover a topic, return an empty string for it — an empty string, not a sentence saying the topic is
missing, which would show on the page as if the topic were covered. Never invent or generalise. Write in English.

Topics:
{topics}

Return ONLY JSON: {{"overview": {{"summary": "", "documents": []}}, ...}} with every topic key; "documents" lists the titles
of the documents the summary comes from — at most two per topic, and only titles given above, never section headings."""

_running: set[int] = set()
_pending: set[int] = set()   # a rebuild asked for while one was running: run once more when it ends
_lock = threading.Lock()


def _key(agent_id: int) -> str:
    return f"knowledge_profile.{agent_id}"


def get(agent_id: int) -> dict:
    return SettingsService().get_state(_key(agent_id)) or {"status": "empty", "topics": {}}


def _corpus(agent_id: int) -> tuple[str, list[str]]:
    with get_db() as db:
        rows = db.execute(
            select(Document.id, Document.title, DocumentChunk.text)
            .join(DocumentChunk, DocumentChunk.document_id == Document.id)
            .where(Document.agent_id == agent_id, Document.status == "ready")
            .order_by(Document.id, DocumentChunk.position)
        ).all()
    doc_ids = list(dict.fromkeys(doc_id for doc_id, _, _ in rows))
    per_doc = max(MAX_CHARS // max(len(doc_ids), 1), 2000)
    parts, titles = [], []
    doc_used = {}
    for doc_id, title, text in rows:
        used = doc_used.get(doc_id, 0)
        if used >= per_doc:
            continue
        if used == 0:
            parts.append(f"\n=== Document: {title} ===\n")
            titles.append(title)
        remaining = per_doc - used
        chunk = text[:remaining]
        parts.append(chunk)
        doc_used[doc_id] = used + len(chunk)
    return "\n".join(parts), titles


def rebuild(agent_id: int) -> dict:
    corpus, titles = _corpus(agent_id)
    state = SettingsService()
    if not corpus.strip():
        profile = {"status": "empty", "topics": {}, "updated_at": datetime.now().isoformat(timespec="seconds")}
        state.set_state(_key(agent_id), profile)
        return profile
    state.set_state(_key(agent_id), {**get(agent_id), "status": "analyzing"})
    system = PROMPT.format(topics="\n".join(f"- {k}: {v}" for k, v in TOPICS.items()))

    def audit(text: str):
        result = llm.complete(
            [{"role": "system", "content": system}, {"role": "user", "content": text}],
            json_mode=True, max_tokens=3000, temperature=0.1, providers=settings.summary_llm_providers, timeout=40)
        return result, llm.parse_json(result.text)

    try:
        try:
            result, data = audit(corpus)
        except Exception as e:  # noqa: BLE001 - most often a model refusing the prompt length
            if len(corpus) <= RETRY_CHARS:
                raise
            log.warning("Knowledge coverage: full corpus refused for agent %s (%s); retrying on %s characters",
                        agent_id, str(e)[:120], RETRY_CHARS)
            result, data = audit(corpus[:RETRY_CHARS])
        topics = {}
        for key in TOPICS:
            item = data.get(key) or {}
            if isinstance(item, str):
                item = {"summary": item}
            summary = str(item.get("summary") or "").strip()[:500]
            docs = [d for d in (item.get("documents") or []) if isinstance(d, str) and d in titles]
            topics[key] = {"summary": summary, "documents": docs or (titles[:1] if summary else [])}
        profile = {"status": "ready", "topics": topics, "documents": titles, "model": f"{result.provider}/{result.model}",
                   "updated_at": datetime.now().isoformat(timespec="seconds")}
    except Exception as e:  # noqa: BLE001 - coverage is advisory
        log.warning("Knowledge coverage failed for agent %s: %s", agent_id, e)
        profile = {**get(agent_id), "status": "failed", "error": str(e)[:300]}
    state.set_state(_key(agent_id), profile)
    return profile


def rebuild_async(agent_id: int):
    """Coalesce: one analysis per agent at a time; a request made mid-analysis queues exactly one rerun."""
    with _lock:
        if agent_id in _running:
            _pending.add(agent_id)  # documents that finish during an analysis would otherwise never be read
            return
        _running.add(agent_id)

    def run():
        while True:
            try:
                rebuild(agent_id)
            except Exception as e:  # noqa: BLE001 - rebuild() already records failures in the profile
                log.warning("Knowledge coverage rerun failed for agent %s: %s", agent_id, e)
            with _lock:
                if agent_id in _pending:
                    _pending.discard(agent_id)
                    continue
                _running.discard(agent_id)
                return

    threading.Thread(target=run, daemon=True, name=f"kb-coverage-{agent_id}").start()
