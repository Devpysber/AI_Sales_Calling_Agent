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
    if channel == "email":
        return [l for l in leads if l.email]
    if channel == "whatsapp":
        return [l for l in leads if l.phone]
    return [l for l in leads if not (channel == "call" and l.do_not_call)]


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
                from app.services.whatsapp_service import send_whatsapp_message
                result = asyncio.run(send_whatsapp_message(lead.phone, render(campaign.get("message") or "", _ctx(lead)),
                                                           agent_id=campaign["agent_id"], lead_id=lead.id))
                stats["sent" if (result or {}).get("ok") else "failed"] += 1
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
        from app.services.whatsapp_service import is_whatsapp_configured
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
