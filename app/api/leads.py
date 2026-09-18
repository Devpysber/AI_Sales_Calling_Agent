import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.deps import workspace
from app.core.auth import actor
from app.services import agents, events
from app.services.call_service import CallError, CallService
from app.services.crm_service import CRMService

router = APIRouter(prefix="/api/agents/{agent_id}/leads", tags=["leads"])
MAX_UPLOAD = 20 * 1024 * 1024


class LeadIn(BaseModel):
    name: str | None = None
    company: str | None = None
    phone: str
    email: str | None = None
    city: str | None = None
    language: str | None = "en-IN"
    source: str | None = "manual"
    tags: list[str] | None = None
    notes: str | None = None
    status: str | None = "New"


class LeadPatch(BaseModel):
    name: str | None = None
    company: str | None = None
    phone: str | None = None
    email: str | None = None
    city: str | None = None
    language: str | None = None
    source: str | None = None
    tags: list[str] | None = None
    notes: str | None = None
    do_not_call: bool | None = None
    status: str | None = None
    call_status: str | None = None
    qualification: str | None = None
    summary: str | None = None
    requirements: str | None = None
    objections: str | None = None
    follow_up_date: str | None = None
    meeting_at: str | None = None
    callback_at: str | None = None  # "YYYY-MM-DD HH:MM" IST: the agent calls at this time; "" clears it
    retry_count: int | None = None


class Ids(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=1000)


class QueueRequest(Ids):
    at: str | None = None  # "YYYY-MM-DDTHH:MM" or "YYYY-MM-DD HH:MM" IST; empty = as soon as possible


def scheduled_time(value: str | None) -> str | None:
    """Validate a user-picked call time (IST): must be in the future and within 60 days."""
    if not value:
        return None
    from datetime import datetime, timedelta
    from app.services.call_service import IST
    try:
        dt = datetime.strptime(value.strip().replace("T", " ")[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        raise HTTPException(400, "Pick a valid date and time.")
    now = datetime.now(IST).replace(tzinfo=None)
    if dt < now - timedelta(minutes=1):
        raise HTTPException(400, "That time has already passed.")
    if dt > now + timedelta(days=60):
        raise HTTPException(400, "Schedule within the next 60 days.")
    return dt.strftime("%Y-%m-%d %H:%M")


def meeting_time(value: str) -> str:
    """Validate a meeting time typed on the lead form (IST): must be in the future and within a year."""
    from datetime import datetime, timedelta
    from app.services.call_service import IST
    try:
        dt = datetime.strptime(value.strip().replace("T", " ")[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        raise HTTPException(400, "Meeting time must look like 2026-09-20 14:00.")
    now = datetime.now(IST).replace(tzinfo=None)
    if dt < now:
        raise HTTPException(400, "That meeting time has already passed.")
    if dt > now + timedelta(days=365):
        raise HTTPException(400, "Book the meeting within the next year.")
    return dt.strftime("%Y-%m-%d %H:%M")


BOARD_STATUSES = ["New", "Contacted", "Interested", "Follow Up", "Meeting Booked", "Closed Won", "Closed Lost",
                  "Not Interested", "Do Not Call"]
LEAD_STATUSES = set(BOARD_STATUSES)


class BulkUpdate(Ids):
    status: str | None = None
    qualification: str | None = None
    language: str | None = None
    do_not_call: bool | None = None
    add_tags: list[str] | None = None


@router.get("")
def list_leads(search: str | None = None, status: str | None = None, call_status: str | None = None,
               qualification: str | None = None, sort: str = "id", order: str = "desc",
               page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=500), view: str | None = None,
               source: str | None = None, agent_id: int = Depends(workspace)):
    return CRMService(agent_id).list_leads(search, status, call_status, qualification, sort, order, page, page_size, view, source)


@router.get("/views")
def lead_views(agent_id: int = Depends(workspace)):
    """Counts for the saved views on the Leads page."""
    return CRMService(agent_id).view_counts()


@router.get("/stats")
def stats(agent_id: int = Depends(workspace)):
    return CRMService(agent_id).stats()


@router.get("/board")
def board(search: str | None = None, qualification: str | None = None, per_column: int = Query(50, ge=1, le=200),
          agent_id: int = Depends(workspace)):
    return CRMService(agent_id).board(BOARD_STATUSES, search, qualification, per_column)


@router.get("/export")
def export(search: str | None = None, status: str | None = None, call_status: str | None = None,
           qualification: str | None = None, view: str | None = None, source: str | None = None,
           agent_id: int = Depends(workspace)):
    """The filtered view, not the whole book: the button sits next to the filters that produced it."""
    slug = "".join(c if c.isalnum() else "-" for c in agents.get(agent_id)["name"].lower()).strip("-") or "agent"
    csv = CRMService(agent_id).export_csv(search=search, status=status, call_status=call_status,
                                          qualification=qualification, view=view, source=source)
    return Response(csv, media_type="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={slug}-leads.csv"})


@router.get("/ids")
def matching_ids(search: str | None = None, status: str | None = None, call_status: str | None = None,
                 qualification: str | None = None, view: str | None = None, source: str | None = None,
                 limit: int = Query(5000, ge=1, le=20000), agent_id: int = Depends(workspace)):
    """Every id the current filters select, so a bulk action can cover the result set, not one page."""
    ids = CRMService(agent_id).matching_ids(limit=limit, search=search, status=status, call_status=call_status,
                                            qualification=qualification, view=view, source=source)
    return {"ids": ids, "limit": limit}


@router.post("")
def create(body: LeadIn, request: Request, agent_id: int = Depends(workspace)):
    data = body.model_dump()
    if data.get("meeting_at"):
        data["meeting_at"] = meeting_time(data["meeting_at"])
    try:
        return CRMService(agent_id).create(data, actor=actor(request))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/import/preview")
async def import_preview(file: UploadFile = File(...), agent_id: int = Depends(workspace)):
    crm = CRMService(agent_id)
    df = _read(file, await file.read(), crm)
    mapping = crm.map_columns(df.columns)
    return {"rows": len(df), "columns": [str(c) for c in df.columns], "mapping": mapping,
            "sample": df.head(8).fillna("").to_dict("records"), "analysis": crm.analyze_rows(df, mapping)}


@router.post("/import/analyze")
async def import_analyze(file: UploadFile = File(...), mapping: str = Form("{}"), agent_id: int = Depends(workspace)):
    """Re-check rows after the user changes the column mapping."""
    crm = CRMService(agent_id)
    df = _read(file, await file.read(), crm)
    column_map = {k: v for k, v in json.loads(mapping or "{}").items() if v}
    return crm.analyze_rows(df, column_map or None)


@router.post("/import")
async def import_leads(request: Request, file: UploadFile = File(...), mapping: str = Form("{}"),
                       skip_duplicates: bool = Form(True), default_language: str = Form("en-IN"),
                       source: str = Form("import"), tags: str = Form(""), on_duplicate: str = Form("skip"),
                       queue_for_calls: bool = Form(False), agent_id: int = Depends(workspace)):
    crm = CRMService(agent_id)
    df = _read(file, await file.read(), crm)
    try:
        column_map = {k: v for k, v in json.loads(mapping or "{}").items() if v} or None
        return crm.import_rows(df, column_map, skip_duplicates,
                               {"language": default_language, "source": source, "tags": tags or None}, actor(request),
                               on_duplicate=on_duplicate if on_duplicate in ("skip", "update") else "skip",
                               queue_for_calls=queue_for_calls)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/bulk/delete")
def bulk_delete(body: Ids, request: Request, agent_id: int = Depends(workspace)):
    return {"deleted": CRMService(agent_id).delete(body.ids, actor=actor(request))}


@router.post("/bulk/update")
def bulk_update(body: BulkUpdate, request: Request, agent_id: int = Depends(workspace)):
    data = body.model_dump(exclude_unset=True, exclude={"ids", "add_tags"})
    if data.get("status") and data["status"] not in LEAD_STATUSES:
        raise HTTPException(400, f"Unknown status: {data['status']}")
    if data.get("qualification") not in (None, "", "Hot", "Warm", "Cold"):
        raise HTTPException(400, "Qualification must be Hot, Warm or Cold.")
    tags = [t.strip() for t in (body.add_tags or []) if t.strip()]
    if not data and not tags:
        raise HTTPException(400, "Nothing to update.")
    try:
        updated = CRMService(agent_id).bulk_update(body.ids, data, tags, actor=actor(request))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"updated": updated}


@router.post("/bulk/queue")
def bulk_queue(body: QueueRequest, request: Request, agent_id: int = Depends(workspace)):
    """Queue leads: as soon as possible (queue order), or at a chosen time (scheduled call)."""
    crm, queued = CRMService(agent_id), 0
    at = scheduled_time(body.at)
    for lead_id in body.ids:
        try:
            crm.update(lead_id, {"call_status": "Pending", "callback_at": at or ""}, actor="system")
            queued += 1
        except LookupError:
            continue
    from app.services import agents as agent_service
    from app.services.call_service import within_calling_hours
    cfg = agent_service.get_automation(agent_id)
    size = crm.queue_size()
    if at:
        eta = f"Scheduled: the agent calls at {at[11:16]} on {at[:10]}"
        events.record("callback.scheduled", f"Call scheduled for {at}", f"{queued} lead(s)", agent_id=agent_id, actor=actor(request))
        return {"queued": queued, "queue_size": size, "eta": eta, "at": at}
    if within_calling_hours(cfg):
        eta = "Calling starts within a minute" + (f" ({size} in queue, {cfg['max_concurrent_calls']} at a time)" if size > 1 else "")
    else:
        eta = f"Outside calling hours: calls start at {cfg['calling_hours_start']}:00 IST"
    events.record("lead.queued", f"Queued {queued} lead(s) for calling", eta, agent_id=agent_id, actor=actor(request))
    return {"queued": queued, "queue_size": size, "eta": eta}


@router.get("/queue")
def call_queue(agent_id: int = Depends(workspace)):
    """The call queue in dialling order, with why a lead is waiting and a rough time estimate."""
    from app.services import agents as agent_service
    from app.services.call_service import CallService, within_calling_hours
    crm = CRMService(agent_id)
    cfg = agent_service.get_automation(agent_id)
    items = crm.queued(500, dialable_only=False)
    ready_ids = {l["id"] for l in crm.queued(500)}
    slots = max(1, cfg["max_concurrent_calls"])
    open_now = within_calling_hours(cfg)
    rows, ready_index = [], 0
    for position, lead in enumerate(items, start=1):
        if lead["id"] in ready_ids:
            wait = "Next up" if ready_index < slots and open_now else (f"≈ {((ready_index // slots) + 1) * 2} min" if open_now else "When calling hours open")
            state, ready_index = "ready", ready_index + 1
        elif lead.get("phone_valid") is False:
            state, wait = "blocked", "Fix the phone number"
        elif lead.get("callback_at"):
            state, wait = "scheduled", f"Callback at {lead['callback_at'][11:16]}"
        else:
            state, wait = "cooldown", "Just called: waits 10 min"
        rows.append({**lead, "position": position, "state": state, "wait": wait})
    return {"items": rows, "active_calls": CallService(agent_id).active_count(), "slots": cfg["max_concurrent_calls"],
            "open_now": open_now, "hours": f"{cfg['calling_hours_start']}:00–{cfg['calling_hours_end']}:00 IST"}


class QueueOrder(BaseModel):
    ids: list[int]


@router.post("/queue/order")
def reorder_queue(body: QueueOrder, request: Request, agent_id: int = Depends(workspace)):
    moved = CRMService(agent_id).reorder_queue(body.ids)
    return {"reordered": moved}


@router.post("/queue/remove")
def dequeue(body: QueueOrder, request: Request, agent_id: int = Depends(workspace)):
    removed = CRMService(agent_id).dequeue(body.ids)
    events.record("lead.dequeued", f"Removed {removed} lead(s) from the call queue", agent_id=agent_id, actor=actor(request))
    return {"removed": removed}


@router.post("/bulk/call")
def bulk_call(body: Ids, request: Request, agent_id: int = Depends(workspace)):
    calls, placed, errors = CallService(agent_id), [], []
    for lead_id in body.ids[:20]:
        try:
            placed.append(calls.start(lead_id, trigger="bulk", actor=actor(request)))
        except CallError as e:
            errors.append({"lead_id": lead_id, "error": str(e)})
            if "limit" in str(e).lower():
                break
    return {"placed": placed, "errors": errors}


@router.get("/{lead_id}")
def get(lead_id: int, agent_id: int = Depends(workspace)):
    lead = CRMService(agent_id).get(lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found.")
    return lead


@router.patch("/{lead_id}")
def patch(lead_id: int, body: LeadPatch, request: Request, agent_id: int = Depends(workspace)):
    data = body.model_dump(exclude_unset=True)
    if "callback_at" in data:
        at = scheduled_time(data["callback_at"])
        data["callback_at"] = at or ""
        if at:  # a scheduled call is dialled by the callback job and keeps the follow-up date in sync
            data.setdefault("follow_up_date", at[:10])
            data.setdefault("call_status", "Pending")
    if data.get("meeting_at"):
        data["meeting_at"] = meeting_time(data["meeting_at"])
    try:
        return CRMService(agent_id).update(lead_id, data, actor=actor(request))
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/{lead_id}")
def delete(lead_id: int, request: Request, agent_id: int = Depends(workspace)):
    if not CRMService(agent_id).delete([lead_id], actor=actor(request)):
        raise HTTPException(404, "Lead not found.")
    return {"ok": True}


@router.get("/{lead_id}/activity")
def activity(lead_id: int, limit: int = 100, agent_id: int = Depends(workspace)):
    if not CRMService(agent_id).get(lead_id):
        raise HTTPException(404, "Lead not found.")
    return events.list_events(agent_id, lead_id=lead_id, limit=limit)


def _read(file: UploadFile, content: bytes, crm: CRMService):
    if not (file.filename or "").lower().endswith((".csv", ".xlsx", ".xls")):
        raise HTTPException(400, "Upload a .csv, .xlsx or .xls file.")
    if len(content) > MAX_UPLOAD:
        raise HTTPException(413, "File too large (max 20 MB).")
    try:
        return crm.read_table(file.filename, content)
    except Exception as e:
        raise HTTPException(400, f"Could not read file: {e}")

from pydantic import BaseModel
class ManualEmail(BaseModel):
    subject: str
    body: str

@router.get("/{lead_id}/draft_email")
def draft_email(lead_id: int, agent_id: int = Depends(workspace)):
    crm = CRMService(agent_id)
    lead = crm.get(lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found.")
    
    from app.services import agents
    from app.services.llm import complete, parse_json
    persona = agents.get_profile(agent_id)
    
    from app.models.call import Call
    from app.core.database import SessionLocal
    with SessionLocal() as db:
        # Calls record created_at, answered_at and ended_at; there is no started_at, and asking for one
        # made this endpoint fail every time it was opened.
        call = (db.query(Call).filter_by(lead_id=lead_id)
                .order_by(Call.created_at.desc(), Call.id.desc()).first())
        summary = (call.summary if call else None) or "No prior conversation."
    
    prompt = f"""You are {persona.get('agent_name', 'an agent')} from {persona.get('company_name', 'our company')}.
Write a highly professional follow-up email to the prospect '{lead.get('name') or 'there'}'.
Context of previous interaction: {summary}
Lead Notes: {lead.get('notes') or 'None'}

Return ONLY JSON:
{{
  "subject": "Clear, engaging subject line",
  "body": "The professional email body"
}}"""
    try:
        res = complete([{"role": "user", "content": prompt}], json_mode=True, max_tokens=400)
        draft = parse_json(res.text) or {}
    except Exception as e:
        raise HTTPException(502, f"AI generation failed: {e}")
    subject, text = str(draft.get("subject") or "").strip(), str(draft.get("body") or "").strip()
    if not subject or not text:
        # An empty draft silently blanked the compose form and looked like the button did nothing.
        raise HTTPException(502, "The model returned an empty draft. Try again, or write the email yourself.")
    return {"subject": subject, "body": text}

@router.post("/{lead_id}/email")
def send_manual_email(lead_id: int, body: ManualEmail, agent_id: int = Depends(workspace)):
    crm = CRMService(agent_id)
    lead = crm.get(lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found.")
    if not lead.get("email"):
        raise HTTPException(400, "Lead has no email address.")
        
    from app.services.notification_service import email_configured, email_detail, email_sent, send_email
    if not email_configured():
        raise HTTPException(400, email_detail())
    try:
        status = send_email(lead["email"], body.subject, body.body, lead_id=lead_id, agent_id=agent_id, actor="user")
        if not email_sent(status):
            # The provider rejected it (unverified sending domain, bad key, bounce): don't report success.
            raise HTTPException(502, f"Email not sent — {status.removeprefix('failed: ')}")
        events.record(
            "email.manual",
            f"Email to {lead['email']}: {body.subject}",
            f"sent manually · to {lead.get('name') or lead['email']}",
            agent_id=agent_id,
            lead_id=lead_id,
            actor="user",
        )
        return {"ok": True, "status": status}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to send email: {e}")
