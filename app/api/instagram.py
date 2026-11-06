"""Instagram Messaging webhook (no session auth; signed by Meta) + inbox endpoints, mirroring
app/api/whatsapp.py's shape for the same Conversation/Message tables (channel="instagram")."""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import workspace
from app.core.auth import actor
from app.core.config import settings
from app.core.database import get_db
from app.models.channel import ContactIdentity, Conversation, Message
from app.models.lead import Lead
from app.services import instagram_service as ig

public = APIRouter(prefix="/api/instagram", tags=["instagram"])
router = APIRouter(prefix="/api/agents/{agent_id}/instagram", tags=["instagram"])


@public.get("/webhook")
def verify(request: Request):
    q = request.query_params
    token = settings.instagram_verify_token
    if token and q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == token:
        return PlainTextResponse(q.get("hub.challenge", ""))
    raise HTTPException(403, "Verification failed")


@public.post("/webhook")
async def inbound(request: Request, background: BackgroundTasks):
    body = await request.body()
    if not ig.verify_signature(body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(403, "Bad signature")
    try:
        payload = await request.json()
    except ValueError:
        return {"ok": True}
    for msg in ig.parse_webhook(payload):
        background.add_task(ig.enqueue_inbound, msg)
    return {"ok": True}


class Reply(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)


def _chat(db, agent_id: int, chat_id: int):
    row = db.execute(select(Conversation, Lead.name, Lead.phone).outerjoin(Lead, Lead.id == Conversation.lead_id)
                     .where(Conversation.id == chat_id, Conversation.agent_id == agent_id,
                            Conversation.channel == "instagram")).first()
    if row is None:
        raise HTTPException(404, "Chat not found.")
    return row


def _turn(m: Message) -> dict:
    role = "customer" if m.sender_type == "customer" else ("human" if m.sender_type == "human" else "assistant")
    turn = {"role": role, "text": m.text or "", "at": m.created_at.isoformat() if m.created_at else None}
    if m.sender_name:
        turn["by"] = m.sender_name
    return turn


def _item(db, conv: Conversation, name, phone, with_transcript: bool = False) -> dict:
    msgs = db.scalars(select(Message).where(Message.conversation_id == conv.id).order_by(Message.id)).all()
    last = msgs[-1] if msgs else None
    data = {"id": conv.id, "lead_id": conv.lead_id, "lead_name": name, "lead_phone": phone,
            "status": "Chat" if conv.status == "open" else "Completed",
            "last_text": (last.text or "") if last else "",
            "last_at": (last.created_at.isoformat() if last and last.created_at else (conv.created_at.isoformat() if conv.created_at else None)),
            "message_count": len(msgs), "ai_paused": bool(conv.ai_paused)}
    if with_transcript:
        data["transcript"] = [_turn(m) for m in msgs]
        data["summary"] = conv.summary
    return data


@router.get("/chats")
def chats(agent_id: int = Depends(workspace)):
    with get_db() as db:
        rows = db.execute(select(Conversation, Lead.name, Lead.phone).outerjoin(Lead, Lead.id == Conversation.lead_id)
                          .where(Conversation.agent_id == agent_id, Conversation.channel == "instagram")
                          .order_by(Conversation.id.desc()).limit(200)).all()
        return {"items": [_item(db, c, n, p) for c, n, p in rows]}


@router.get("/chats/{chat_id}")
def chat(chat_id: int, agent_id: int = Depends(workspace)):
    with get_db() as db:
        c, n, p = _chat(db, agent_id, chat_id)
        return _item(db, c, n, p, with_transcript=True)


@router.post("/chats/{chat_id}/reply")
def reply(chat_id: int, body: Reply, request: Request, agent_id: int = Depends(workspace)):
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Reply text cannot be blank.")
    with get_db() as db:
        _chat(db, agent_id, chat_id)
    try:
        return ig.human_reply(chat_id, text, actor(request))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/chats/{chat_id}/resume")
def resume(chat_id: int, agent_id: int = Depends(workspace)):
    with get_db() as db:
        _chat(db, agent_id, chat_id)
    ig.set_ai_paused(chat_id, False)
    return {"ok": True}
