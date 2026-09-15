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
from pydantic import BaseModel

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


def auth_enabled() -> bool:
    return bool(settings.admin_password)


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
    if not (hmac.compare_digest(body.username, settings.admin_username) and
            hmac.compare_digest(body.password, settings.admin_password)):
        raise HTTPException(401, "Invalid username or password.")
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
    return {"user": current_user(request), "auth_enabled": auth_enabled()}
