"""
AI CRM assistant: natural-language questions over this workspace's CRM data.

The model sees a snapshot of the workspace (counts, leads that need attention, open tasks, deals) and
answers in JSON. Anything that changes data comes back as a proposed action; nothing runs until the
user confirms it (execute()).
"""

import json

from sqlalchemy import func, or_, select

from app.core.database import get_db
from app.core.logging import get_logger
from app.models.crm import Deal, Task
from app.models.lead import Lead
from app.services import crm_records, events, llm

log = get_logger(__name__)
ACTION_TYPES = ("create_task", "update_lead", "send_email", "queue_call", "create_deal", "add_tag")

SYSTEM = """You are the CRM assistant inside a sales CRM. Answer the user's question using ONLY the CRM snapshot below.
Be concise and specific: name leads, numbers and dates. Use short bullet lists when listing records. Amounts are in INR (₹).
If the data needed is not in the snapshot, say so plainly.

When the user asks you to DO something (create a task, update a lead, draft and send an email, queue a call, create a deal,
tag a lead), do not claim it is done. Propose it as an action for the user to confirm.

Reply with JSON only:
{"answer": "markdown text", "actions": [{"type": one of %s, "label": "short human description",
  "lead_id": int or null, "params": {...}}]}
Params by type:
- create_task: {"title": str, "due_in_hours": number, "kind": "follow_up"|"call"|"email"|"meeting"|"todo", "priority": "low"|"medium"|"high"}
- update_lead: {"fields": {"status"|"qualification"|"follow_up_date"|"notes": value}}
- send_email: {"subject": str, "body": str}   (write the full email body; the lead must have an email)
- queue_call: {}
- create_deal: {"title": str, "value": number}
- add_tag: {"tag": str}
"actions" is [] when nothing needs doing."""


def _lead_row(l: Lead) -> dict:
    return {k: v for k, v in {"id": l.id, "name": l.name, "company": l.company, "phone": l.phone, "email": l.email,
                              "status": l.status, "qualification": l.qualification, "follow_up": l.follow_up_date,
                              "meeting_at": l.meeting_at, "last_contacted": str(l.last_contacted_at or "")[:16] or None,
                              "summary": (l.summary or "")[:220] or None}.items() if v}


def snapshot(agent_id: int, question: str) -> dict:
    now = crm_records.ist_now()
    with get_db() as db:
        leads = select(Lead).where(Lead.agent_id == agent_id)
        by_status = dict(db.execute(select(Lead.status, func.count()).where(Lead.agent_id == agent_id).group_by(Lead.status)).all())
        by_qual = dict(db.execute(select(Lead.qualification, func.count()).where(Lead.agent_id == agent_id).group_by(Lead.qualification)).all())
        no_follow_up = db.scalars(leads.where(or_(Lead.follow_up_date.is_(None), Lead.follow_up_date == ""),
                                              Lead.status.notin_(["Converted", "Lost", "Not Interested"])).order_by(Lead.updated_at.desc()).limit(15))
        hot = db.scalars(leads.where(Lead.qualification.in_(["Hot", "hot", "Qualified"])).order_by(Lead.updated_at.desc()).limit(15))
        recent = db.scalars(leads.where(Lead.summary.isnot(None)).order_by(Lead.last_contacted_at.desc()).limit(12))
        words = [w for w in question.replace("?", " ").split() if len(w) > 3][:6]
        mentioned = db.scalars(leads.where(or_(*[Lead.name.ilike(f"%{w}%") for w in words] + [Lead.company.ilike(f"%{w}%") for w in words])).limit(8)) if words else []
        tasks = db.scalars(select(Task).where(Task.agent_id == agent_id, Task.status == "open").order_by(Task.due_at).limit(20))
        deals = db.scalars(select(Deal).where(Deal.agent_id == agent_id).order_by(Deal.updated_at.desc()).limit(20))
        return {
            "now_ist": now,
            "lead_counts_by_status": {k or "None": v for k, v in by_status.items()},
            "lead_counts_by_qualification": {k or "Unqualified": v for k, v in by_qual.items()},
            "leads_mentioned": [_lead_row(l) for l in mentioned],
            "leads_without_follow_up": [_lead_row(l) for l in no_follow_up],
            "hot_or_qualified_leads": [_lead_row(l) for l in hot],
            "recently_contacted_with_ai_summary": [_lead_row(l) for l in recent],
            "open_tasks": [{"id": t.id, "title": t.title, "due": t.due_at, "overdue": bool(t.due_at and t.due_at < now), "lead_id": t.lead_id, "assignee": t.assignee} for t in tasks],
            "deals": [{"id": d.id, "title": d.title, "stage": d.stage, "value": d.value, "lead_id": d.lead_id, "expected_close": d.expected_close} for d in deals],
            "deal_summary": crm_records.deal_summary(agent_id),
            "task_summary": crm_records.task_summary(agent_id),
        }


def chat(agent_id: int, message: str, history: list[dict] | None = None, actor: str = "admin") -> dict:
    data = snapshot(agent_id, message)
    messages = [{"role": "system", "content": SYSTEM % list(ACTION_TYPES) + "\n\nCRM snapshot:\n" + json.dumps(data, ensure_ascii=False, default=str)}]
    for turn in (history or [])[-8:]:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            messages.append({"role": turn["role"], "content": str(turn["content"])[:2000]})
    messages.append({"role": "user", "content": message})
    try:
        result = llm.complete(messages, json_mode=True, max_tokens=900, temperature=0.2, timeout=40)
    except llm.LLMError as e:
        log.warning("Assistant LLM failed: %s", e)
        return {"answer": "The AI service is unavailable right now. Your CRM data is unchanged; try again in a minute, or use Search and the Leads page meanwhile.",
                "actions": [], "error": True}
    parsed = llm.parse_json(result.text) or {}
    actions = [a for a in (parsed.get("actions") or []) if isinstance(a, dict) and a.get("type") in ACTION_TYPES]
    events.record("assistant.query", f"Assistant: {message[:120]}", agent_id=agent_id, actor=actor, data={"actions": len(actions), "model": result.model})
    return {"answer": parsed.get("answer") or result.text, "actions": actions, "model": result.model}


def execute(agent_id: int, action: dict, actor: str = "admin") -> dict:
    """Runs one action the user confirmed, through the same code path as automation actions."""
    from app.services import rules
    if action.get("type") not in ACTION_TYPES:
        raise ValueError("Unknown action.")
    lead_id = action.get("lead_id")
    ctx = rules._context(agent_id, int(lead_id) if lead_id else None, {})
    rules._run_action(agent_id, {"type": action["type"], **(action.get("params") or {})}, ctx, f"assistant ({actor})")
    events.record("assistant.action", f"Assistant action done: {action.get('label') or action['type']}", agent_id=agent_id,
                  lead_id=ctx.get("lead_id"), actor=actor)
    return {"ok": True}
