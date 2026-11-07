"""
Campaigns: pick an audience of leads with a segment, then reach them by AI call, email or WhatsApp.

A call campaign puts the audience in the dialler queue (calling hours, retries and do-not-call apply as
for any queued lead). Email and WhatsApp campaigns send on a background thread and keep their counts.
"""

import asyncio
import json
import threading
from datetime import datetime

from sqlalchemy import func, or_, select

from app.core.database import get_db
from app.core.logging import get_logger
from app.models.crm import Campaign
from app.models.lead import Lead
from app.services import events
from app.services.rules import render

log = get_logger(__name__)
MAX_AUDIENCE = 5000


def _audience_query(agent_id: int, segment: dict):
    q = select(Lead).where(Lead.agent_id == agent_id)
    if segment.get("status"):
        q = q.where(Lead.status.in_(segment["status"]))
    if segment.get("qualification"):
        q = q.where(Lead.qualification.in_(segment["qualification"]))
    if segment.get("source"):
        q = q.where(Lead.source == segment["source"])
    if segment.get("language"):
        q = q.where(Lead.language == segment["language"])
    if segment.get("tags"):
        q = q.where(or_(*[func.lower(Lead.tags).like(f"%{t.strip().lower()}%") for t in segment["tags"] if t.strip()]))
    return q


def audience(agent_id: int, segment: dict | None, channel: str) -> list[Lead]:
    with get_db() as db:
        leads = list(db.scalars(_audience_query(agent_id, segment or {}).limit(MAX_AUDIENCE)))
    # do_not_call is the shared "don't contact this lead" flag (opt_out_tool sets it for chat opt-outs
    # too, per its own docstring) -- a campaign blast must respect it on every channel, not just calls.
    leads = [l for l in leads if not l.do_not_call]
    if channel == "email":
        return [l for l in leads if l.email]
    if channel == "whatsapp":
        from app.services.whatsapp_service import is_opted_out
        return [l for l in leads if l.phone and not is_opted_out(l.to_dict())]
    return leads


def preview(agent_id: int, segment: dict | None, channel: str) -> dict:
    leads = audience(agent_id, segment, channel)
    return {"count": len(leads), "sample": [{"id": l.id, "name": l.name, "phone": l.phone, "email": l.email, "status": l.status} for l in leads[:8]]}


def _ctx(lead: Lead) -> dict:
    return {"name": lead.name or "there", "company": lead.company or "", "phone": lead.phone, "email": lead.email or "", "city": lead.city or ""}


def _save(campaign_id: int, **values):
    with get_db() as db:
        row = db.get(Campaign, campaign_id)
        for key, value in values.items():
            setattr(row, key, value)


def _send_whatsapp_lead(campaign: dict, lead: Lead) -> dict | None:
    """Outside the 24h window, use an agent-configured WhatsApp template automatically; else skip the
    lead and log why (returns None for skip, a send result dict otherwise)."""
    from sqlalchemy import select as _select

    from app.models.channel import Conversation
    from app.models.crm import MessageTemplate
    from app.services.whatsapp_cloud_service import is_within_free_form_window, send_whatsapp_message, send_whatsapp_template

    agent_id = campaign["agent_id"]
    with get_db() as db:
        conv = db.scalars(_select(Conversation).where(Conversation.agent_id == agent_id, Conversation.lead_id == lead.id,
                          Conversation.channel == "whatsapp").order_by(Conversation.id.desc())).first()
        within_window = conv is not None and is_within_free_form_window(conv)
    if within_window:
        return asyncio.run(send_whatsapp_message(lead.phone, render(campaign.get("message") or "", _ctx(lead)),
                                                  agent_id=agent_id, lead_id=lead.id))
    with get_db() as db:
        tmpl = db.query(MessageTemplate).filter(MessageTemplate.agent_id == agent_id,
                                                MessageTemplate.channel == "whatsapp",
                                                MessageTemplate.is_meta_approved.is_(True)).order_by(MessageTemplate.id).first()
        tmpl_name, tmpl_lang = (tmpl.whatsapp_template_name, tmpl.whatsapp_template_language) if tmpl else (None, None)
    if not tmpl_name:
        log.warning("Campaign %s skipping lead %s: outside 24h window and no WhatsApp template configured",
                   campaign["id"], lead.id)
        return None
    return asyncio.run(send_whatsapp_template(lead.phone, tmpl_name, tmpl_lang or "en_US", [],
                                              agent_id=agent_id, lead_id=lead.id))


def _send(campaign: dict, leads: list[Lead]):
    stats = {"targeted": len(leads), "sent": 0, "failed": 0, "skipped": 0}
    from app.services.notification_service import email_sent, send_email
    for lead in leads:
        try:
            if campaign["channel"] == "email":
                status = send_email(lead.email, render(campaign.get("subject") or "", _ctx(lead)), render(campaign.get("message") or "", _ctx(lead)),
                                    lead_id=lead.id, agent_id=campaign["agent_id"], actor="user")
                stats["sent" if email_sent(status) else "failed"] += 1
            else:
                result = _send_whatsapp_lead(campaign, lead)
                if result is None:
                    stats["skipped"] += 1
                else:
                    stats["sent" if result.get("ok") else "failed"] += 1
        except Exception as e:  # noqa: BLE001
            log.warning("Campaign %s to lead %s failed: %s", campaign["id"], lead.id, e)
            stats["failed"] += 1
        _save(campaign["id"], stats_json=json.dumps(stats))
    _save(campaign["id"], status="completed")
    events.record("campaign.completed", f"Campaign “{campaign['name']}” finished", f"{stats['sent']} sent, {stats['failed']} failed",
                  agent_id=campaign["agent_id"], actor="system", data=stats)


def run(agent_id: int, campaign_id: int, actor: str = "admin") -> dict:
    from app.services.crm_records import get
    campaign = get(agent_id, "campaigns", campaign_id)
    if campaign["status"] == "running":
        raise ValueError("This campaign is already running.")
    if campaign["channel"] in ("email", "whatsapp") and not (campaign.get("message") or "").strip():
        raise ValueError("Write the message before launching.")
    if campaign["channel"] == "whatsapp":
        from app.services.whatsapp_cloud_service import is_whatsapp_configured
        if not is_whatsapp_configured():
            raise ValueError("WhatsApp is not configured on the server.")
    leads = audience(agent_id, campaign.get("segment"), campaign["channel"])
    if not leads:
        raise ValueError("No leads match this audience.")
    _save(campaign_id, status="running", started_at=datetime.utcnow(), stats_json=json.dumps({"targeted": len(leads), "sent": 0, "failed": 0, "skipped": 0}))
    events.record("campaign.started", f"Campaign “{campaign['name']}” launched", f"{campaign['channel']} to {len(leads)} lead(s)", agent_id=agent_id, actor=actor)
    if campaign["channel"] == "call":
        from app.services.crm_service import CRMService
        crm, queued = CRMService(agent_id), 0
        for lead in leads:
            if lead.phone:
                crm.update(lead.id, {"call_status": "Pending", "callback_at": ""}, actor="system")
                queued += 1
        stats = {"targeted": len(leads), "sent": queued, "failed": 0, "skipped": len(leads) - queued}
        _save(campaign_id, status="completed", stats_json=json.dumps(stats))
        return {"ok": True, "stats": stats, "message": f"{queued} lead(s) queued for AI calls (calling hours and the concurrency cap apply)."}
    threading.Thread(target=_send, args=(campaign, leads), daemon=True, name=f"campaign-{campaign_id}").start()
    return {"ok": True, "stats": {"targeted": len(leads)}, "message": f"Sending to {len(leads)} lead(s)."}
