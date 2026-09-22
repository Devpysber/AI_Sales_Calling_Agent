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

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from app.api.deps import require_admin, workspace
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
# Field names other form builders / CRMs use for the same thing (WordPress, Webflow, Elementor, Zapier...).
ALIASES = {
    "name": ("full_name", "fullname", "your-name", "your_name", "first_name", "firstname", "fname", "contact_name", "customer_name",
             "lead_name", "patient_name", "student_name", "guest_name", "client_name", "applicant_name", "naam"),
    "phone": ("mobile", "mobile_number", "phone_number", "phonenumber", "contact", "contact_number", "contact_no", "phone_no", "mob",
              "tel", "telephone", "whatsapp", "whatsapp_number", "your-phone", "your_phone", "cell", "number"),
    "email": ("email_address", "emailaddress", "your-email", "your_email", "mail", "e_mail", "e-mail"),
    "company": ("organisation", "organization", "org", "business", "company_name", "firm", "clinic", "school", "college", "institute",
                "shop", "store", "brand", "dealership"),
    "city": ("location", "town", "area", "place", "city_name", "region", "branch", "state", "pincode", "zip", "postcode"),
    "message": ("msg", "comments", "comment", "enquiry", "inquiry", "query", "requirement", "requirements", "details", "your-message",
                "your_message", "description", "notes", "subject", "reason", "interest", "interested_in", "service", "services",
                "product", "course", "treatment", "property", "package", "problem", "issue", "help", "question"),
    "source": ("utm_source", "campaign", "utm_campaign", "utm_medium", "form_name", "form", "form_id", "page", "landing_page",
               "referrer", "ref", "channel", "ad", "adgroup"),
    "language": ("lang", "preferred_language", "locale", "language_preference"),
}
# Never recorded: honeypots, tokens and framework noise.
IGNORED = {"website", "url_hp", "token", "csrf", "_token", "csrfmiddlewaretoken", "g-recaptcha-response", "h-captcha-response", "cf-turnstile-response",
           "submit", "action", "_wpcf7", "_wpcf7_version", "_wpcf7_locale", "_wpcf7_unit_tag", "_wpcf7_container_post", "_wpnonce",
           "formid", "form_id_hidden", "hs_context", "__vtrftk", "entry_id", "gform_submit", "is_submit", "state", "consent", "terms", "agree"}


def normalise(body: dict) -> tuple[dict, dict]:
    """
    (known fields, extra fields) from whatever a website posted: aliases are folded onto the lead
    fields, a "last_name" is joined onto the name, and every other field is kept so nothing a form
    collects is lost - the extras go into the lead's notes as "label: value" lines.
    """
    raw = {str(k).strip().lower(): str(v or "").strip()[:500] for k, v in body.items() if v not in (None, "", [], {})}
    data: dict = {}
    for field in FIELDS:
        for key in (field,) + ALIASES.get(field, ()):
            if raw.get(key):
                data[field] = raw.pop(key)
                break
        else:
            data[field] = ""
    for key in ("last_name", "lastname", "surname"):
        if raw.get(key):
            data["name"] = f"{data['name']} {raw.pop(key)}".strip()
    if data["message"] and not data["message"].strip():
        data["message"] = ""
    # drop the alias spellings that lost the race above, honeypots and noise
    extra = {k: v for k, v in raw.items() if k not in IGNORED and not k.startswith("_")
             and all(k not in ALIASES.get(f, ()) and k != f for f in FIELDS)}
    return data, extra


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
            "speed_to_lead_min_seconds": automation.get("speed_to_lead_min_seconds", 3600),
            "speed_to_lead_max_seconds": automation.get("speed_to_lead_max_seconds", 7200)}


@router.get("")
def intake_info(agent_id: int = Depends(workspace)):
    return _info(agent_id)


@router.post("/rotate", dependencies=[Depends(require_admin)])
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
    if (body.get("website") or body.get("url_hp")):  # honeypot field filled by bots
        return JSONResponse({"ok": True}, headers=CORS)
    data, extra = normalise(body)
    site = urlparse(request.headers.get("origin") or request.headers.get("referer") or "").netloc
    try:
        lead, created, calling = await asyncio.to_thread(ingest, agent_id, data, site, extra)
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=400, headers=CORS)
    return JSONResponse({"ok": True, "lead_id": lead["id"], "created": created, "calling": calling}, headers=CORS)


def ingest(agent_id: int, data: dict, site: str = "", extra: dict | None = None) -> tuple[dict, bool, bool]:
    """Create or update the lead, then schedule the call when speed to lead is on and calling is allowed."""
    from app.services.call_service import IST, next_calling_window
    from app.services.crm_service import CRMService

    crm = CRMService(agent_id)
    source = data.get("source") or (f"website:{site}" if site else "website")
    lines = [f"Website enquiry{f' ({site})' if site else ''}: {data['message'].replace(chr(0xFFFD), '')}"] if data.get("message") else []
    # Whatever else the form collected (budget, model, preferred time, ...) travels with the lead and is
    # read by the agent as context on the call, so each website can ask its own questions.
    lines += [f"{k.replace('_', ' ').replace('-', ' ').capitalize()}: {v}" for k, v in (extra or {}).items()][:20]
    if extra and not data.get("message"):
        lines.insert(0, f"Website enquiry{f' ({site})' if site else ''}")
    note = "\n".join(lines)
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
    # A paused agent still captures the enquiry, but nothing is scheduled or promised to the customer.
    if (cfg.get("speed_to_lead_enabled") and not agents.is_paused(agent_id)
            and not lead["do_not_call"] and lead.get("phone_valid") is not False):
        # A random 1-2 hours out (configurable), counted from the next opening of the calling window when
        # the form arrives outside it, so the lead page always shows when the call will happen.
        low = max(0, int(cfg.get("speed_to_lead_min_seconds", 3600)))
        high = max(low, int(cfg.get("speed_to_lead_max_seconds", 7200)))
        due = next_calling_window(cfg) + timedelta(seconds=random.randint(low, high))
        crm.update(lead["id"], {"callback_at": due.strftime("%Y-%m-%d %H:%M"), "call_status": "Pending"}, actor="system")
        events.record("callback.scheduled", f"Website lead: call scheduled for {due:%d %b %H:%M}", agent_id=agent_id, lead_id=lead["id"],
                      actor="website")
        calling = True
        if lead.get("email") and cfg.get("ai_auto_emails", True):
            # The moment the enquiry lands: acknowledge it and say when the call will come.
            from app.services.notification_service import email_sent, send_email
            persona = agents.get_profile(agent_id)
            company = persona.get("company_name") or (agents.get(agent_id) or {}).get("name") or "our team"
            body = "\n".join(filter(None, [
                f"Thanks {lead.get('name') or ''}, we have your enquiry".replace("  ", " "),
                "",
                f"We will call you around {due:%I:%M %p} on {due:%d %b} to help with it. If another time suits you better, reply to this email.",
                "",
                f"Your message: {data['message']}" if data.get("message") else None,
                "" if data.get("message") else None,
                persona.get("agent_name") or company,
                company,
            ]))
            email_sent(send_email(lead["email"], f"{company}: we received your enquiry", body, lead_id=lead["id"], agent_id=agent_id, actor="ai"))
    elif not lead.get("call_status"):
        crm.update(lead["id"], {"call_status": "Pending"}, actor="system")
    return lead, created, calling
