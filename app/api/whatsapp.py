"""
WhatsApp: the bridge's inbound webhook and the dashboard's proxy to the bridge.
"""

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


# `from` is a Python keyword: the bridge sends it under that name and the handler maps it to from_
class InboundIn(BaseModel):
    agent_id: int
    from_: str
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


@router.post("/api/whatsapp/inbound")
async def inbound(body: dict, background: BackgroundTasks, x_bridge_secret: str | None = Header(None)):
    if not _bridge_ok(x_bridge_secret):
        raise HTTPException(401, "Bad bridge secret.")
    try:
        data = InboundIn.model_validate({**body, "from_": body.get("from")})
    except Exception:
        raise HTTPException(422, "agent_id, from and text are required.")
    digits = "".join(c for c in data.from_ if c.isdigit())
    text = data.text.strip()
    if not digits or not text or not agents.exists(data.agent_id):
        return {"ok": True, "ignored": True}
    if data.message_id:
        with get_db() as db:
            if db.scalar(select(WhatsAppMessage.id).where(WhatsAppMessage.wa_message_id == data.message_id)):
                return {"ok": True, "duplicate": True}
    lead_id = _find_lead(data.agent_id, digits)
    crm = CRMService(data.agent_id)
    if lead_id is None:
        try:
            lead_id = crm.create({"name": (data.name or "").strip() or None, "phone": digits, "source": "whatsapp", "status": "New"},
                                 actor="whatsapp")["id"]
        except ValueError:
            log.warning("WhatsApp number %s could not be made a lead", digits)
    try:
        with get_db() as db:
            db.add(WhatsAppMessage(agent_id=data.agent_id, lead_id=lead_id, phone=digits, direction="in", text=text[:4000],
                                   wa_message_id=data.message_id or None, provider="native_qr"))
    except IntegrityError:
        return {"ok": True, "duplicate": True}  # the bridge retried a message we already hold
    if lead_id:
        crm.update(lead_id, {}, actor="system", touch=True)
        events.record("whatsapp.received", f"WhatsApp from +{digits}", text[:120], agent_id=data.agent_id, lead_id=lead_id, actor="lead")
        background.add_task(whatsapp_agent.handle_inbound, data.agent_id, lead_id, digits)
    return {"ok": True}


# ---------------- dashboard (per agent workspace) ----------------

class SendIn(BaseModel):
    to: str
    text: str


@router.get("/api/agents/{agent_id}/whatsapp/status")
async def status(agent_id: int = Depends(workspace)):
    return await whatsapp_service.bridge_request(agent_id, "GET", "status")


@router.get("/api/agents/{agent_id}/whatsapp/qr")
async def qr(agent_id: int = Depends(workspace)):
    return await whatsapp_service.bridge_request(agent_id, "GET", "qr")


@router.post("/api/agents/{agent_id}/whatsapp/disconnect")
async def disconnect(agent_id: int = Depends(workspace)):
    return await whatsapp_service.bridge_request(agent_id, "POST", "disconnect")


@router.post("/api/agents/{agent_id}/whatsapp/send")
async def send(body: SendIn, agent_id: int = Depends(workspace)):
    if not body.text.strip():
        raise HTTPException(422, "Message text is required.")
    # A person typing in the dashboard: the agent then stays out of that chat for a while.
    return await whatsapp_service.send_whatsapp_message(to=body.to, text=body.text.strip(), agent_id=agent_id, via="dashboard")


@router.get("/api/agents/{agent_id}/leads/{lead_id}/whatsapp")
def lead_chat(lead_id: int, agent_id: int = Depends(workspace)):
    if not CRMService(agent_id).get(lead_id):
        raise HTTPException(404, "Lead not found.")
    with get_db() as db:
        rows = db.scalars(select(WhatsAppMessage).where(WhatsAppMessage.agent_id == agent_id, WhatsAppMessage.lead_id == lead_id)
                          .order_by(WhatsAppMessage.id.desc()).limit(100)).all()
    return {"items": [r.to_dict() for r in reversed(rows)]}
