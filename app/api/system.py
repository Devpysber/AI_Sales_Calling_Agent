import asyncio
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException
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
        return {"ok": True, "key": _mask(settings.openrouter_api_key), "free_tier": data.get("is_free_tier"),
                "usage": data.get("usage"), "limit_remaining": data.get("limit_remaining"),
                "models": settings.openrouter_models.split(",")}
    except Exception as e:
        return {"ok": False, "detail": str(e)}


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
async def alerts(refresh: bool = False):
    """Navbar bell: reminders across agents plus live provider balances (cached briefly; refresh=true re-fetches)."""
    from app.services import alerts as alert_service
    return await asyncio.to_thread(alert_service.summary, refresh)


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
from pydantic import BaseModel
import hashlib
import uuid
import time

class TeamMemberUpdate(BaseModel):
    name: str
    email: str
    password: str | None = None
    max_agents: int = 2

# Credentials live in the encrypted AppSetting row that app/core/config.py reads; everything else
# (pricing, credits) stays in SettingsService, where analytics and alerts read it.
CREDENTIAL_KEYS = {"resend_api_key", "email_from", "email_reply_to", "smtp_host", "smtp_port",
                   "smtp_username", "smtp_password", "smtp_from", "openrouter_api_key",
                   "sarvam_api_key", "plivo_auth_id", "plivo_auth_token", "plivo_phone_number"}


@router.get("/system/secrets", dependencies=[Depends(require_admin)])
async def get_secrets():
    # The encrypted AppSetting row is the one app.core.config actually reads; SettingsService held an
    # older plaintext copy that nothing consumed, so edits saved there never took effect.
    secrets = {**(SettingsService().get_state("secrets") or {}),
               **{k: "********" for k, v in get_all_secrets_from_db().items() if v}}
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
    stored = dict(get_all_secrets_from_db())
    plain = SettingsService().get_state("secrets") or {}
    credentials: dict[str, str] = {}
    for key, raw in body.items():
        value = "" if raw is None else str(raw).strip()   # numeric fields arrive as numbers, not strings
        if value.startswith("*"):
            continue                                      # untouched masked field: keep what is stored
        if key in CREDENTIAL_KEYS:
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

@router.get("/system/team-members")
async def get_team_members():
    members = SettingsService().get_state("team_members") or []
    for m in members:
        m.pop("password_hash", None)
    return {"members": members}

@router.post("/system/team-members")
async def add_team_member(body: TeamMemberUpdate):
    members = SettingsService().get_state("team_members") or []
    if any(m.get("email") == body.email for m in members):
        raise HTTPException(400, "A team member with this email already exists.")
        
    salt = uuid.uuid4().hex
    pwd = body.password or "12345678"
    h = hashlib.pbkdf2_hmac("sha256", pwd.encode(), salt.encode(), 240_000).hex()
    
    new_member = {
        "id": uuid.uuid4().hex,
        "name": body.name,
        "email": body.email,
        "password_hash": f"{salt}${h}",
        "max_agents": body.max_agents,
        "created_agents": 0,
        "created_at": int(time.time())
    }
    members.append(new_member)
    SettingsService().set_state("team_members", members)
    return {"ok": True}

@router.delete("/system/team-members/{member_id}")
async def delete_team_member(member_id: str):
    members = SettingsService().get_state("team_members") or []
    members = [m for m in members if m.get("id") != member_id]
    SettingsService().set_state("team_members", members)
    return {"ok": True}

class TeamMemberPasswordUpdate(BaseModel):
    password: str

@router.put("/team-members/{member_id}/password")
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
