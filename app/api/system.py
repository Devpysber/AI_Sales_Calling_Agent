import asyncio
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text

from app.api.deps import require_admin
from app.core import store
from app.core.config import settings
from app.core.secrets import get_all_secrets_from_db, set_secrets_in_db
from app.core.database import engine
from app.services import tts
from app.services.notification_service import email_configured, email_detail

router = APIRouter(prefix="/api", tags=["system"])
STATIC_AUDIO = Path(__file__).resolve().parents[2] / "audio" / "static"


# ---------------- health ----------------

@router.get("/health")
def health():
    checks = {}
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"
    checks["store"] = "ok" if store.store.ping() else "error"
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse({"status": "ok" if ok else "degraded", "version": settings.app_version, **checks},
                        status_code=200 if ok else 503)


# ---------------- media (public: fetched by Plivo) ----------------

@router.get("/media/audio/{audio_id}.wav")
def media_audio(audio_id: str):
    if not audio_id.isalnum() or len(audio_id) > 64:
        raise HTTPException(404)
    audio = tts.load_audio(audio_id)
    if audio is None:
        raise HTTPException(404, "Audio expired")
    return Response(audio, media_type="audio/wav", headers={"Cache-Control": "private, max-age=600"})


@router.get("/media/static/{name}")
def media_static(name: str):
    path = STATIC_AUDIO / Path(name).name
    if not path.exists():
        raise HTTPException(404)
    return Response(path.read_bytes(), media_type="audio/wav" if path.suffix == ".wav" else "audio/mpeg",
                    headers={"Cache-Control": "public, max-age=86400"})


# ---------------- integrations ----------------

def _mask(value: str) -> str:
    return f"{value[:6]}…{value[-4:]}" if len(value) > 12 else ("set" if value else "")


def _plivo():
    try:
        from app.services.plivo_service import PlivoService
        return {"ok": True, **PlivoService().health()}
    except Exception as e:
        return {"ok": False, "detail": str(e)}


def _openrouter():
    if not settings.openrouter_api_key:
        return {"ok": False, "detail": "OPENROUTER_API_KEY not set"}
    try:
        data = httpx.get("https://openrouter.ai/api/v1/key", timeout=8,
                         headers={"Authorization": f"Bearer {settings.openrouter_api_key}"}).json()["data"]
        models = [m.strip() for m in settings.openrouter_models.split(",") if m.strip()]
        unknown = _unknown_models(models)
        result = {"ok": not unknown, "key": _mask(settings.openrouter_api_key), "free_tier": data.get("is_free_tier"),
                  "usage": data.get("usage"), "limit_remaining": data.get("limit_remaining"),
                  "models": models, "unknown_models": unknown}
        if unknown:
            # A retired model id fails every live turn before the next model is tried: that was
            # the "not a valid model ID" behind dropped calls, and nothing on this page showed it.
            result["detail"] = (f"OpenRouter no longer has {', '.join(unknown)}. Replace it in OPENROUTER_MODELS "
                                "— every live reply wastes a request on it first.")
        return result
    except Exception as e:
        return {"ok": False, "detail": str(e)}


def _unknown_models(models: list[str]) -> list[str]:
    """Configured model ids missing from OpenRouter's public catalogue. Empty if the catalogue can't be read."""
    try:
        catalogue = {m["id"] for m in httpx.get("https://openrouter.ai/api/v1/models", timeout=8).json()["data"]}
    except Exception:  # noqa: BLE001 - never report a model as missing because the check itself failed
        return []
    return [m for m in models if m not in catalogue]


def _sarvam():
    if not settings.sarvam_api_key:
        return {"ok": False, "detail": "SARVAM_API_KEY not set"}
    try:
        res = httpx.get("https://api.sarvam.ai/v1/models", timeout=8, headers={"api-subscription-key": settings.sarvam_api_key})
        res.raise_for_status()
        return {"ok": True, "key": _mask(settings.sarvam_api_key), "tts_model": settings.sarvam_tts_model,
                "llm_model": settings.sarvam_llm_model}
    except Exception as e:
        return {"ok": False, "detail": str(e)}


def _public_url():
    if not settings.public_base_url:
        return {"ok": False, "detail": "PUBLIC_BASE_URL not set"}
    try:
        res = httpx.get(settings.base_url + "/api/health", timeout=8)
        return {"ok": res.status_code == 200, "url": settings.base_url,
                "detail": None if res.status_code == 200 else f"HTTP {res.status_code}"}
    except Exception as e:
        return {"ok": False, "url": settings.base_url, "detail": f"Not reachable: {e}"}


@router.get("/system/status")
async def status():
    plivo, openrouter, sarvam, public = await asyncio.gather(
        asyncio.to_thread(_plivo), asyncio.to_thread(_openrouter), asyncio.to_thread(_sarvam), asyncio.to_thread(_public_url))
    return {
        "plivo": plivo, "openrouter": openrouter, "sarvam": sarvam, "public_url": public,
        "llm_providers": settings.llm_providers.split(","),
        "signature_validation": settings.plivo_validate_signature,
        "email": {"ok": email_configured(), "detail": email_detail()},
        "infrastructure": {
            "environment": settings.environment,
            "database": engine.dialect.name,
            "store": "redis" if settings.redis_url else "in-memory (single instance)",
            "scheduler_in_api": settings.run_scheduler,
            "version": settings.app_version,
        },
    }


# ---------------- inbound calls on the Plivo number ----------------

def _plivo_action(action: str):
    from app.services.plivo_service import PlivoService
    try:
        service = PlivoService()
        return getattr(service, action)()
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001 - surface Plivo errors to the admin
        raise HTTPException(502, f"Plivo: {e}")


@router.get("/system/inbound")
async def inbound_status():
    return await asyncio.to_thread(_plivo_action, "inbound_status")


@router.post("/system/inbound/connect")
async def inbound_connect():
    """Route inbound calls on PLIVO_PHONE_NUMBER to this app (previous application is remembered)."""
    return await asyncio.to_thread(_plivo_action, "connect_inbound")


@router.post("/system/inbound/restore")
async def inbound_restore():
    return await asyncio.to_thread(_plivo_action, "restore_inbound")


@router.get("/system/alerts")
async def alerts(request: Request, refresh: bool = False):
    """Navbar bell: reminders across agents plus live provider balances (cached briefly; refresh=true re-fetches)."""
    user = getattr(request.state, "user", "admin")
    payload = getattr(request.state, "token_payload", {})
    unlocked = payload.get("unlocked", []) if user == "team" else None
    from app.services import alerts as alert_service
    return await asyncio.to_thread(alert_service.summary, refresh, unlocked)


@router.post("/system/alerts/snooze")
async def snooze_alert(body: dict):
    """Hide one reminder (or a low-credit popup, key 'popup:<Provider>') for a number of hours."""
    from app.services import alerts as alert_service
    key = str(body.get("key") or "")
    if not key:
        raise HTTPException(400, "key is required")
    await asyncio.to_thread(alert_service.snooze, key, float(body.get("hours") or 4))
    return {"ok": True}
# ---------------- team members ----------------

from app.services.settings_service import SettingsService
from pydantic import BaseModel, Field
import hashlib
import re
import uuid

class TeamMemberUpdate(BaseModel):
    name: str
    email: str
    password: str | None = None
    max_agents: int = 1      # workspaces this member may create; the admin raises it per person
    phone: str = ""          # the line a transferred call rings, and where the team is reached
    role: str = "Sales"      # what they handle, shown on the team list
    notes: str = ""

# Credentials live in the encrypted AppSetting row that app/core/config.py reads; everything else
# (pricing, credits) stays in SettingsService, where analytics and alerts read it.
CREDENTIAL_KEYS = {"resend_api_key", "email_from", "email_reply_to", "smtp_host", "smtp_port",
                   "smtp_username", "smtp_password", "smtp_from", "openrouter_api_key",
                   "sarvam_api_key", "plivo_auth_id", "plivo_auth_token", "plivo_phone_number"}

# A credential that is the wrong shape is only noticed later, as a provider error on a live call
# ("Invalid auth_id supplied: <an email address>"), so reject the obvious mistakes at save time.
CREDENTIAL_FORMATS = {
    "plivo_auth_id": (r"^[A-Z]{2}[A-Z0-9]{18}$",
                      "The Plivo Auth ID is 20 characters starting with MA or SA — find it on the Plivo console overview, not your email address."),
    "plivo_phone_number": (r"^\+[1-9]\d{7,14}$",
                           "Enter the Plivo number in international format, e.g. +919876543210."),
    "openrouter_api_key": (r"^sk-or-\S+$", "An OpenRouter key starts with sk-or-."),
    "resend_api_key": (r"^re_\S+$", "A Resend key starts with re_."),
}


def _check_credential(key: str, value: str):
    rule = CREDENTIAL_FORMATS.get(key)
    if not value or not rule:
        return                                            # empty clears the override; unchecked keys pass
    pattern, message = rule
    if not re.match(pattern, value):
        raise HTTPException(400, message)


def _hint(value: str) -> str:
    """Enough of a stored credential to recognise it, never enough to use it."""
    if len(value) <= 6:
        return "•" * len(value)
    return f"{value[:4]}…{value[-4:]}"


@router.get("/system/secrets", dependencies=[Depends(require_admin)])
async def get_secrets():
    # The encrypted AppSetting row is the one app.core.config actually reads; SettingsService held an
    # older plaintext copy that nothing consumed, so edits saved there never took effect.
    stored = {k: v for k, v in get_all_secrets_from_db().items() if v}
    secrets = {**(SettingsService().get_state("secrets") or {}),
               **{k: "********" for k in stored}}
    # A masked field alone cannot show that the wrong value is saved; a short hint can.
    secrets["_hints"] = {k: _hint(v) for k, v in stored.items()}
    sarvam_credits = secrets.get("sarvam_credits")
    sarvam_credits_updated_at = secrets.get("sarvam_credits_updated_at")
    
    if sarvam_credits is not None and sarvam_credits_updated_at:
        try:
            from app.services.analytics import get_sarvam_usage_since
            usage_cost = get_sarvam_usage_since(sarvam_credits_updated_at)
            reduced = float(sarvam_credits) - usage_cost
            secrets["sarvam_credits"] = f"{reduced:.2f}" if reduced > 0 else "0.00"
        except Exception:
            pass
            
    return secrets

@router.post("/system/secrets", dependencies=[Depends(require_admin)])
async def update_secrets(body: dict):
    """
    Credentials go to the encrypted row config reads; pricing and credits stay in SettingsService,
    where analytics and the balance alerts read them. Masked fields keep their stored value.
    """
    plain = SettingsService().get_state("secrets") or {}
    credentials: dict[str, str] = {}
    for key, raw in body.items():
        if key.startswith("_"):
            continue                                      # display-only fields such as _hints
        value = "" if raw is None else str(raw).strip()   # numeric fields arrive as numbers, not strings
        if value.startswith("*"):
            continue                                      # untouched masked field: keep what is stored
        if key in CREDENTIAL_KEYS:
            _check_credential(key, value)
            credentials[key] = value                       # "" removes the override
            continue
        if value and key == "sarvam_credits" and plain.get(key) != value:
            plain["sarvam_credits_updated_at"] = time.time()
        if value:
            plain[key] = value
        else:
            plain.pop(key, None)
    if credentials:
        try:
            set_secrets_in_db(credentials)
        except ValueError as e:
            raise HTTPException(400, str(e))
    SettingsService().set_state("secrets", plain)
    saved = sorted([k for k, v in credentials.items() if v] + [k for k in body if k in plain])
    return {"ok": True, "saved": saved, "cleared": sorted(k for k, v in credentials.items() if not v)}

@router.get("/system/team-members", dependencies=[Depends(require_admin)])
async def get_team_members():
    from app.services import agents as agent_service
    from app.services import team_service
    members = SettingsService().get_state("team_members") or []
    for m in members:
        m.pop("password_hash", None)
        # Shown next to the limit so the admin can see who is out of room before they ask.
        m["max_agents"] = team_service.agent_limit(m)
        m["created_agents"] = agent_service.created_count(m.get("id") or "")
    return {"members": members}

@router.post("/system/team-members", dependencies=[Depends(require_admin)])
async def add_team_member(body: TeamMemberUpdate):
    members = SettingsService().get_state("team_members") or []
    if any(m.get("email") == body.email for m in members):
        raise HTTPException(400, "A team member with this email already exists.")
        
    salt = uuid.uuid4().hex
    pwd = body.password or "12345678"
    h = hashlib.pbkdf2_hmac("sha256", pwd.encode(), salt.encode(), 240_000).hex()
    
    new_member = {
        "id": uuid.uuid4().hex,
        "name": body.name.strip(),
        "email": body.email.strip().lower(),
        "phone": body.phone.strip(),
        "role": body.role.strip() or "Sales",
        "notes": body.notes.strip(),
        "password_hash": f"{salt}${h}",
        "max_agents": body.max_agents,
        "created_agents": 0,
        "created_at": int(time.time())
    }
    members.append(new_member)
    SettingsService().set_state("team_members", members)
    return {"ok": True}

class TeamMemberEdit(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    role: str | None = None
    notes: str | None = None
    max_agents: int | None = Field(None, ge=0, le=100)


@router.put("/system/team-members/{member_id}", dependencies=[Depends(require_admin)])
async def update_team_member(member_id: str, body: TeamMemberEdit):
    members = SettingsService().get_state("team_members") or []
    member = next((m for m in members if m.get("id") == member_id), None)
    if not member:
        raise HTTPException(404, "Member not found.")
    values = body.model_dump(exclude_none=True)
    if "email" in values:
        email = values["email"].strip().lower()
        if any(m.get("email") == email and m.get("id") != member_id for m in members):
            raise HTTPException(400, "A team member with this email already exists.")
        values["email"] = email
    for key in ("name", "phone", "role", "notes"):
        if key in values:
            values[key] = values[key].strip()
    member.update(values)
    SettingsService().set_state("team_members", members)
    safe = {k: v for k, v in member.items() if k != "password_hash"}
    return {"ok": True, "member": safe}


@router.delete("/system/team-members/{member_id}", dependencies=[Depends(require_admin)])
async def delete_team_member(member_id: str):
    members = SettingsService().get_state("team_members") or []
    members = [m for m in members if m.get("id") != member_id]
    SettingsService().set_state("team_members", members)
    return {"ok": True}

class TeamMemberPasswordUpdate(BaseModel):
    password: str

# Path must match the client: it calls /api/system/team-members/<id>/password, so the previous
# /api/team-members/... route 404'd and the Change Password button never did anything.
@router.put("/system/team-members/{member_id}/password", dependencies=[Depends(require_admin)])
async def update_team_member_password(member_id: str, body: TeamMemberPasswordUpdate):
    import uuid, hashlib
    from fastapi import HTTPException
    from app.services.settings_service import SettingsService
    members = SettingsService().get_state("team_members") or []
    member = next((m for m in members if m.get("id") == member_id), None)
    if not member:
        raise HTTPException(404, "Member not found.")
    salt = uuid.uuid4().hex
    pwd = body.password
    h = hashlib.pbkdf2_hmac("sha256", pwd.encode(), salt.encode(), 240_000).hex()
    member["password_hash"] = f"{salt}${h}"
    SettingsService().set_state("team_members", members)
    return {"ok": True}
