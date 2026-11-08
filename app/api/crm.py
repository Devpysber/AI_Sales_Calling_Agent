"""
CRM records (companies, contacts, deals, tasks, templates, campaigns, automation rules), global search,
the communication hub, the AI assistant, and the public website's demo-request form.
"""

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.deps import require_roles, workspace
from app.core import store
from app.core.auth import actor
from app.models.crm import DEAL_STAGES
from app.services import assistant, campaigns, crm_records, events, rules

router = APIRouter(prefix="/api/agents/{agent_id}/crm", tags=["crm"])
public = APIRouter(prefix="/api/public", tags=["public"])
KINDS = tuple(crm_records.MODELS)
# Roles allowed to change each kind of record (admins always can; viewers never can).
WRITERS = {
    "rules": ("ai_ops", "sales_manager"), "campaigns": ("ai_ops", "sales_manager"), "templates": ("ai_ops", "sales_manager", "sales_exec"),
}
DEFAULT_WRITERS = ("sales_manager", "sales_exec", "ai_ops")


def _kind(kind: str) -> str:
    if kind not in KINDS:
        raise HTTPException(404, "Unknown record type.")
    return kind


def _can_write(request: Request, kind: str):
    require_roles(*WRITERS.get(kind, DEFAULT_WRITERS))(request)


def _fail(e: Exception):
    if isinstance(e, LookupError):
        raise HTTPException(404, str(e).strip("'\""))
    raise HTTPException(400, str(e))


# ---------------- fixed routes first (they would otherwise match /{kind}) ----------------

@router.get("/meta")
def meta(agent_id: int = Depends(workspace)):
    return {"deal_stages": DEAL_STAGES, "triggers": rules.TRIGGERS, "actions": rules.ACTIONS, "ops": rules.OPS,
            "tasks": crm_records.task_summary(agent_id), "deals": crm_records.deal_summary(agent_id)}


@router.get("/search")
def search(q: str = Query("", max_length=120), agent_id: int = Depends(workspace)):
    return {"results": crm_records.search(agent_id, q)}


COMM_TYPES = ("email.", "call.", "whatsapp.", "lead.details_sent", "campaign.", "meeting.")


@router.get("/inbox")
def inbox(channel: str = "", lead_id: int | None = None, limit: int = Query(100, le=300), agent_id: int = Depends(workspace)):
    """Communication hub: one timeline of calls, emails, WhatsApp messages and campaigns."""
    from sqlalchemy import or_, select
    from app.core.database import get_db
    from app.models.event import Event
    prefixes = [p for p in COMM_TYPES if not channel or p.startswith(channel)]
    with get_db() as db:
        q = select(Event).where(Event.agent_id == agent_id, or_(*[Event.type.like(p + "%") for p in prefixes]))
        if lead_id:
            q = q.where(Event.lead_id == lead_id)
        return {"items": [e.to_dict() for e in db.scalars(q.order_by(Event.created_at.desc()).limit(limit))]}


class Conversion(BaseModel):
    deal_value: float = Field(0, ge=0)
    deal_title: str = ""


@router.post("/leads/{lead_id}/convert")
def convert(lead_id: int, request: Request, body: Conversion = Body(default_factory=Conversion), agent_id: int = Depends(workspace)):
    _can_write(request, "contacts")
    try:
        return crm_records.convert_lead(agent_id, lead_id, body.deal_value, body.deal_title, actor=actor(request))
    except (LookupError, ValueError) as e:
        _fail(e)


@router.get("/leads/{lead_id}/related")
def related(lead_id: int, agent_id: int = Depends(workspace)):
    return crm_records.related(agent_id, lead_id)


class Segment(BaseModel):
    segment: dict = {}
    channel: str = "call"


@router.post("/campaigns/preview")
def campaign_preview(body: Segment, agent_id: int = Depends(workspace)):
    return campaigns.preview(agent_id, body.segment, body.channel)


@router.post("/campaigns/{campaign_id}/run")
def campaign_run(campaign_id: int, request: Request, agent_id: int = Depends(workspace)):
    _can_write(request, "campaigns")
    try:
        return campaigns.run(agent_id, campaign_id, actor=actor(request))
    except (LookupError, ValueError) as e:
        _fail(e)


@router.post("/campaigns/{campaign_id}/cancel")
def campaign_cancel(campaign_id: int, request: Request, agent_id: int = Depends(workspace)):
    _can_write(request, "campaigns")
    try:
        return campaigns.cancel(agent_id, campaign_id)
    except (LookupError, ValueError) as e:
        _fail(e)


class RuleTest(BaseModel):
    lead_id: int | None = None
    run: bool = False


@router.post("/rules/{rule_id}/test")
def rule_test(rule_id: int, body: RuleTest, request: Request, agent_id: int = Depends(workspace)):
    """Dry run against a lead (does the rule match?); with run=true the actions really execute."""
    if body.run:
        _can_write(request, "rules")
    try:
        rule = crm_records.get(agent_id, "rules", rule_id)
    except LookupError as e:
        _fail(e)
    return rules.execute(rule, agent_id, body.lead_id, {}, dry_run=not body.run)


class Chat(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[dict] = []


@router.post("/assistant/chat")
def assistant_chat(body: Chat, request: Request, agent_id: int = Depends(workspace)):
    if store.rate_limited(f"assistant:{actor(request)}", limit=30, window=300):
        raise HTTPException(429, "Too many questions in a short time. Wait a few minutes.")
    return assistant.chat(agent_id, body.message, body.history, actor=actor(request))


@router.post("/assistant/execute")
def assistant_execute(request: Request, action: dict = Body(...), agent_id: int = Depends(workspace)):
    _can_write(request, "tasks")
    try:
        return assistant.execute(agent_id, action, actor=actor(request))
    except (LookupError, ValueError) as e:
        _fail(e)


# ---------------- generic CRUD ----------------

@router.get("/{kind}")
def list_records(kind: str, request: Request, search: str = "", agent_id: int = Depends(workspace)):
    filters = {k: v for k, v in request.query_params.items() if k not in ("search",)}
    return crm_records.list_records(agent_id, _kind(kind), search, filters)


@router.post("/{kind}")
def create(kind: str, request: Request, data: dict = Body(...), agent_id: int = Depends(workspace)):
    _can_write(request, _kind(kind))
    try:
        return crm_records.create(agent_id, kind, data, actor=actor(request))
    except (LookupError, ValueError, TypeError) as e:
        _fail(e)


@router.get("/{kind}/{record_id}")
def get(kind: str, record_id: int, agent_id: int = Depends(workspace)):
    try:
        return crm_records.get(agent_id, _kind(kind), record_id)
    except LookupError as e:
        _fail(e)


@router.patch("/{kind}/{record_id}")
def update(kind: str, record_id: int, request: Request, data: dict = Body(...), agent_id: int = Depends(workspace)):
    _can_write(request, _kind(kind))
    try:
        return crm_records.update(agent_id, kind, record_id, data, actor=actor(request))
    except (LookupError, ValueError, TypeError) as e:
        _fail(e)


@router.delete("/{kind}/{record_id}")
def delete(kind: str, record_id: int, request: Request, agent_id: int = Depends(workspace)):
    _can_write(request, _kind(kind))
    try:
        crm_records.delete(agent_id, kind, record_id, actor=actor(request))
    except LookupError as e:
        _fail(e)
    return {"ok": True}


# ---------------- public website ----------------

class DemoRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=255)
    phone: str = Field("", max_length=32)
    company: str = Field("", max_length=160)
    message: str = Field("", max_length=2000)
    kind: str = Field("demo", pattern="^(demo|contact|sales)$")


@public.post("/demo-request")
def demo_request(body: DemoRequest, request: Request):
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0].strip()
    if store.rate_limited(f"demo:{ip}", limit=5, window=3600):
        raise HTTPException(429, "Too many requests. Try again later.")
    label = {"demo": "Demo request", "contact": "Contact message", "sales": "Sales enquiry"}[body.kind]
    events.record("website.enquiry", f"{label} from {body.name}", body.message or None, actor="website", data=body.model_dump())
    from app.services.notification_service import notify_admin
    try:
        notify_admin(f"{label}: {body.name} ({body.company or 'no company'})",
                     f"Name: {body.name}\nEmail: {body.email}\nPhone: {body.phone}\nCompany: {body.company}\n\n{body.message}")
    except Exception:  # noqa: BLE001 - the enquiry is already in the activity log
        pass
    return {"ok": True, "message": "Thanks! Our team will get back to you within one business day."}
