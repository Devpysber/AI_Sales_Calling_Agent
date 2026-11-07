"""Meta WhatsApp Cloud API webhook: verification handshake and inbound messages (no session auth; signed by Meta)."""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import workspace
from app.core.auth import actor
from app.core.config import settings
from app.core.database import get_db
from app.models.channel import Conversation, Message
from app.models.crm import MessageTemplate
from app.models.lead import Lead
from app.services import whatsapp_cloud_service as wa

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
        # enqueue_inbound: RPUSH to the durable Redis queue when configured, else process inline
        # (sync function: Starlette runs it in a worker thread either way).
        background.add_task(wa.enqueue_inbound, msg)
    return {"ok": True}


class Reply(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)


def _chat(db, agent_id: int, chat_id: int):
    row = db.execute(select(Conversation, Lead.name, Lead.phone).outerjoin(Lead, Lead.id == Conversation.lead_id)
                     .where(Conversation.id == chat_id, Conversation.agent_id == agent_id,
                            Conversation.channel == "whatsapp")).first()
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
            "message_count": len(msgs), "ai_paused": bool(conv.ai_paused),
            "awaiting_reply": bool(last and last.sender_type == "customer")}
    if with_transcript:
        data["transcript"] = [_turn(m) for m in msgs]
        data["summary"] = conv.summary
    return data


@router.get("/chats")
def chats(agent_id: int = Depends(workspace)):
    with get_db() as db:
        rows = db.execute(select(Conversation, Lead.name, Lead.phone).outerjoin(Lead, Lead.id == Conversation.lead_id)
                          .where(Conversation.agent_id == agent_id, Conversation.channel == "whatsapp")
                          .order_by(Conversation.id.desc()).limit(200)).all()
        return {"items": [_item(db, c, n, p) for c, n, p in rows]}


@router.get("/chats/{chat_id}")
def chat(chat_id: int, agent_id: int = Depends(workspace)):
    with get_db() as db:
        c, n, p = _chat(db, agent_id, chat_id)
        return _item(db, c, n, p, with_transcript=True)


@router.get("/chats/by-lead/{lead_id}")
def chat_by_lead(lead_id: int, agent_id: int = Depends(workspace)):
    """The WhatsApp Cloud API chat (if any) for a given lead, for that lead's detail page."""
    with get_db() as db:
        row = db.execute(select(Conversation, Lead.name, Lead.phone).outerjoin(Lead, Lead.id == Conversation.lead_id)
                          .where(Conversation.lead_id == lead_id, Conversation.agent_id == agent_id,
                                 Conversation.channel == "whatsapp")
                          .order_by(Conversation.id.desc())).first()
        if row is None:
            raise HTTPException(404, "No WhatsApp Cloud API chat for this lead.")
        c, n, p = row
        return _item(db, c, n, p, with_transcript=True)


@router.post("/chats/{chat_id}/reply")
def reply(chat_id: int, body: Reply, request: Request, agent_id: int = Depends(workspace)):
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Reply text cannot be blank.")
    with get_db() as db:
        _chat(db, agent_id, chat_id)
    try:
        return wa.human_reply(chat_id, text, actor(request))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/chats/{chat_id}/resume")
def resume(chat_id: int, agent_id: int = Depends(workspace)):
    with get_db() as db:
        _chat(db, agent_id, chat_id)
    wa.set_ai_paused(chat_id, False)
    return {"ok": True}


# ---------------- WhatsApp message templates (Meta-approved, for the 24h window) ----------------

class TemplateIn(BaseModel):
    name: str
    whatsapp_template_name: str
    whatsapp_template_language: str = "en_US"
    body: str = ""
    is_meta_approved: bool = True


class TemplateSend(BaseModel):
    lead_id: int | None = None
    phone: str | None = None
    variables: list[str] = Field(default_factory=list)


@router.get("/templates")
def list_templates(agent_id: int = Depends(workspace)):
    with get_db() as db:
        rows = db.query(MessageTemplate).filter(MessageTemplate.agent_id == agent_id,
                                                 MessageTemplate.channel == "whatsapp").order_by(MessageTemplate.id).all()
        return [r.to_dict() for r in rows]


@router.post("/templates")
def create_template(body: TemplateIn, agent_id: int = Depends(workspace)):
    with get_db() as db:
        row = MessageTemplate(agent_id=agent_id, name=body.name, channel="whatsapp", body=body.body,
                              whatsapp_template_name=body.whatsapp_template_name,
                              whatsapp_template_language=body.whatsapp_template_language,
                              is_meta_approved=body.is_meta_approved)
        db.add(row)
        db.flush()
        return row.to_dict()


@router.post("/templates/{template_id}/send")
def send_template(template_id: int, body: TemplateSend, agent_id: int = Depends(workspace)):
    with get_db() as db:
        row = db.query(MessageTemplate).filter(MessageTemplate.agent_id == agent_id,
                                                MessageTemplate.id == template_id,
                                                MessageTemplate.channel == "whatsapp").first()
        if not row:
            raise HTTPException(404, "Template not found.")
        template_name, language = row.whatsapp_template_name, row.whatsapp_template_language
    phone = body.phone
    if not phone and body.lead_id:
        with get_db() as db:
            phone = db.scalar(select(Lead.phone).where(Lead.id == body.lead_id))
    if not phone:
        raise HTTPException(400, "A lead_id with a phone number, or a phone, is required.")
    import asyncio
    result = asyncio.run(wa.send_whatsapp_template(phone, template_name, language, body.variables,
                                                    agent_id=agent_id, lead_id=body.lead_id))
    if not result.get("ok"):
        raise HTTPException(400, result.get("error") or "WhatsApp template send failed")
    return result
