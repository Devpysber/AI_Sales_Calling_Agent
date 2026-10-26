"""
WhatsApp: the bridge's inbound webhook and the dashboard's proxy to the bridge.
"""

import asyncio
import hmac

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import workspace
from app.core.config import settings
from app.core.database import get_db
from app.core.logging import get_logger
from app.models.lead import Lead
from app.models.whatsapp_message import WhatsAppMessage
from app.services import agents, events, whatsapp_agent, whatsapp_service
from app.services.crm_service import CRMService

log = get_logger(__name__)
router = APIRouter(tags=["whatsapp"])
LID_TAG = "whatsapp_lid"


# `from` is a Python keyword: the bridge sends it under that name and the handler maps it to from_
class InboundIn(BaseModel):
    agent_id: int
    from_: str | None = None   # absent for an @lid sender whose number the bridge could not resolve
    chat_id: str | None = None
    name: str | None = None
    text: str
    message_id: str | None = None
    timestamp: int | None = None


def _bridge_ok(secret: str | None) -> bool:
    if settings.wa_bridge_secret:
        return hmac.compare_digest(secret or "", settings.wa_bridge_secret)
    return not settings.is_production  # an open webhook would let anyone run the LLM on our bill


def _find_lead(agent_id: int, digits: str) -> int | None:
    """The agent's lead for this number, matched on the last 10 digits like the CRM's phone normalisation."""
    with get_db() as db:
        return db.scalar(select(Lead.id).where(Lead.agent_id == agent_id, Lead.phone.like(f"%{digits[-10:]}"))
                         .order_by(Lead.id.desc()).limit(1))


def _lead_by_chat_id(agent_id: int, chat_id: str) -> int | None:
    """The lead an earlier message from this WhatsApp id was filed under."""
    with get_db() as db:
        return db.scalar(select(WhatsAppMessage.lead_id).where(
            WhatsAppMessage.agent_id == agent_id, WhatsAppMessage.chat_id == chat_id, WhatsAppMessage.lead_id.is_not(None))
            .order_by(WhatsAppMessage.id.desc()).limit(1))


def _create_lid_lead(agent_id: int, name: str | None) -> int:
    """A lead for a sender with no known number: phone stays empty (so nothing can dial it) until a person fills it in."""
    with get_db() as db:
        lead = Lead(agent_id=agent_id, name=(name or "").strip() or None, phone="", source="whatsapp", status="New",
                    language="en-IN", retry_count=0, tags=LID_TAG)
        db.add(lead)
        db.flush()
        lead_id = lead.id
    events.record("lead.created", f"Lead added: {name or 'WhatsApp contact'} (no number)", agent_id=agent_id, lead_id=lead_id, actor="whatsapp")
    return lead_id


@router.post("/api/whatsapp/inbound")
async def inbound(body: dict, background: BackgroundTasks, x_bridge_secret: str | None = Header(None)):
    if not _bridge_ok(x_bridge_secret):
        raise HTTPException(401, "Bad bridge secret.")
    try:
        data = InboundIn.model_validate({**body, "from_": body.get("from")})
    except Exception:
        raise HTTPException(422, "agent_id, from and text are required.")
    digits = "".join(c for c in (data.from_ or "") if c.isdigit())
    chat_id = (data.chat_id or "").strip()[:64] or None
    text = data.text.strip()
    if not (digits or chat_id) or not text or not agents.exists(data.agent_id):
        return {"ok": True, "ignored": True}
    if data.message_id:
        with get_db() as db:
            if db.scalar(select(WhatsAppMessage.id).where(WhatsAppMessage.wa_message_id == data.message_id)):
                return {"ok": True, "duplicate": True}
    lead_id = _find_lead(data.agent_id, digits) if digits else None
    if lead_id is None and chat_id:
        lead_id = _lead_by_chat_id(data.agent_id, chat_id)
    crm = CRMService(data.agent_id)
    if lead_id is None and not digits:
        lead_id = _create_lid_lead(data.agent_id, data.name)
    elif lead_id is None:
        try:
            lead_id = crm.create({"name": (data.name or "").strip() or None, "phone": digits, "source": "whatsapp", "status": "New"},
                                 actor="whatsapp")["id"]
        except ValueError:
            log.warning("WhatsApp number %s could not be made a lead", digits)
    # Replies to an @lid sender go to their chat id; everyone else is reached by number.
    target = chat_id if chat_id and chat_id.endswith("@lid") and not digits else digits
    try:
        with get_db() as db:
            db.add(WhatsAppMessage(agent_id=data.agent_id, lead_id=lead_id, phone=target, chat_id=chat_id, direction="in",
                                   text=text[:4000], wa_message_id=data.message_id or None, provider="native_qr"))
    except IntegrityError:
        return {"ok": True, "duplicate": True}  # the bridge retried a message we already hold
    if lead_id:
        crm.update(lead_id, {}, actor="system", touch=True)
        events.record("whatsapp.received", f"WhatsApp from {'+' + digits if digits else 'a contact with no number'}", text[:120], agent_id=data.agent_id, lead_id=lead_id, actor="lead")
        background.add_task(whatsapp_agent.handle_inbound, data.agent_id, lead_id, target)
    return {"ok": True}


# ---------------- dashboard (per agent workspace) ----------------

class SendIn(BaseModel):
    to: str
    text: str
    lead_id: int | None = None   # lets a lead with no number be reached on its chat id


@router.get("/api/agents/{agent_id}/whatsapp/status")
async def status(agent_id: int = Depends(workspace)):
    return await whatsapp_service.bridge_request(agent_id, "GET", "status")


@router.get("/api/agents/{agent_id}/whatsapp/qr")
async def qr(agent_id: int = Depends(workspace)):
    return await whatsapp_service.bridge_request(agent_id, "GET", "qr")


@router.post("/api/agents/{agent_id}/whatsapp/disconnect")
async def disconnect(agent_id: int = Depends(workspace)):
    return await whatsapp_service.bridge_request(agent_id, "POST", "disconnect")


def _chat_id_of(agent_id: int, lead_id: int) -> str | None:
    with get_db() as db:
        return db.scalar(select(WhatsAppMessage.chat_id).where(
            WhatsAppMessage.agent_id == agent_id, WhatsAppMessage.lead_id == lead_id, WhatsAppMessage.chat_id.is_not(None))
            .order_by(WhatsAppMessage.id.desc()).limit(1))


@router.post("/api/agents/{agent_id}/whatsapp/send")
async def send(body: SendIn, agent_id: int = Depends(workspace)):
    if not body.text.strip():
        raise HTTPException(422, "Message text is required.")
    to = body.to
    if not any(c.isdigit() for c in to) and body.lead_id:
        to = await asyncio.to_thread(_chat_id_of, agent_id, body.lead_id) or to
    # A person typing in the dashboard: the agent then stays out of that chat for a while.
    result = await whatsapp_service.send_whatsapp_message(to=to, text=body.text.strip(), agent_id=agent_id, lead_id=body.lead_id, via="dashboard")
    if not result.get("ok"):
        # A 200 with ok=False looked like success to the page, which cleared the box and showed nothing.
        raise HTTPException(409 if result.get("refused") else 502, result.get("error") or "WhatsApp message was not sent.")
    return result


@router.get("/api/agents/{agent_id}/leads/{lead_id}/whatsapp")
def lead_chat(lead_id: int, agent_id: int = Depends(workspace)):
    if not CRMService(agent_id).get(lead_id):
        raise HTTPException(404, "Lead not found.")
    with get_db() as db:
        rows = db.scalars(select(WhatsAppMessage).where(WhatsAppMessage.agent_id == agent_id, WhatsAppMessage.lead_id == lead_id)
                          .order_by(WhatsAppMessage.id.desc()).limit(100)).all()
    return {"items": [r.to_dict() for r in reversed(rows)]}
