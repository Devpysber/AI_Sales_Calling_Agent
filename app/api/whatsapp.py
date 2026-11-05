"""Meta WhatsApp Cloud API webhook: verification handshake and inbound messages (no session auth; signed by Meta)."""

import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import workspace
from app.core.auth import actor
from app.core.config import settings
from app.core.database import get_db
from app.models.call import Call
from app.models.lead import Lead
from app.services import whatsapp_service as wa

public = APIRouter(prefix="/api/whatsapp", tags=["whatsapp"])
router = APIRouter(prefix="/api/agents/{agent_id}/whatsapp", tags=["whatsapp"])


@public.get("/webhook")
def verify(request: Request):
    q = request.query_params
    token = settings.whatsapp_verify_token
    if token and q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == token:
        return PlainTextResponse(q.get("hub.challenge", ""))
    raise HTTPException(403, "Verification failed")


@public.post("/webhook")
async def inbound(request: Request, background: BackgroundTasks):
    body = await request.body()
    if not wa.verify_signature(body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(403, "Bad signature")
    try:
        payload = await request.json()
    except ValueError:
        return {"ok": True}
    for msg in wa.parse_webhook(payload):
        background.add_task(wa.handle_inbound, msg)  # sync function: Starlette runs it in a worker thread
    return {"ok": True}


class Reply(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)


def _chat(db, agent_id: int, chat_id: int):
    row = db.execute(select(Call, Lead.name, Lead.phone).outerjoin(Lead, Lead.id == Call.lead_id)
                     .where(Call.id == chat_id, Call.agent_id == agent_id, Call.direction == "whatsapp")).first()
    if row is None:
        raise HTTPException(404, "Chat not found.")
    return row


def _item(call: Call, name, phone, with_transcript: bool = False) -> dict:
    turns = json.loads(call.transcript or "[]")
    last = turns[-1] if turns else {}
    data = {"id": call.id, "lead_id": call.lead_id, "lead_name": name, "lead_phone": phone or call.from_number,
            "status": call.status, "last_text": last.get("text", ""), "last_at": last.get("at") or (call.created_at.isoformat() if call.created_at else None),
            "message_count": len(turns), "ai_paused": call.trigger == "whatsapp_human"}
    if with_transcript:
        data["transcript"] = turns
        data["summary"] = call.summary
    return data


@router.get("/chats")
def chats(agent_id: int = Depends(workspace)):
    with get_db() as db:
        rows = db.execute(select(Call, Lead.name, Lead.phone).outerjoin(Lead, Lead.id == Call.lead_id)
                          .where(Call.agent_id == agent_id, Call.direction == "whatsapp")
                          .order_by(Call.id.desc()).limit(200)).all()
        return {"items": [_item(c, n, p) for c, n, p in rows]}


@router.get("/chats/{chat_id}")
def chat(chat_id: int, agent_id: int = Depends(workspace)):
    with get_db() as db:
        c, n, p = _chat(db, agent_id, chat_id)
        return _item(c, n, p, with_transcript=True)


@router.post("/chats/{chat_id}/reply")
def reply(chat_id: int, body: Reply, request: Request, agent_id: int = Depends(workspace)):
    with get_db() as db:
        _chat(db, agent_id, chat_id)
    try:
        return wa.human_reply(chat_id, body.text.strip(), actor(request))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/chats/{chat_id}/resume")
def resume(chat_id: int, agent_id: int = Depends(workspace)):
    with get_db() as db:
        _chat(db, agent_id, chat_id)
    wa.set_ai_paused(chat_id, False)
    return {"ok": True}
