import asyncio
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text

from app.core import store
from app.core.config import settings
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
