from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.api.deps import workspace
from app.core.auth import actor
from app.services import knowledge_profile, rag

router = APIRouter(prefix="/api/agents/{agent_id}/knowledge", tags=["knowledge"])
MAX_UPLOAD = 25 * 1024 * 1024


class TextDoc(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=30, max_length=500_000)


class Query(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(3, ge=1, le=10)   # a live turn retrieves 3: the page must show what a call would get


@router.get("")
def list_documents(agent_id: int = Depends(workspace)):
    documents = rag.list_documents(agent_id)
    # The index counts distinct titles of ready chunks; the page lists documents. Report the list's count.
    return {"documents": documents, "stats": {**rag.stats(agent_id), "documents": len(documents)}, "coverage": knowledge_profile.get(agent_id)}


@router.post("/coverage")
def refresh_coverage(agent_id: int = Depends(workspace)):
    """Re-read the documents and refill topic coverage (runs in the background)."""
    knowledge_profile.rebuild_async(agent_id)
    return {"status": "analyzing"}


@router.post("/upload")
async def upload(request: Request, file: UploadFile = File(...), title: str = Form(""), agent_id: int = Depends(workspace)):
    content = await file.read()
    if len(content) > MAX_UPLOAD:
        raise HTTPException(413, "File too large (max 25 MB).")
    try:
        return await run_in_threadpool(rag.add_document, agent_id, title.strip() or file.filename, file.filename, content, file.content_type, actor(request))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/text")
def add_text(body: TextDoc, request: Request, agent_id: int = Depends(workspace)):
    try:
        return rag.add_text(agent_id, body.title, body.text, actor(request))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/search")
def search(body: Query, agent_id: int = Depends(workspace)):
    """What this question would retrieve on a call.

    The page used to search with a 2.5s embedding budget and no relevance gate, so it demonstrated a
    retrieval no live turn ever performs — passages here, "a specialist will confirm" on the phone.
    It now runs the same gate and the same passage count as a turn, with the budget a warm prefetch
    gives a real call.
    """
    from app.services.agent import needs_knowledge
    use_embeddings = needs_knowledge(body.query)
    results = rag.search(agent_id, body.query, body.top_k, use_embeddings=use_embeddings, embed_timeout=1.5)
    return {"results": results, "semantic": rag.stats(agent_id)["semantic"]}


@router.get("/{doc_id}")
def get(doc_id: int, agent_id: int = Depends(workspace)):
    doc = rag.get_document(agent_id, doc_id, with_chunks=True)
    if not doc:
        raise HTTPException(404, "Document not found.")
    return doc


@router.delete("/{doc_id}")
def delete(doc_id: int, request: Request, agent_id: int = Depends(workspace)):
    if not rag.delete_document(agent_id, doc_id, actor(request)):
        raise HTTPException(404, "Document not found.")
    return {"ok": True}
