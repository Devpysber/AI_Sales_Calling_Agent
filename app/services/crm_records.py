"""
Workspace-scoped CRUD for the CRM records in app.models.crm, plus the business steps around them:
deal stage changes, task completion with recurrence, and lead → customer conversion.
"""

import json
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select

from app.core.database import get_db
from app.models.crm import DEAL_STAGES, AutomationRule, Campaign, Company, Contact, Deal, MessageTemplate, Task
from app.models.lead import Lead
from app.services import events

MODELS = {"companies": Company, "contacts": Contact, "deals": Deal, "tasks": Task, "templates": MessageTemplate,
          "campaigns": Campaign, "rules": AutomationRule}
READ_ONLY = {"id", "agent_id", "created_at", "updated_at", "completed_at", "runs", "failures", "last_run_at",
             "last_error", "started_at", "stats_json"}
SEARCH_COLUMNS = {"companies": ("name", "industry", "city"), "contacts": ("name", "email", "phone", "tags"),
                  "deals": ("title", "notes"), "tasks": ("title", "description"), "templates": ("name", "body"),
                  "campaigns": ("name",), "rules": ("name", "trigger")}
TITLE = {"companies": "name", "contacts": "name", "deals": "title", "tasks": "title", "templates": "name",
         "campaigns": "name", "rules": "name"}
SINGULAR = {"companies": "company", "contacts": "contact", "deals": "deal", "tasks": "task", "templates": "template",
            "campaigns": "campaign", "rules": "rule"}


def _assign(row, data: dict):
    columns = {c.name for c in row.__table__.columns}
    for key, value in data.items():
        if f"{key}_json" in columns and f"{key}_json" not in READ_ONLY:
            setattr(row, f"{key}_json", json.dumps(value, ensure_ascii=False) if value is not None else None)
        elif key in columns and key not in READ_ONLY:
            setattr(row, key, value)


def _validate(kind: str, data: dict):
    if kind == "deals" and data.get("stage") and data["stage"] not in DEAL_STAGES:
        raise ValueError(f"Stage must be one of: {', '.join(DEAL_STAGES)}.")
    title = TITLE[kind]
    if title in data and not str(data[title] or "").strip():
        raise ValueError(f"{title.capitalize()} is required.")


def list_records(agent_id: int, kind: str, search: str = "", filters: dict | None = None, limit: int = 500) -> list[dict]:
    model = MODELS[kind]
    with get_db() as db:
        q = select(model).where(model.agent_id == agent_id)
        if search:
            like = f"%{search.strip().lower()}%"
            q = q.where(or_(*[func.lower(getattr(model, c)).like(like) for c in SEARCH_COLUMNS[kind]]))
        for key, value in (filters or {}).items():
            if value not in (None, "") and hasattr(model, key):
                q = q.where(getattr(model, key) == value)
        order = (model.status.desc(), model.due_at.is_(None), model.due_at) if kind == "tasks" else (model.id.desc(),)
        q = q.order_by(*order).limit(limit)
        return [r.to_dict() for r in db.scalars(q)]


def get(agent_id: int, kind: str, record_id: int) -> dict:
    with get_db() as db:
        row = db.get(MODELS[kind], record_id)
        if not row or row.agent_id != agent_id:
            raise LookupError(f"{SINGULAR[kind].capitalize()} not found.")
        return row.to_dict()


def create(agent_id: int, kind: str, data: dict, actor: str = "admin") -> dict:
    if TITLE[kind] not in data:
        raise ValueError(f"{TITLE[kind].capitalize()} is required.")
    _validate(kind, data)
    model = MODELS[kind]
    with get_db() as db:
        row = model(agent_id=agent_id)
        if kind == "tasks":
            row.created_by = actor
        _assign(row, data)
        db.add(row)
        db.flush()
        out = row.to_dict()
    events.record(f"{SINGULAR[kind]}.created", f"New {SINGULAR[kind]}: {out[TITLE[kind]]}", agent_id=agent_id,
                  lead_id=out.get("lead_id"), actor=actor, data={"id": out["id"], **{k: out.get(k) for k in ("stage", "value", "due_at", "assignee") if k in out}})
    return out


def update(agent_id: int, kind: str, record_id: int, data: dict, actor: str = "admin") -> dict:
    _validate(kind, data)
    with get_db() as db:
        row = db.get(MODELS[kind], record_id)
        if not row or row.agent_id != agent_id:
            raise LookupError(f"{SINGULAR[kind].capitalize()} not found.")
        before = row.to_dict()
        _assign(row, data)
        if kind == "deals" and "stage" in data and "probability" not in data:
            row.probability = {"Prospecting": 10, "Qualified": 30, "Proposal": 50, "Negotiation": 75, "Won": 100, "Lost": 0}[row.stage]
        if kind == "tasks" and data.get("status") == "done" and before["status"] != "done":
            row.completed_at = datetime.utcnow()
        db.flush()
        out = row.to_dict()
    changed = {k: out[k] for k in data if k in out and out.get(k) != before.get(k)}
    if kind == "deals" and "stage" in changed:
        kind_event = {"Won": "deal.won", "Lost": "deal.lost"}.get(out["stage"], "deal.stage_changed")
        events.record(kind_event, f"Deal {out['title']}: {before['stage']} → {out['stage']}", out.get("lost_reason"),
                      agent_id=agent_id, lead_id=out.get("lead_id"), actor=actor, data={"id": out["id"], "value": out["value"], "stage": out["stage"]})
    elif kind == "tasks" and changed.get("status") == "done":
        events.record("task.completed", f"Task done: {out['title']}", agent_id=agent_id, lead_id=out.get("lead_id"), actor=actor, data={"id": out["id"]})
        _spawn_recurrence(agent_id, out, actor)
    elif changed:
        events.record(f"{SINGULAR[kind]}.updated", f"Updated {SINGULAR[kind]}: {out[TITLE[kind]]}", ", ".join(changed),
                      agent_id=agent_id, lead_id=out.get("lead_id"), actor=actor)
    return out


def delete(agent_id: int, kind: str, record_id: int, actor: str = "admin"):
    with get_db() as db:
        row = db.get(MODELS[kind], record_id)
        if not row or row.agent_id != agent_id:
            raise LookupError(f"{SINGULAR[kind].capitalize()} not found.")
        title = getattr(row, TITLE[kind])
        db.delete(row)
    events.record(f"{SINGULAR[kind]}.deleted", f"Deleted {SINGULAR[kind]}: {title}", agent_id=agent_id, actor=actor)


def _spawn_recurrence(agent_id: int, task: dict, actor: str):
    step = {"daily": timedelta(days=1), "weekly": timedelta(weeks=1), "monthly": timedelta(days=30)}.get(task.get("recurrence") or "")
    if not step or not task.get("due_at"):
        return
    try:
        due = datetime.strptime(task["due_at"], "%Y-%m-%d %H:%M") + step
    except ValueError:
        return
    create(agent_id, "tasks", {k: task[k] for k in ("title", "description", "kind", "priority", "recurrence", "assignee", "lead_id", "deal_id", "contact_id")}
           | {"due_at": due.strftime("%Y-%m-%d %H:%M"), "status": "open"}, actor=actor)


def ist_now() -> str:
    return (datetime.utcnow() + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d %H:%M")


def task_summary(agent_id: int) -> dict:
    now, today = ist_now(), ist_now()[:10]
    with get_db() as db:
        base = select(func.count(Task.id)).where(Task.agent_id == agent_id, Task.status == "open")
        return {
            "open": db.scalar(base) or 0,
            "overdue": db.scalar(base.where(Task.due_at < now)) or 0,
            "today": db.scalar(base.where(Task.due_at.like(f"{today}%"))) or 0,
        }


def deal_summary(agent_id: int) -> dict:
    with get_db() as db:
        rows = db.execute(select(Deal.stage, func.count(Deal.id), func.coalesce(func.sum(Deal.value), 0))
                          .where(Deal.agent_id == agent_id).group_by(Deal.stage)).all()
    by_stage = {s: {"count": 0, "value": 0.0} for s in DEAL_STAGES}
    for stage, count, value in rows:
        by_stage.setdefault(stage, {"count": 0, "value": 0.0}).update(count=count, value=float(value))
    open_value = sum(v["value"] for s, v in by_stage.items() if s not in ("Won", "Lost"))
    closed = by_stage["Won"]["count"] + by_stage["Lost"]["count"]
    return {"stages": by_stage, "open_value": open_value, "won_value": by_stage["Won"]["value"],
            "win_rate": round(by_stage["Won"]["count"] / closed * 100, 1) if closed else None}


def convert_lead(agent_id: int, lead_id: int, deal_value: float = 0, deal_title: str = "", actor: str = "admin") -> dict:
    """Lead → customer: a contact (and its company, reused by name) plus an open deal, and the lead marked Converted."""
    with get_db() as db:
        lead = db.get(Lead, lead_id)
        if not lead or lead.agent_id != agent_id:
            raise LookupError("Lead not found.")
        existing = db.scalar(select(Contact).where(Contact.agent_id == agent_id, Contact.lead_id == lead_id))
        if existing:
            raise ValueError("This lead has already been converted.")
        company_id = None
        if lead.company:
            company = db.scalar(select(Company).where(Company.agent_id == agent_id, func.lower(Company.name) == lead.company.strip().lower()))
            if not company:
                company = Company(agent_id=agent_id, name=lead.company.strip(), city=lead.city)
                db.add(company)
                db.flush()
            company_id = company.id
        contact = Contact(agent_id=agent_id, company_id=company_id, lead_id=lead_id, name=lead.name or lead.phone,
                          email=lead.email, phone=lead.phone, tags=lead.tags, notes=lead.summary)
        db.add(contact)
        db.flush()
        deal = Deal(agent_id=agent_id, title=deal_title or f"{lead.company or lead.name or 'Lead'} deal", stage="Qualified",
                    probability=30, value=deal_value, lead_id=lead_id, contact_id=contact.id, company_id=company_id)
        db.add(deal)
        lead.status = "Converted"
        db.flush()
        out = {"contact": contact.to_dict(), "deal": deal.to_dict(), "company_id": company_id}
    events.record("lead.converted", f"{out['contact']['name']} converted to a customer", f"Deal: {out['deal']['title']}",
                  agent_id=agent_id, lead_id=lead_id, actor=actor, data={"contact_id": out["contact"]["id"], "deal_id": out["deal"]["id"]})
    return out


def related(agent_id: int, lead_id: int) -> dict:
    with get_db() as db:
        return {
            "contact": (c.to_dict() if (c := db.scalar(select(Contact).where(Contact.agent_id == agent_id, Contact.lead_id == lead_id))) else None),
            "deals": [d.to_dict() for d in db.scalars(select(Deal).where(Deal.agent_id == agent_id, Deal.lead_id == lead_id))],
            "tasks": [t.to_dict() for t in db.scalars(select(Task).where(Task.agent_id == agent_id, Task.lead_id == lead_id).order_by(Task.due_at))],
        }


def search(agent_id: int, q: str, per_kind: int = 6) -> list[dict]:
    """Global search: leads plus every CRM record type, each hit with a kind, title, subtitle and id."""
    q = q.strip()
    if len(q) < 2:
        return []
    from app.services.crm_service import lead_search_filter
    hits = []
    with get_db() as db:
        for lead in db.scalars(select(Lead).where(Lead.agent_id == agent_id, lead_search_filter(q)).limit(per_kind)):
            hits.append({"kind": "lead", "id": lead.id, "title": lead.name or lead.phone, "subtitle": " · ".join(filter(None, [lead.company, lead.phone, lead.status]))})
    for kind in ("contacts", "companies", "deals", "tasks"):
        for r in list_records(agent_id, kind, search=q, limit=per_kind):
            sub = {"contacts": [r.get("email"), r.get("phone")], "companies": [r.get("industry"), r.get("city")],
                   "deals": [r.get("stage"), f"₹{r.get('value') or 0:,.0f}"], "tasks": [r.get("status"), r.get("due_at")]}[kind]
            hits.append({"kind": SINGULAR[kind], "id": r["id"], "title": r[TITLE[kind]], "subtitle": " · ".join(filter(None, sub))})
    return hits
