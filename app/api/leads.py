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
    retry_count: int | None = None


class Ids(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=1000)


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
def export(agent_id: int = Depends(workspace)):
    slug = "".join(c if c.isalnum() else "-" for c in agents.get(agent_id)["name"].lower()).strip("-") or "agent"
    return Response(CRMService(agent_id).export_csv(), media_type="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={slug}-leads.csv"})


@router.post("")
def create(body: LeadIn, request: Request, agent_id: int = Depends(workspace)):
    try:
        return CRMService(agent_id).create(body.model_dump(), actor=actor(request))
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
def bulk_queue(body: Ids, request: Request, agent_id: int = Depends(workspace)):
    """Mark leads Pending so this agent's auto-dial picks them up within calling hours."""
    crm, queued = CRMService(agent_id), 0
    for lead_id in body.ids:
        try:
            crm.update(lead_id, {"call_status": "Pending"}, actor="system")
            queued += 1
        except LookupError:
            continue
    events.record("lead.queued", f"Queued {queued} lead(s) for auto-dial", agent_id=agent_id, actor=actor(request))
    return {"queued": queued}


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
    try:
        return CRMService(agent_id).update(lead_id, body.model_dump(exclude_unset=True), actor=actor(request))
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
