"""
Automation builder: trigger → conditions → actions, run when a matching event is recorded.

Every activity event goes through events.record, which hands trigger events to dispatch(). Rules run on a
worker thread; events recorded by a rule's own actions do not trigger further rules (no loops).
"""

import asyncio
import json
import threading
from datetime import datetime, timedelta

from sqlalchemy import select

from app.core.database import get_db
from app.core.logging import get_logger
from app.models.crm import AutomationRule

log = get_logger(__name__)
_local = threading.local()

TRIGGERS = {
    "lead.created": "New lead created", "lead.imported": "Leads imported", "lead.updated": "Lead updated",
    "lead.converted": "Lead converted to customer", "call.ended": "Call ended", "call.failed": "Call failed",
    "meeting.booked": "Meeting booked", "deal.created": "Deal created", "deal.stage_changed": "Deal moved stage",
    "deal.won": "Deal won", "deal.lost": "Deal lost", "task.completed": "Task completed", "contact.created": "Contact created",
}
ACTIONS = {
    "create_task": "Create a follow-up task", "update_lead": "Update lead fields", "add_tag": "Tag the lead",
    "queue_call": "Queue an AI call", "send_email": "Email the lead", "send_whatsapp": "WhatsApp the lead",
    "notify_team": "Notify the team", "create_deal": "Create a deal",
}
OPS = ("eq", "neq", "contains", "gt", "lt", "exists", "not_exists")


def in_rule() -> bool:
    return getattr(_local, "active", False)


def render(text: str, ctx: dict) -> str:
    out = text or ""
    for key, value in ctx.items():
        if isinstance(value, (str, int, float)) or value is None:
            out = out.replace("{" + key + "}", "" if value is None else str(value))
    return out


def _matches(cond: dict, ctx: dict) -> bool:
    value, op, target = ctx.get(cond.get("field", "")), cond.get("op", "eq"), cond.get("value")
    if op == "exists":
        return value not in (None, "")
    if op == "not_exists":
        return value in (None, "")
    if op in ("gt", "lt"):
        try:
            a, b = float(value), float(target)
        except (TypeError, ValueError):
            return False
        return a > b if op == "gt" else a < b
    a, b = str(value or "").strip().lower(), str(target or "").strip().lower()
    if op == "contains":
        return b in a
    return (a == b) if op == "eq" else (a != b)


def _context(agent_id: int, lead_id: int | None, data: dict | None) -> dict:
    ctx = {**(data or {})}
    if lead_id:
        from app.services.crm_service import CRMService
        lead = CRMService(agent_id).get(lead_id) or {}
        ctx.update({k: v for k, v in lead.items() if not isinstance(v, (dict, list))})
        ctx["tags"] = ",".join(lead.get("tags") or []) if isinstance(lead.get("tags"), list) else lead.get("tags")
        ctx["lead_id"] = lead_id
    return ctx


def _run_action(agent_id: int, action: dict, ctx: dict, rule_name: str):
    from app.services import crm_records
    from app.services.crm_service import CRMService
    kind, lead_id = action.get("type"), ctx.get("lead_id")
    actor = f"automation:{rule_name}"[:64]
    if kind == "create_task":
        due = datetime.utcnow() + timedelta(hours=5.5 + float(action.get("due_in_hours") or 24))
        crm_records.create(agent_id, "tasks", {"title": render(action.get("title") or "Follow up with {name}", ctx), "kind": action.get("kind") or "follow_up",
                                               "priority": action.get("priority") or "medium", "assignee": action.get("assignee") or None,
                                               "lead_id": lead_id, "due_at": due.strftime("%Y-%m-%d %H:%M")}, actor=actor)
    elif kind == "create_deal":
        crm_records.create(agent_id, "deals", {"title": render(action.get("title") or "{company} deal", ctx), "value": float(action.get("value") or 0),
                                               "lead_id": lead_id, "stage": "Prospecting"}, actor=actor)
    elif kind == "notify_team":
        from app.services.notification_service import notify_team
        notify_team(render(action.get("subject") or "Automation: {name}", ctx), render(action.get("body") or "", ctx), lead_id=lead_id, agent_id=agent_id)
    else:
        if not lead_id:
            raise ValueError(f"'{ACTIONS.get(kind, kind)}' needs a lead, and this event has none.")
        crm = CRMService(agent_id)
        if kind == "update_lead":
            crm.update(lead_id, {k: render(str(v), ctx) for k, v in (action.get("fields") or {}).items()}, actor=actor)
        elif kind == "add_tag":
            crm.bulk_update([lead_id], {}, add_tags=[action.get("tag") or "automation"], actor=actor)
        elif kind == "queue_call":
            lead = crm.get(lead_id) or {}
            if lead.get("do_not_call"):
                raise ValueError("Lead is marked do-not-call.")
            crm.update(lead_id, {"call_status": "Pending", "callback_at": ""}, actor=actor)
        elif kind == "send_email":
            lead = crm.get(lead_id) or {}
            if lead.get("do_not_call"):
                raise ValueError("Lead is marked do-not-call.")
            if not ctx.get("email"):
                raise ValueError("Lead has no email address.")
            from app.services.notification_service import send_email
            send_email(ctx["email"], render(action.get("subject") or "Hello {name}", ctx), render(action.get("body") or "", ctx),
                       lead_id=lead_id, agent_id=agent_id, actor="ai")
        elif kind == "send_whatsapp":
            lead = crm.get(lead_id) or {}
            if lead.get("do_not_call"):
                raise ValueError("Lead is marked do-not-call.")
            from app.services.whatsapp_service import is_opted_out
            if is_opted_out(lead):
                raise ValueError("Lead has opted out of WhatsApp.")
            from app.services.whatsapp_cloud_service import send_whatsapp_message
            asyncio.run(send_whatsapp_message(ctx.get("phone") or "", render(action.get("text") or "", ctx), agent_id=agent_id, lead_id=lead_id))
        else:
            raise ValueError(f"Unknown action {kind}.")


def execute(rule: dict, agent_id: int, lead_id: int | None, data: dict | None, dry_run: bool = False) -> dict:
    """One rule against one event. Returns {matched, actions: [{type, ok, error}]}."""
    ctx = _context(agent_id, lead_id, data)
    matched = all(_matches(c, ctx) for c in (rule.get("conditions") or []))
    result = {"matched": matched, "actions": []}
    if not matched or dry_run:
        return result
    for action in rule.get("actions") or []:
        try:
            _run_action(agent_id, action, ctx, rule["name"])
            result["actions"].append({"type": action.get("type"), "ok": True})
        except Exception as e:  # noqa: BLE001 - one action failing must not stop the rest; it is reported
            log.warning("Rule %s action %s failed: %s", rule.get("id"), action.get("type"), e)
            result["actions"].append({"type": action.get("type"), "ok": False, "error": str(e)})
    return result


def _dispatch(event_type: str, agent_id: int, lead_id: int | None, data: dict | None):
    _local.active = True
    try:
        with get_db() as db:
            rules = [r.to_dict() for r in db.scalars(select(AutomationRule).where(
                AutomationRule.agent_id == agent_id, AutomationRule.trigger == event_type, AutomationRule.enabled.is_(True)))]
        for rule in rules:
            result = execute(rule, agent_id, lead_id, data)
            if not result["matched"]:
                continue
            errors = [a["error"] for a in result["actions"] if not a["ok"]]
            with get_db() as db:
                row = db.get(AutomationRule, rule["id"])
                if row:
                    row.runs += 1
                    row.failures += 1 if errors else 0
                    row.last_run_at = datetime.utcnow()
                    row.last_error = "; ".join(errors) or None
            from app.services import events
            events.record("automation.run", f"Rule “{rule['name']}” ran", "; ".join(errors) or f"{len(result['actions'])} action(s) done",
                          agent_id=agent_id, lead_id=lead_id, actor="automation", data={"rule_id": rule["id"], "trigger": event_type, "result": result})
    except Exception:
        log.exception("Automation dispatch for %s failed", event_type)
    finally:
        _local.active = False


def dispatch(event_type: str, agent_id: int | None, lead_id: int | None, data: dict | None):
    if event_type not in TRIGGERS or not agent_id or in_rule():
        return
    with get_db() as db:  # most events match no rule: do not start a thread for them
        if not db.scalar(select(AutomationRule.id).where(AutomationRule.agent_id == agent_id, AutomationRule.trigger == event_type,
                                                         AutomationRule.enabled.is_(True)).limit(1)):
            return
    threading.Thread(target=_dispatch, args=(event_type, agent_id, lead_id, json.loads(json.dumps(data or {}, default=str))),
                     daemon=True, name=f"rule-{event_type}").start()
