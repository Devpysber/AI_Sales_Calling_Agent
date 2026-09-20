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
PUBLIC_API = ("/api/plivo/", "/api/media/", "/api/health", "/api/auth/login", "/api/public/")

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


def make_token(payload_dict: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(payload_dict).encode())
    return f"{payload.decode()}.{_sign(payload)}"


def read_token(token: str | None) -> dict | None:
    if not token or "." not in token:
        return None
    payload, signature = token.rsplit(".", 1)
    if not hmac.compare_digest(_sign(payload.encode()), signature):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return None
    return data if data.get("exp", 0) > time.time() else None


def current_user(request: Request) -> str | None:
    if not auth_enabled():
        return "admin"
    header = request.headers.get("Authorization", "")
    if settings.api_token and header.startswith("Bearer ") and hmac.compare_digest(header[7:], settings.api_token):
        return "api"
    payload = read_token(request.cookies.get(COOKIE))
    if payload:
        if payload.get("u") == "team":
            # A member removed from Sales Team Accounts must not keep working until the cookie expires.
            from app.services import team_service
            if not team_service.by_id(payload.get("team_id")):
                return None
        request.state.token_payload = payload
        return payload["u"]
    return None


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
    username: str = ""
    email: str = ""
    password: str

def login_email() -> str:
    return (_profile().get("email") or settings.admin_email or "").strip().lower()

@router.post("/login")
def login(body: Login, request: Request, response: Response):
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0].strip()
    if store.rate_limited(f"login:{ip}", limit=10, window=900):
        raise HTTPException(429, "Too many attempts. Try again in 15 minutes.")
    if not auth_enabled():
        raise HTTPException(400, "Login is disabled: set ADMIN_PASSWORD on the server.")
    identifier = (body.email or body.username).strip().lower()
    expected = login_email()
    known = hmac.compare_digest(identifier, expected) if expected else hmac.compare_digest(identifier, settings.admin_username.lower())
    target_user = settings.admin_username
    if not (known and _password_ok(body.password)):
        # Check Multi-Member Team Logins
        from app.services.settings_service import SettingsService
        members = SettingsService().get_state("team_members") or []
        team_ok = False
        team_member = None
        
        for m in members:
            m_email = (m.get("email") or "").strip().lower()
            m_hash = m.get("password_hash")
            if m_email and identifier == m_email and m_hash:
                salt, h = m_hash.split("$")
                if hmac.compare_digest(h, hashlib.pbkdf2_hmac("sha256", body.password.encode(), salt.encode(), PBKDF2_ROUNDS).hex()):
                    team_ok = True
                    team_member = m
                    break
                
        if team_ok:
            target_user = "team"
            payload = {"u": target_user, "team_id": team_member["id"], "exp": int(time.time()) + TTL, "unlocked": []}
            https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
            response.set_cookie(COOKIE, make_token(payload), max_age=TTL, httponly=True, samesite="lax", secure=https, path="/")
            return {"user": target_user}
        else:
            raise HTTPException(401, "Invalid email or password.")
    else:
        profile = _profile()
        profile["last_login_at"], profile["last_login_ip"] = int(time.time()), ip
        _save_profile(profile)
        
    payload = {"u": target_user, "exp": int(time.time()) + TTL}
    https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    response.set_cookie(COOKIE, make_token(payload), max_age=TTL, httponly=True, samesite="lax", secure=https, path="/")
    return {"user": target_user}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    user = current_user(request)
    # If this is a team member session, look up their own record — not the admin profile
    if user == "team":
        payload = getattr(request.state, "token_payload", {})
        team_id = payload.get("team_id")
        from app.services.settings_service import SettingsService
        members = SettingsService().get_state("team_members") or []
        member = next((m for m in members if m.get("id") == team_id), None)
        from app.services import agents as agent_service
        from app.services import team_service
        limit = team_service.agent_limit(member) if member else 0
        used = agent_service.created_count(team_id) if team_id else 0
        return {
            "user": user,
            "auth_enabled": auth_enabled(),
            "display_name": (member.get("name") or member.get("email") or "Team Member") if member else "Team Member",
            "role": "Team Member",
            # The client hides the create button once the admin's limit is used up.
            "agent_limit": limit,
            "agents_created": used,
            "can_create_agent": used < limit,
        }
    profile = _profile()
    return {"user": user, "auth_enabled": auth_enabled(),
            "display_name": profile.get("display_name") or settings.admin_username, "role": profile.get("role") or "Administrator"}


class ProfileUpdate(BaseModel):
    current_password: str = ""  # required when the sign-in email changes
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
        "login_email": login_email(),
        **{k: profile.get(k, "") for k in ("display_name", "email", "phone", "role", "company")},
        "timezone": profile.get("timezone") or "Asia/Kolkata",
        "password_source": "dashboard" if profile.get("password_hash") else "environment",
        "password_changed_at": profile.get("password_changed_at"),
        "last_login_at": profile.get("last_login_at"), "last_login_ip": profile.get("last_login_ip"),
        "session_hours": TTL // 3600, "api_token_enabled": bool(settings.api_token),
    }


@router.get("/profile")
def get_profile(request: Request):
    user = current_user(request)
    if user == "team":
        payload = getattr(request.state, "token_payload", {})
        team_id = payload.get("team_id")
        from app.services.settings_service import SettingsService
        members = SettingsService().get_state("team_members") or []
        member = next((m for m in members if m.get("id") == team_id), None)
        if not member:
            raise HTTPException(404)
        return {
            "username": member.get("name", ""),
            "login_email": member.get("email", ""),
            "display_name": member.get("name", ""),
            "email": member.get("email", ""),
            "phone": "",
            "role": "Team Member",
            "company": "",
            "timezone": "Asia/Kolkata",
            "password_source": "dashboard",
            "password_changed_at": member.get("password_changed_at") or member.get("created_at"),
            "last_login_at": None,
            "last_login_ip": None,
            "session_hours": TTL // 3600,
            "api_token_enabled": False
        }
    return _public_profile(_profile())


@router.put("/profile")
def update_profile(body: ProfileUpdate, request: Request):
    user = current_user(request)
    if user == "team":
        payload = getattr(request.state, "token_payload", {})
        team_id = payload.get("team_id")
        from app.services.settings_service import SettingsService
        members = SettingsService().get_state("team_members") or []
        member = next((m for m in members if m.get("id") == team_id), None)
        if member:
            member["name"] = body.display_name
            SettingsService().set_state("team_members", members)
        return get_profile(request)
    email = body.email.strip()
    if email and ("@" not in email or "." not in email.split("@")[-1]):
        raise HTTPException(400, "Enter a valid email address.")
    current = _profile()
    if email.lower() != login_email() and auth_enabled():
        if not email:
            raise HTTPException(400, "The sign-in email can't be empty.")
        if not _password_ok(body.current_password):
            raise HTTPException(400, "Enter your current password to change the sign-in email.")
    fields = body.model_dump(exclude={"current_password"})
    fields["email"] = email.lower()
    profile = {**current, **{k: v.strip() for k, v in fields.items()}}
    _save_profile(profile)
    return _public_profile(profile)


@router.post("/password")
def change_password(body: PasswordChange, request: Request):
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0].strip()
    if store.rate_limited(f"password:{ip}", limit=5, window=900):
        raise HTTPException(429, "Too many attempts. Try again in 15 minutes.")
        
    if current_user(request) == "team":
        payload = getattr(request.state, "token_payload", {})
        team_id = payload.get("team_id")
        from app.services.settings_service import SettingsService
        members = SettingsService().get_state("team_members") or []
        member = next((m for m in members if m.get("id") == team_id), None)
        if not member: raise HTTPException(404)
        h = hashlib.pbkdf2_hmac("sha256", body.current_password.encode(), member["password_hash"].split("$")[0].encode(), 240_000).hex()
        if member["password_hash"] != f"{member['password_hash'].split('$')[0]}${h}":
            raise HTTPException(400, "Current password is incorrect.")
        new = body.new_password
        if new.lower() == new or new.isalpha() or new.isdigit(): raise HTTPException(400, "Use at least 10 characters mixing upper and lower case letters with numbers or symbols.")
        salt = __import__('uuid').uuid4().hex
        new_h = hashlib.pbkdf2_hmac("sha256", new.encode(), salt.encode(), 240_000).hex()
        member["password_hash"] = f"{salt}${new_h}"
        member["password_changed_at"] = int(time.time())
        SettingsService().set_state("team_members", members)
        return {"ok": True}

    if auth_enabled() and not _password_ok(body.current_password):
        raise HTTPException(400, "Current password is incorrect.")
    new = body.new_password
    if new.lower() == new or new.isalpha() or new.isdigit():
        raise HTTPException(400, "Use at least 10 characters mixing upper and lower case letters with numbers or symbols.")
    profile = {**_profile(), "password_hash": _hash_password(new), "password_changed_at": int(time.time())}
    _save_profile(profile)
    return {"ok": True}
