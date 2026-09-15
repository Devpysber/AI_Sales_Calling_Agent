"""
Authentication.

- Dashboard: POST /api/auth/login -> HMAC-signed HttpOnly cookie.
- Integrations: Authorization: Bearer <API_TOKEN>.
- Public: Plivo webhooks (signature-verified), generated audio, health.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core import store
from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)
COOKIE = "va_session"
TTL = 12 * 3600
PUBLIC_API = ("/api/plivo/", "/api/media/", "/api/health", "/api/auth/login")

if not settings.secret_key:
    log.warning("SECRET_KEY not set: sessions reset on restart and differ between replicas")
_secret = (settings.secret_key or secrets.token_hex(32)).encode()

router = APIRouter(prefix="/api/auth", tags=["auth"])


PROFILE_KEY = "admin_profile"
PBKDF2_ROUNDS = 240_000


def _profile() -> dict:
    from app.services.settings_service import SettingsService
    return SettingsService().get_state(PROFILE_KEY) or {}


def _save_profile(profile: dict):
    from app.services.settings_service import SettingsService
    SettingsService().set_state(PROFILE_KEY, profile)


def _hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ROUNDS).hex()
    return f"pbkdf2${salt}${digest}"


def _password_ok(password: str) -> bool:
    """A password changed from the dashboard (hashed in the database) replaces ADMIN_PASSWORD."""
    stored = _profile().get("password_hash")
    if stored:
        _, salt, _ = stored.split("$")
        return hmac.compare_digest(_hash_password(password, salt), stored)
    return bool(settings.admin_password) and hmac.compare_digest(password, settings.admin_password)


def auth_enabled() -> bool:
    return bool(settings.admin_password or _profile().get("password_hash"))


def _sign(payload: bytes) -> str:
    return hmac.new(_secret, payload, hashlib.sha256).hexdigest()


def make_token(user: str) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"u": user, "exp": int(time.time()) + TTL}).encode())
    return f"{payload.decode()}.{_sign(payload)}"


def read_token(token: str | None) -> str | None:
    if not token or "." not in token:
        return None
    payload, signature = token.rsplit(".", 1)
    if not hmac.compare_digest(_sign(payload.encode()), signature):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return None
    return data["u"] if data.get("exp", 0) > time.time() else None


def current_user(request: Request) -> str | None:
    if not auth_enabled():
        return "admin"
    header = request.headers.get("Authorization", "")
    if settings.api_token and header.startswith("Bearer ") and hmac.compare_digest(header[7:], settings.api_token):
        return "api"
    return read_token(request.cookies.get(COOKIE))


def actor(request: Request) -> str:
    return getattr(request.state, "user", None) or "admin"


async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and not path.startswith(PUBLIC_API) and request.method != "OPTIONS":
        user = current_user(request)
        if not user:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        request.state.user = user
    return await call_next(request)


class Login(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(body: Login, request: Request, response: Response):
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0].strip()
    if store.rate_limited(f"login:{ip}", limit=10, window=900):
        raise HTTPException(429, "Too many attempts. Try again in 15 minutes.")
    if not auth_enabled():
        raise HTTPException(400, "Login is disabled: set ADMIN_PASSWORD on the server.")
    if not (hmac.compare_digest(body.username, settings.admin_username) and _password_ok(body.password)):
        raise HTTPException(401, "Invalid username or password.")
    profile = _profile()
    profile["last_login_at"], profile["last_login_ip"] = int(time.time()), ip
    _save_profile(profile)
    https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    response.set_cookie(COOKIE, make_token(body.username), max_age=TTL, httponly=True, samesite="lax",
                        secure=https, path="/")
    return {"user": body.username}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    profile = _profile()
    return {"user": current_user(request), "auth_enabled": auth_enabled(),
            "display_name": profile.get("display_name") or settings.admin_username, "role": profile.get("role") or "Administrator"}


class ProfileUpdate(BaseModel):
    display_name: str = Field("", max_length=80)
    email: str = Field("", max_length=160)
    phone: str = Field("", max_length=32)
    role: str = Field("", max_length=60)
    company: str = Field("", max_length=120)
    timezone: str = Field("Asia/Kolkata", max_length=64)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=128)


def _public_profile(profile: dict) -> dict:
    return {
        "username": settings.admin_username,
        **{k: profile.get(k, "") for k in ("display_name", "email", "phone", "role", "company")},
        "timezone": profile.get("timezone") or "Asia/Kolkata",
        "password_source": "dashboard" if profile.get("password_hash") else "environment",
        "password_changed_at": profile.get("password_changed_at"),
        "last_login_at": profile.get("last_login_at"), "last_login_ip": profile.get("last_login_ip"),
        "session_hours": TTL // 3600, "api_token_enabled": bool(settings.api_token),
    }


@router.get("/profile")
def get_profile():
    return _public_profile(_profile())


@router.put("/profile")
def update_profile(body: ProfileUpdate):
    email = body.email.strip()
    if email and ("@" not in email or "." not in email.split("@")[-1]):
        raise HTTPException(400, "Enter a valid email address.")
    profile = {**_profile(), **{k: v.strip() for k, v in body.model_dump().items()}}
    _save_profile(profile)
    return _public_profile(profile)


@router.post("/password")
def change_password(body: PasswordChange, request: Request):
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0].strip()
    if store.rate_limited(f"password:{ip}", limit=5, window=900):
        raise HTTPException(429, "Too many attempts. Try again in 15 minutes.")
    if auth_enabled() and not _password_ok(body.current_password):
        raise HTTPException(400, "Current password is incorrect.")
    new = body.new_password
    if new.lower() == new or new.isalpha() or new.isdigit():
        raise HTTPException(400, "Use at least 10 characters mixing upper and lower case letters with numbers or symbols.")
    profile = {**_profile(), "password_hash": _hash_password(new), "password_changed_at": int(time.time())}
    _save_profile(profile)
    return {"ok": True}
