"""
Sign-up, verification, invitations, password reset (public, under /api/auth/), account self-service and
user / role administration (/api/accounts/).
"""

import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.api.deps import require_admin
from app.core import store
from app.core.auth import TTL, _set_session, actor
from app.services import accounts, events

public = APIRouter(prefix="/api/auth", tags=["accounts"])
router = APIRouter(prefix="/api/accounts", tags=["accounts"])


def _ip(request: Request) -> str:
    return request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0].strip()


def _limit(request: Request, key: str):
    if store.rate_limited(f"{key}:{_ip(request)}", limit=10, window=900):
        raise HTTPException(429, "Too many attempts. Try again in 15 minutes.")


class SignUp(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=255)
    password: str = Field(min_length=8, max_length=128)
    name: str = Field("", max_length=120)
    organization: str = Field("", max_length=160)
    business_type: str = Field("", max_length=80)


class Token(BaseModel):
    token: str


class Email(BaseModel):
    email: str


class NewPassword(BaseModel):
    token: str
    password: str = Field(min_length=8, max_length=128)
    name: str = ""


def _err(e: Exception):
    raise HTTPException(400, str(e))


@public.post("/signup")
def signup(body: SignUp, request: Request):
    _limit(request, "signup")
    try:
        accounts.signup(body.email, body.password, body.name, body.organization, body.business_type)
    except accounts.AccountError as e:
        _err(e)
    events.record("user.signup", f"{body.name or body.email} signed up", body.organization or None, actor=body.email)
    return {"ok": True, "message": "Check your inbox for a verification link."}


@public.post("/verify")
def verify(body: Token):
    try:
        user = accounts.verify(body.token)
    except accounts.AccountError as e:
        _err(e)
    events.record("user.verified", f"{user['email']} verified their email", actor=user["email"])
    return {"ok": True, "status": user["status"]}


@public.post("/resend")
def resend(body: Email, request: Request):
    _limit(request, "resend")
    accounts.resend_verification(body.email)
    return {"ok": True}


@public.post("/forgot")
def forgot(body: Email, request: Request):
    _limit(request, "forgot")
    accounts.forgot(body.email)
    return {"ok": True, "message": "If an account uses that email, a reset link is on its way."}


@public.post("/reset")
def reset(body: NewPassword):
    try:
        accounts.reset(body.token, body.password)
    except accounts.AccountError as e:
        _err(e)
    return {"ok": True}


@public.get("/invitation")
def invitation(token: str):
    try:
        return accounts.invitation(token)
    except accounts.AccountError as e:
        _err(e)


@public.post("/accept-invite")
def accept_invite(body: NewPassword, request: Request, response: Response):
    try:
        user = accounts.accept_invite(body.token, body.password, body.name)
    except accounts.AccountError as e:
        _err(e)
    events.record("user.joined", f"{user.name or user.email} joined the organization", actor=user.email)
    _set_session(request, response, {"u": "user", "uid": user.id, "role": user.role, "exp": int(time.time()) + TTL})
    return {"user": "admin" if accounts.is_admin(user) else "member"}


# ---------------- signed-in account ----------------

def _me(request: Request):
    account = getattr(request.state, "account", None)
    if not account:
        raise HTTPException(400, "This sign-in is not a user account (the built-in admin uses Admin profile).")
    return account


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


class Code(BaseModel):
    code: str = ""
    password: str = ""


@router.get("/me")
def me(request: Request):
    return accounts.public(_me(request))


@router.post("/me/password")
def change_password(body: PasswordChange, request: Request):
    try:
        accounts.change_password(_me(request).id, body.current_password, body.new_password)
    except accounts.AccountError as e:
        _err(e)
    return {"ok": True}


@router.post("/me/mfa/setup")
def mfa_setup(request: Request):
    return accounts.mfa_begin(_me(request).id)


@router.post("/me/mfa/confirm")
def mfa_confirm(body: Code, request: Request):
    try:
        accounts.mfa_confirm(_me(request).id, body.code)
    except accounts.AccountError as e:
        _err(e)
    return {"ok": True}


@router.post("/me/mfa/disable")
def mfa_disable(body: Code, request: Request):
    try:
        accounts.mfa_disable(_me(request).id, body.password)
    except accounts.AccountError as e:
        _err(e)
    return {"ok": True}


# ---------------- administration ----------------

class Invite(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    role: str
    name: str = ""


class UserPatch(BaseModel):
    name: str | None = None
    role: str | None = None
    status: str | None = Field(None, pattern="^(active|disabled|awaiting_approval)$")


@router.get("/roles")
def roles():
    return [{"value": k, "label": v} for k, v in accounts.ROLE_LABELS.items()]


@router.get("", dependencies=[Depends(require_admin)])
def list_users():
    return [{**u, "role_label": accounts.ROLE_LABELS.get(u["role"], u["role"])} for u in accounts.list_users()]


@router.post("/invite", dependencies=[Depends(require_admin)])
def invite(body: Invite, request: Request):
    account = getattr(request.state, "account", None)
    if body.role == "super_admin" and account and account.role != "super_admin":
        raise HTTPException(403, "Only a Super Admin can invite another Super Admin.")
    try:
        link = accounts.invite(body.email, body.role, body.name, invited_by=account.name if account else "The administrator")
    except accounts.AccountError as e:
        _err(e)
    events.record("user.invited", f"Invited {body.email} as {accounts.ROLE_LABELS[body.role]}", actor=actor(request))
    return {"ok": True, "link": link}


@router.patch("/{user_id}", dependencies=[Depends(require_admin)])
def update_user(user_id: int, body: UserPatch, request: Request):
    account = getattr(request.state, "account", None)
    if account and account.id == user_id and (body.role or body.status):
        raise HTTPException(400, "You cannot change your own role or status.")
    try:
        user = accounts.update(user_id, body.model_dump(exclude_none=True))
    except LookupError as e:
        raise HTTPException(404, str(e))
    except accounts.AccountError as e:
        _err(e)
    events.record("user.updated", f"Updated {user['email']}: {user['role_label']}, {user['status']}", actor=actor(request))
    return user


@router.delete("/{user_id}", dependencies=[Depends(require_admin)])
def delete_user(user_id: int, request: Request):
    account = getattr(request.state, "account", None)
    if account and account.id == user_id:
        raise HTTPException(400, "You cannot delete your own account.")
    accounts.delete(user_id)
    events.record("user.deleted", f"Removed user #{user_id}", actor=actor(request))
    return {"ok": True}
