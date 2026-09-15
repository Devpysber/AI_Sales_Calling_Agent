from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from app.api.deps import workspace
from app.core.auth import actor
from app.services import rag

router = APIRouter(prefix="/api/agents/{agent_id}/knowledge", tags=["knowledge"])
MAX_UPLOAD = 25 * 1024 * 1024


class TextDoc(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=30, max_length=500_000)


class Query(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(4, ge=1, le=10)


@router.get("")
def list_documents(agent_id: int = Depends(workspace)):
    return {"documents": rag.list_documents(agent_id), "stats": rag.stats(agent_id)}


@router.post("/upload")
async def upload(request: Request, file: UploadFile = File(...), title: str = Form(""), agent_id: int = Depends(workspace)):
    content = await file.read()
    if len(content) > MAX_UPLOAD:
        raise HTTPException(413, "File too large (max 25 MB).")
    try:
        return rag.add_document(agent_id, title.strip() or file.filename, file.filename, content, file.content_type, actor(request))
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
    return {"results": rag.search(agent_id, body.query, body.top_k)}


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
