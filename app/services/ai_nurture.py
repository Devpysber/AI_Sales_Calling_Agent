"""
AI-driven proactive follow-up: unlike job_nurture (always a voice call, fixed N days, no judgment),
this asks the LLM per stale lead whether to re-engage now and how -- call, WhatsApp, email, or skip --
then carries out exactly that one decision through the existing send paths. Opt-in and separate from
job_nurture, which keeps running unchanged for operators who want the simple always-call behaviour.
"""

import json

from app.core.logging import get_logger
from app.services import agents, events, llm
from app.services.crm_service import CRMService
from app.services.settings_service import SettingsService

log = get_logger(__name__)

ACTIONS = ("call", "whatsapp", "email", "skip")

SYSTEM = """You are deciding whether to proactively re-engage ONE stale sales lead on behalf of {company}.
This lead has gone quiet. You see their full context below. Decide the single best next action.

Reply with JSON only: {{"action": "call"|"whatsapp"|"email"|"skip", "message": "...", "reason": "..."}}
- "call": queue an AI outbound call; message is unused (leave "").
- "whatsapp": message is a short WhatsApp-style message (under 300 characters), written as {agent_name} from {company}, in the lead's own language if known. No markdown headers.
- "email": message is a short email body (plain text, a few sentences), subject line implied by context.
- "skip": nothing is working, recent contact was too soon, or they asked not to be contacted -- message "".
Be conservative: skip rather than nag. Never invent facts not in the context (pricing, dates, promises) beyond what the sales playbook below states."""


def _lead_context(lead: dict) -> str:
    fields = {"name": lead.get("name"), "company": lead.get("company"), "status": lead.get("status"),
              "qualification": lead.get("qualification"), "last_contacted": lead.get("last_contacted_at"),
              "summary": (lead.get("summary") or "")[:400], "source": lead.get("source"),
              "language": lead.get("language"), "notes": (lead.get("notes") or "")[:300]}
    return json.dumps({k: v for k, v in fields.items() if v}, ensure_ascii=False, default=str)


def decide(agent_id: int, lead: dict) -> dict:
    persona = agents.get_profile(agent_id)
    system = SYSTEM.format(company=persona["company_name"], agent_name=persona["agent_name"])
    if persona.get("sales_playbook"):
        system += "\n\nSales playbook:\n" + persona["sales_playbook"]
    messages = [{"role": "system", "content": system}, {"role": "user", "content": "Lead context:\n" + _lead_context(lead)}]
    try:
        result = llm.complete(messages, json_mode=True, max_tokens=350, temperature=0.4, timeout=20)
    except llm.LLMError as e:
        log.warning("AI nurture decision failed for lead %s: %s", lead.get("id"), e)
        return {"action": "skip", "message": "", "reason": f"LLM error: {e}"}
    parsed = llm.parse_json(result.text) or {}
    action = parsed.get("action") if parsed.get("action") in ACTIONS else "skip"
    return {"action": action, "message": str(parsed.get("message") or "")[:1000], "reason": str(parsed.get("reason") or "")[:300]}


def run(agent_id: int, cfg: dict) -> str:
    """One scheduler tick: decide-and-act for up to cfg['ai_nurture_max_per_run'] stale leads."""
    from app.models.lead import Lead
    from app.core.database import get_db
    from app.services.call_service import CallService, CallError
    from app.services.campaigns import _send_whatsapp_lead
    from app.services.notification_service import send_email, email_sent

    state = SettingsService()
    candidates = CRMService(agent_id).nurture_candidates(cfg["nurture_after_days"], cfg["ai_nurture_max_per_run"] * 3)
    calls = CallService(agent_id)
    acted, skipped, failed = 0, 0, 0
    for lead in candidates:
        if acted >= cfg["ai_nurture_max_per_run"]:
            break
        key = f"ai_nurture.{lead['id']}"
        attempts = state.get_state(key) or 0
        if attempts >= cfg["nurture_max_attempts"] or lead.get("do_not_call"):
            continue
        decision = decide(agent_id, lead)
        if decision["action"] == "skip":
            skipped += 1
            continue
        try:
            if decision["action"] == "call":
                calls.start(lead["id"], trigger="ai_nurture", actor="ai_nurture", purpose="follow_up")
            elif decision["action"] == "whatsapp":
                if not lead.get("phone"):
                    continue
                with get_db() as db:
                    lead_row = db.get(Lead, lead["id"])
                    if lead_row is None:
                        continue
                    # _send_whatsapp_lead is itself synchronous (it wraps its own asyncio.run calls
                    # internally) -- calling it directly here, not wrapped in another asyncio.run.
                    result = _send_whatsapp_lead(
                        {"agent_id": agent_id, "message": decision["message"], "id": f"ai_nurture_{lead['id']}"}, lead_row)
                if result is None or not result.get("ok"):
                    failed += 1
                    continue
            elif decision["action"] == "email":
                if not lead.get("email"):
                    continue
                persona = agents.get_profile(agent_id)
                status = send_email(lead["email"], f"Following up - {persona['company_name']}", decision["message"],
                                    lead_id=lead["id"], agent_id=agent_id)
                if not email_sent(status):
                    failed += 1
                    continue
            state.set_state(key, attempts + 1)
            acted += 1
            events.record("ai_nurture.action", f"AI follow-up ({decision['action']}): {lead.get('name') or lead.get('phone') or lead['id']}",
                          decision["reason"], agent_id=agent_id, lead_id=lead["id"], actor="ai_nurture")
        except CallError as e:
            failed += 1
            log.warning("AI nurture call failed for lead %s: %s", lead["id"], e)
    if not acted and not skipped and not failed:
        return "no leads to review"
    return f"{acted} action(s) taken, {skipped} skipped, {failed} failed"
