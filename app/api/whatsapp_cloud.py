"""Meta WhatsApp Cloud API webhook: verification handshake and inbound messages (no session auth; signed by Meta)."""

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, UploadFile
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
    # Delivery/read receipts for our own outbound sends: a plain DB write, no per-contact ordering
    # needed, so handled synchronously here rather than through the inbound-message queue.
    for upd in wa.parse_status_updates(payload):
        wa.apply_status_update(upd["wamid"], upd["status"], upd.get("error"))
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
    if role != "customer":
        turn["status"] = m.status
        if m.error:
            turn["error"] = m.error
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


# ---------------- WhatsApp message templates (real Meta message_templates shape) ----------------

import json as _json

MAX_BUTTONS = 3


class TemplateIn(BaseModel):
    name: str
    language: str = "en_US"
    category: str
    components: list[dict] = Field(default_factory=list)

    def validated_components(self) -> tuple[list[dict], str]:
        """Returns (components, header_format). Raises ValueError on an invalid shape."""
        bodies = [c for c in self.components if (c.get("type") or "").upper() == "BODY"]
        if len(bodies) != 1 or not str(bodies[0].get("text") or "").strip():
            raise ValueError("Exactly one BODY component with non-empty text is required.")
        headers = [c for c in self.components if (c.get("type") or "").upper() == "HEADER"]
        header_format = (headers[0].get("format") or "TEXT").upper() if headers else "NONE"
        buttons_comp = [c for c in self.components if (c.get("type") or "").upper() == "BUTTONS"]
        if buttons_comp and len(buttons_comp[0].get("buttons") or []) > MAX_BUTTONS:
            raise ValueError(f"At most {MAX_BUTTONS} buttons are allowed.")
        return self.components, header_format


class TemplatePatch(BaseModel):
    name: str | None = None
    language: str | None = None
    category: str | None = None
    components: list[dict] | None = None


class TemplateSend(BaseModel):
    lead_id: int | None = None
    phone: str | None = None
    variables: dict[str, list[str]] = Field(default_factory=dict)


def _build_send_components(stored: list[dict], variables: dict[str, list[str]]) -> list[dict]:
    """Build Meta's `template.components` send-time parameters array from the stored
    create-time `components` (header/body/footer/buttons) plus caller-supplied variable values,
    keyed by component type: {"header": [...], "body": [...], "button_0": [...], ...}."""
    out: list[dict] = []
    for comp in stored or []:
        ctype = (comp.get("type") or "").upper()
        if ctype == "BODY":
            values = variables.get("body") or []
            if values:
                out.append({"type": "body", "parameters": [{"type": "text", "text": str(v)} for v in values]})
        elif ctype == "HEADER":
            values = variables.get("header") or []
            fmt = (comp.get("format") or "TEXT").upper()
            if values:
                if fmt == "TEXT":
                    out.append({"type": "header", "parameters": [{"type": "text", "text": str(values[0])}]})
                else:
                    out.append({"type": "header", "parameters": [{"type": fmt.lower(), fmt.lower(): {"link": str(values[0])}}]})
        elif ctype == "BUTTONS":
            for i, btn in enumerate(comp.get("buttons") or []):
                values = variables.get(f"button_{i}") or []
                if values and (btn.get("type") or "").upper() == "URL":
                    out.append({"type": "button", "sub_type": "url", "index": str(i),
                               "parameters": [{"type": "text", "text": str(values[0])}]})
    return out


class EmbeddedSignupIn(BaseModel):
    code: str
    phone_number_id: str
    waba_id: str


@router.post("/embedded-signup")
async def embedded_signup(body: EmbeddedSignupIn, agent_id: int = Depends(workspace)):
    result = await wa.complete_embedded_signup(body.code, body.phone_number_id, body.waba_id)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error") or "Embedded signup failed")
    from app.services import agents
    agents.update_profile(agent_id, {
        "whatsapp_phone_number_id": result["phone_number_id"],
        "whatsapp_business_account_id": result["waba_id"],
        "whatsapp_business_token": result["access_token"],
    }, actor="admin")
    return {"ok": True, "phone_number_id": result["phone_number_id"], "waba_id": result["waba_id"]}


@router.get("/templates")
def list_templates(agent_id: int = Depends(workspace)):
    with get_db() as db:
        rows = db.query(MessageTemplate).filter(MessageTemplate.agent_id == agent_id,
                                                 MessageTemplate.channel == "whatsapp").order_by(MessageTemplate.id).all()
        return [r.to_dict() for r in rows]


@router.post("/templates")
def create_template(body: TemplateIn, agent_id: int = Depends(workspace)):
    try:
        components, header_format = body.validated_components()
    except ValueError as e:
        raise HTTPException(400, str(e))
    body_text = next(c.get("text") or "" for c in components if (c.get("type") or "").upper() == "BODY")
    with get_db() as db:
        row = MessageTemplate(agent_id=agent_id, name=body.name, channel="whatsapp", body=body_text,
                              whatsapp_template_name=body.name, whatsapp_template_language=body.language,
                              category=body.category, components_json=_json.dumps(components),
                              header_format=header_format, status="draft", is_meta_approved=False)
        db.add(row)
        db.flush()
        return row.to_dict()


@router.post("/templates/media")
async def upload_template_media(agent_id: int = Depends(workspace), file: UploadFile = File(...)):
    MAX_BYTES = 16 * 1024 * 1024  # Meta's own cap for template header media
    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(400, "File is larger than 16 MB.")
    from app.services.whatsapp_cloud_service import upload_template_header_media
    result = await upload_template_header_media(data, file.content_type or "application/octet-stream", agent_id=agent_id)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error") or "Upload failed")
    return {"handle": result["handle"]}


@router.patch("/templates/{template_id}")
def patch_template(template_id: int, body: TemplatePatch, agent_id: int = Depends(workspace)):
    with get_db() as db:
        row = db.query(MessageTemplate).filter(MessageTemplate.agent_id == agent_id,
                                                MessageTemplate.id == template_id,
                                                MessageTemplate.channel == "whatsapp").first()
        if not row:
            raise HTTPException(404, "Template not found.")
        if row.status != "draft":
            raise HTTPException(400, "Only draft templates can be edited; a submitted template must be "
                                      "resubmitted as a new one.")
        name = body.name if body.name is not None else row.whatsapp_template_name
        language = body.language if body.language is not None else row.whatsapp_template_language
        category = body.category if body.category is not None else row.category
        components = body.components if body.components is not None else (
            _json.loads(row.components_json) if row.components_json else [])
        try:
            components, header_format = TemplateIn(name=name, language=language, category=category,
                                                    components=components).validated_components()
        except ValueError as e:
            raise HTTPException(400, str(e))
        body_text = next(c.get("text") or "" for c in components if (c.get("type") or "").upper() == "BODY")
        row.name = name
        row.whatsapp_template_name = name
        row.whatsapp_template_language = language
        row.category = category
        row.components_json = _json.dumps(components)
        row.header_format = header_format
        row.body = body_text
        db.flush()
        return row.to_dict()


@router.post("/templates/{template_id}/submit")
def submit_template(template_id: int, agent_id: int = Depends(workspace)):
    with get_db() as db:
        row = db.query(MessageTemplate).filter(MessageTemplate.agent_id == agent_id,
                                                MessageTemplate.id == template_id,
                                                MessageTemplate.channel == "whatsapp").first()
        if not row:
            raise HTTPException(404, "Template not found.")
        name, language, category = row.whatsapp_template_name, row.whatsapp_template_language, row.category
        components = _json.loads(row.components_json) if row.components_json else []
    import asyncio
    result = asyncio.run(wa.create_meta_template(name, language, category, components, agent_id=agent_id))
    if not result.get("ok"):
        raise HTTPException(400, result.get("error") or "WhatsApp template submission failed")
    with get_db() as db:
        row = db.get(MessageTemplate, template_id)
        row.status = "pending"
        row.whatsapp_template_id = str((result.get("data") or {}).get("id") or "")
        db.flush()
        return row.to_dict()


@router.get("/templates/{template_id}/status")
def sync_template_status(template_id: int, agent_id: int = Depends(workspace)):
    """Explicit "sync status" action: pulls the latest category/status/rejected_reason from Meta.
    Never polled automatically."""
    with get_db() as db:
        row = db.query(MessageTemplate).filter(MessageTemplate.agent_id == agent_id,
                                                MessageTemplate.id == template_id,
                                                MessageTemplate.channel == "whatsapp").first()
        if not row:
            raise HTTPException(404, "Template not found.")
        if not row.whatsapp_template_id:
            raise HTTPException(400, "Template has not been submitted to Meta yet.")
        whatsapp_template_id = row.whatsapp_template_id
    import asyncio
    result = asyncio.run(wa.fetch_meta_template(whatsapp_template_id, agent_id=agent_id))
    if not result.get("ok"):
        raise HTTPException(400, result.get("error") or "WhatsApp status sync failed")
    data = result.get("data") or {}
    status = str(data.get("status") or "").lower()
    with get_db() as db:
        row = db.get(MessageTemplate, template_id)
        if status:
            row.status = status
        if data.get("category"):
            row.category = data["category"]
        row.rejected_reason = data.get("rejected_reason")
        row.is_meta_approved = status == "approved"
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
        stored_components = _json.loads(row.components_json) if row.components_json else []
    phone = body.phone
    if not phone and body.lead_id:
        with get_db() as db:
            phone = db.scalar(select(Lead.phone).where(Lead.id == body.lead_id))
    if not phone:
        raise HTTPException(400, "A lead_id with a phone number, or a phone, is required.")
    send_components = _build_send_components(stored_components, body.variables)
    import asyncio
    result = asyncio.run(wa.send_whatsapp_template(phone, template_name, language, agent_id=agent_id,
                                                    lead_id=body.lead_id, components=send_components))
    if not result.get("ok"):
        raise HTTPException(400, result.get("error") or "WhatsApp template send failed")
    return result


# ---------------- WhatsApp Flows (native multi-screen forms, navigate-only) ----------------

from app.models.crm import WhatsAppFlow


class FlowIn(BaseModel):
    name: str
    category: str = "LEAD_GENERATION"
    screens: list[dict] = Field(default_factory=list)


class FlowSend(BaseModel):
    lead_id: int | None = None
    phone: str | None = None
    body_text: str = "Let's get started"
    cta_text: str = "Start"


def _flow_row(db, agent_id: int, flow_id: int) -> WhatsAppFlow:
    row = db.query(WhatsAppFlow).filter(WhatsAppFlow.agent_id == agent_id, WhatsAppFlow.id == flow_id).first()
    if not row:
        raise HTTPException(404, "Flow not found.")
    return row


@router.get("/flows")
def list_flows(agent_id: int = Depends(workspace)):
    with get_db() as db:
        rows = db.query(WhatsAppFlow).filter(WhatsAppFlow.agent_id == agent_id).order_by(WhatsAppFlow.id).all()
        return [r.to_dict() for r in rows]


@router.get("/flows/{flow_id}")
def get_flow(flow_id: int, agent_id: int = Depends(workspace)):
    with get_db() as db:
        return _flow_row(db, agent_id, flow_id).to_dict()


@router.post("/flows")
def create_flow(body: FlowIn, agent_id: int = Depends(workspace)):
    if not body.screens:
        raise HTTPException(400, "At least one screen is required.")
    with get_db() as db:
        row = WhatsAppFlow(agent_id=agent_id, name=body.name, category=body.category,
                           screens_json=_json.dumps(body.screens), status="draft")
        db.add(row)
        db.flush()
        return row.to_dict()


@router.delete("/flows/{flow_id}")
def delete_flow(flow_id: int, agent_id: int = Depends(workspace)):
    with get_db() as db:
        row = _flow_row(db, agent_id, flow_id)
        if row.status != "draft":
            raise HTTPException(400, "Only a draft flow can be deleted.")
        db.delete(row)
    return {"ok": True}


@router.post("/flows/{flow_id}/publish")
def publish_flow(flow_id: int, agent_id: int = Depends(workspace)):
    """Create + upload + publish on Meta, in sequence; 400 if the WABA id isn't configured for this agent."""
    with get_db() as db:
        row = _flow_row(db, agent_id, flow_id)
        name, category, screens = row.name, row.category, _json.loads(row.screens_json)
        meta_flow_id = row.meta_flow_id
    import asyncio
    if not meta_flow_id:
        created = asyncio.run(wa.create_meta_flow(agent_id, name, category))
        if not created.get("ok"):
            raise HTTPException(400, created.get("error") or "WhatsApp flow creation failed")
        meta_flow_id = str((created.get("data") or {}).get("id") or "")
        with get_db() as db:
            row = db.get(WhatsAppFlow, flow_id)
            row.meta_flow_id = meta_flow_id
            row.status = "created"
            db.flush()
    uploaded = asyncio.run(wa.upload_flow_json(agent_id, meta_flow_id, screens))
    if not uploaded.get("ok"):
        raise HTTPException(400, uploaded.get("error") or "WhatsApp flow JSON upload failed")
    published = asyncio.run(wa.publish_meta_flow(agent_id, meta_flow_id))
    if not published.get("ok"):
        raise HTTPException(400, published.get("error") or "WhatsApp flow publish failed")
    with get_db() as db:
        row = db.get(WhatsAppFlow, flow_id)
        row.status = "published"
        db.flush()
        return row.to_dict()


@router.post("/flows/{flow_id}/send")
def send_flow(flow_id: int, body: FlowSend, agent_id: int = Depends(workspace)):
    with get_db() as db:
        row = _flow_row(db, agent_id, flow_id)
        if row.status != "published" or not row.meta_flow_id:
            raise HTTPException(400, "Flow must be published before it can be sent.")
        meta_flow_id = row.meta_flow_id
        first_screen_id = (_json.loads(row.screens_json) or [{}])[0].get("id") or "WELCOME"
    phone = body.phone
    if not phone and body.lead_id:
        with get_db() as db:
            phone = db.scalar(select(Lead.phone).where(Lead.id == body.lead_id))
    if not phone:
        raise HTTPException(400, "A lead_id with a phone number, or a phone, is required.")
    result = wa.send_whatsapp_flow(phone, agent_id, body.lead_id, meta_flow_id, body.body_text, body.cta_text,
                                   first_screen_id)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error") or "WhatsApp flow send failed")
    return result
