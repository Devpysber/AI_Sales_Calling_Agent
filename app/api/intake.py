"""
Website lead capture: each agent gets a public endpoint (with a secret token) that website forms,
landing pages, Zapier/Make or any backend can post leads to. New leads land in that agent's CRM
and, with "speed to lead" on, the agent calls them within seconds.

    POST /api/public/agents/{agent_id}/leads?token=...
    {"name": "...", "phone": "...", "email": "...", "message": "...", "source": "pune-landing"}
"""

import asyncio
import hmac
import random
import secrets
from datetime import datetime, timedelta
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from app.api.deps import workspace
from app.core import store
from app.core.config import settings
from app.core.logging import get_logger
from app.services import agents, events
from app.services.settings_service import SettingsService

log = get_logger(__name__)
public = APIRouter(prefix="/api/public", tags=["website intake"])
router = APIRouter(prefix="/api/agents/{agent_id}/intake", tags=["website intake"])
CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type"}
FIELDS = ("name", "phone", "email", "company", "city", "message", "source", "language")


def _token_key(agent_id: int) -> str:
    return f"intake_token.{agent_id}"


def get_token(agent_id: int, create: bool = True) -> str | None:
    state = SettingsService()
    token = state.get_state(_token_key(agent_id))
    if not token and create:
        token = secrets.token_urlsafe(24)
        state.set_state(_token_key(agent_id), token)
    return token


def _info(agent_id: int) -> dict:
    token = get_token(agent_id)
    url = f"{settings.base_url}/api/public/agents/{agent_id}/leads?token={token}"
    automation = agents.get_automation(agent_id)
    # Both flags: with speed-to-lead off AND auto-dial off, a form enquiry is only saved, never called,
    # and the page must say so instead of promising a queue that nothing works through.
    return {"url": url, "token": token,
            "speed_to_lead": automation.get("speed_to_lead_enabled", False),
            "auto_dial": automation.get("auto_dial_enabled", False),
            "speed_to_lead_min_seconds": automation.get("speed_to_lead_min_seconds", 60),
            "speed_to_lead_max_seconds": automation.get("speed_to_lead_max_seconds", 120)}


@router.get("")
def intake_info(agent_id: int = Depends(workspace)):
    return _info(agent_id)


@router.post("/rotate")
def rotate(agent_id: int = Depends(workspace)):
    """New token: forms using the old URL stop working."""
    SettingsService().set_state(_token_key(agent_id), secrets.token_urlsafe(24))
    events.record("settings.intake", "Website form token regenerated", agent_id=agent_id, actor="admin")
    return _info(agent_id)


@public.options("/agents/{agent_id}/leads")
def preflight(agent_id: int):
    return Response(status_code=204, headers=CORS)


@public.post("/agents/{agent_id}/leads")
async def capture(agent_id: int, request: Request, token: str = ""):
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0].strip()
    if store.rate_limited(f"intake:{agent_id}:{ip}", limit=20, window=600):
        return JSONResponse({"detail": "Too many submissions, try again later."}, status_code=429, headers=CORS)
    expected = get_token(agent_id, create=False) if agents.exists(agent_id) else None
    if not expected or not hmac.compare_digest(token, expected):
        return JSONResponse({"detail": "Invalid form link."}, status_code=403, headers=CORS)

    if "json" in request.headers.get("content-type", ""):
        body = await request.json()
    else:
        body = dict(await request.form())
    data = {k: str(body.get(k) or "").strip()[:500] for k in FIELDS}
    if (body.get("website") or body.get("url_hp")):  # honeypot field filled by bots
        return JSONResponse({"ok": True}, headers=CORS)
    site = urlparse(request.headers.get("origin") or request.headers.get("referer") or "").netloc
    try:
        lead, created, calling = await asyncio.to_thread(ingest, agent_id, data, site)
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=400, headers=CORS)
    return JSONResponse({"ok": True, "lead_id": lead["id"], "created": created, "calling": calling}, headers=CORS)


def ingest(agent_id: int, data: dict, site: str = "") -> tuple[dict, bool, bool]:
    """Create or update the lead, then call it right away when speed to lead is on and calling is allowed."""
    from app.services.call_service import IST, within_calling_hours
    from app.services.crm_service import CRMService

    crm = CRMService(agent_id)
    source = data.get("source") or (f"website:{site}" if site else "website")
    note = f"Website enquiry{f' ({site})' if site else ''}: {data['message']}" if data.get("message") else ""
    existing = crm.find_by_phone(data.get("phone"))
    fields = {k: data[k] for k in ("name", "email", "company", "city") if data.get(k)}
    if data.get("language") in agents.LANGUAGE_CODES:
        fields["language"] = data["language"]
    if existing:
        if note:
            fields["notes"] = "\n".join(x for x in (existing.get("notes"), note) if x)
        lead = crm.update(existing["id"], fields, actor="website", title=f"Website enquiry again from {existing['name'] or existing['phone']}")
        created = False
    else:
        lead = crm.create({**fields, "phone": data.get("phone"), "source": source, "notes": note}, actor="website")
        created = True

    cfg = agents.get_automation(agent_id)
    calling = False
    if cfg.get("speed_to_lead_enabled") and not lead["do_not_call"] and lead.get("phone_valid") is not False and within_calling_hours(cfg):
        # Schedule the call a random 1-2 minutes out (configurable); the callback job dials it.
        low = max(0, int(cfg.get("speed_to_lead_min_seconds", 60)))
        high = max(low, int(cfg.get("speed_to_lead_max_seconds", 120)))
        due = datetime.now(IST) + timedelta(seconds=random.randint(low, high))
        crm.update(lead["id"], {"callback_at": due.strftime("%Y-%m-%d %H:%M"), "call_status": "Pending"}, actor="system")
        events.record("callback.scheduled", f"Website lead: call scheduled for {due:%H:%M}", agent_id=agent_id, lead_id=lead["id"],
                      actor="website")
        calling = True
    elif not lead.get("call_status"):
        crm.update(lead["id"], {"call_status": "Pending"}, actor="system")
    return lead, created, calling
