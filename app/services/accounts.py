"""
User accounts: sign-up with email verification, invitations, password reset, roles and optional TOTP MFA.

Sign-up never grants access by itself: a verified self-registered account waits for an administrator to
approve it and pick its role. Invitations carry their role and are active once the password is set.
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import get_db
from app.core.logging import get_logger
from app.models.crm import ADMIN_ROLES, ROLES, User

log = get_logger(__name__)
PBKDF2_ROUNDS = 240_000
TOKEN_TTL = {"verify": timedelta(days=2), "invite": timedelta(days=7), "reset": timedelta(hours=1)}
ROLE_LABELS = {"super_admin": "Super Admin", "org_admin": "Organization Admin", "sales_manager": "Sales Manager",
               "sales_exec": "Sales Executive", "ai_ops": "AI / Operations Manager", "viewer": "Viewer / Analyst"}


class AccountError(ValueError):
    pass


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ROUNDS).hex()
    return f"pbkdf2${salt}${digest}"


def password_matches(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    _, salt, _ = stored.split("$")
    return hmac.compare_digest(hash_password(password, salt), stored)


def _check_password(password: str):
    if len(password) < 8 or password.isalpha() or password.isdigit():
        raise AccountError("Use at least 8 characters with letters and numbers.")


def _issue(user: User, purpose: str) -> str:
    user.token = secrets.token_urlsafe(32)
    user.token_purpose = purpose
    user.token_expires = datetime.utcnow() + TOKEN_TTL[purpose]
    return user.token


def _link(path: str, token: str) -> str:
    return f"{(settings.public_base_url or '').rstrip('/')}/{path}?token={token}"


def _mail(to: str, subject: str, body: str):
    from app.services.notification_service import send_email
    try:
        send_email(to, subject, body, actor="system")
    except Exception as e:  # noqa: BLE001 - the link is also returned to the admin / logged
        log.warning("Account email to %s failed: %s", to, e)


# ---------------- TOTP (RFC 6238) ----------------

def _totp(secret: str, at: float, step: int = 30) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", int(at // step)), hashlib.sha1).digest()
    offset = digest[-1] & 15
    return f"{(struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def totp_ok(secret: str | None, code: str) -> bool:
    code = (code or "").strip().replace(" ", "")
    now = time.time()
    return bool(secret) and any(hmac.compare_digest(_totp(secret, now + d), code) for d in (-30, 0, 30))


# ---------------- queries ----------------

def get(user_id: int | None) -> User | None:
    if not user_id:
        return None
    with get_db() as db:
        return db.get(User, user_id)


def by_email(email: str) -> User | None:
    with get_db() as db:
        return db.scalar(select(User).where(func.lower(User.email) == email.strip().lower()))


def list_users() -> list[dict]:
    with get_db() as db:
        return [u.to_dict() for u in db.scalars(select(User).order_by(User.created_at.desc()))]


def public(user: User) -> dict:
    return {**user.to_dict(), "role_label": ROLE_LABELS.get(user.role, user.role)}


def is_admin(user: User) -> bool:
    return user.role in ADMIN_ROLES


# ---------------- flows ----------------

def signup(email: str, password: str, name: str, organization: str = "", business_type: str = "") -> str:
    email = email.strip().lower()
    _check_password(password)
    with get_db() as db:
        if db.scalar(select(User).where(func.lower(User.email) == email)):
            raise AccountError("An account with this email already exists. Sign in or reset your password.")
        user = User(email=email, name=name.strip() or None, organization=organization.strip() or None,
                    business_type=business_type.strip() or None, role="viewer", status="pending",
                    password_hash=hash_password(password))
        token = _issue(user, "verify")
        db.add(user)
    link = _link("verify", token)
    _mail(email, "Verify your email", f"Welcome{', ' + name if name else ''}!\n\nConfirm your email address:\n{link}\n\nThe link is valid for 48 hours.")
    log.info("Verification link for %s: %s", email, link)
    return link


def _by_token(db, token: str, purpose: str) -> User:
    user = db.scalar(select(User).where(User.token == token, User.token_purpose == purpose)) if token else None
    if not user:
        raise AccountError("This link is invalid or has already been used.")
    if user.token_expires and user.token_expires < datetime.utcnow():
        raise AccountError("This link has expired. Ask for a new one.")
    return user


def verify(token: str) -> dict:
    with get_db() as db:
        user = _by_token(db, token, "verify")
        user.token = user.token_purpose = user.token_expires = None
        user.status = "awaiting_approval"
        out = public(user)
    from app.services.notification_service import notify_admin
    try:
        notify_admin("New account waiting for approval", f"{out['name'] or out['email']} ({out['email']}) verified their email. Approve them under Team & roles.")
    except Exception:  # noqa: BLE001
        pass
    return out


def resend_verification(email: str):
    with get_db() as db:
        user = db.scalar(select(User).where(func.lower(User.email) == email.strip().lower()))
        if not user or user.status != "pending":
            return
        token = _issue(user, "verify")
    _mail(email, "Verify your email", f"Confirm your email address:\n{_link('verify', token)}")


def invite(email: str, role: str, name: str = "", invited_by: str = "admin") -> str:
    if role not in ROLES:
        raise AccountError("Unknown role.")
    email = email.strip().lower()
    with get_db() as db:
        user = db.scalar(select(User).where(func.lower(User.email) == email))
        if user and user.status == "active":
            raise AccountError("This person already has an active account.")
        if not user:
            user = User(email=email)
            db.add(user)
        user.name, user.role, user.status = name.strip() or user.name, role, "invited"
        token = _issue(user, "invite")
    link = _link("invite", token)
    _mail(email, "You're invited to the CRM", f"{invited_by} invited you as {ROLE_LABELS[role]}.\n\nAccept the invitation and set your password:\n{link}\n\nThe link is valid for 7 days.")
    return link


def invitation(token: str) -> dict:
    with get_db() as db:
        user = _by_token(db, token, "invite")
        return {"email": user.email, "name": user.name, "role": user.role, "role_label": ROLE_LABELS[user.role]}


def accept_invite(token: str, password: str, name: str = "") -> User:
    _check_password(password)
    with get_db() as db:
        user = _by_token(db, token, "invite")
        user.password_hash = hash_password(password)
        user.name = name.strip() or user.name
        user.status = "active"
        user.token = user.token_purpose = user.token_expires = None
        return user


def forgot(email: str):
    """Always succeeds from the caller's view so the endpoint does not reveal which emails exist."""
    with get_db() as db:
        user = db.scalar(select(User).where(func.lower(User.email) == email.strip().lower()))
        if not user or user.status in ("disabled", "invited"):
            return
        token = _issue(user, "reset")
    _mail(user.email, "Reset your password", f"Set a new password:\n{_link('reset-password', token)}\n\nThe link is valid for 1 hour. Ignore this email if you did not ask for it.")


def reset(token: str, password: str):
    _check_password(password)
    with get_db() as db:
        user = _by_token(db, token, "reset")
        user.password_hash = hash_password(password)
        user.token = user.token_purpose = user.token_expires = None


def authenticate(email: str, password: str, otp: str = "") -> User:
    """Raises AccountError with a message the login form can show; 'MFA_REQUIRED' asks for the code."""
    with get_db() as db:
        user = db.scalar(select(User).where(func.lower(User.email) == email.strip().lower()))
        if not user or not password_matches(password, user.password_hash):
            raise AccountError("Invalid email or password.")
        if user.status == "pending":
            raise AccountError("Verify your email first: check your inbox for the link.")
        if user.status == "awaiting_approval":
            raise AccountError("Your account is waiting for an administrator to approve it.")
        if user.status != "active":
            raise AccountError("This account is disabled.")
        if user.mfa_enabled:
            if not otp:
                raise AccountError("MFA_REQUIRED")
            if not totp_ok(user.totp_secret, otp):
                raise AccountError("That code is not right. Use the current code from your authenticator app.")
        user.last_login_at = datetime.utcnow()
        return user


def update(user_id: int, values: dict) -> dict:
    with get_db() as db:
        user = db.get(User, user_id)
        if not user:
            raise LookupError("User not found.")
        if "role" in values and values["role"] not in ROLES:
            raise AccountError("Unknown role.")
        for key in ("name", "role", "status", "organization", "business_type"):
            if values.get(key) is not None:
                setattr(user, key, values[key])
        return public(user)


def delete(user_id: int):
    with get_db() as db:
        user = db.get(User, user_id)
        if user:
            db.delete(user)


def change_password(user_id: int, current: str, new: str):
    _check_password(new)
    with get_db() as db:
        user = db.get(User, user_id)
        if not user or not password_matches(current, user.password_hash):
            raise AccountError("Current password is incorrect.")
        user.password_hash = hash_password(new)


def mfa_begin(user_id: int) -> dict:
    secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
    with get_db() as db:
        user = db.get(User, user_id)
        user.totp_secret, user.mfa_enabled = secret, False
        email = user.email
    issuer = settings.app_name.replace(" ", "%20")
    return {"secret": secret, "uri": f"otpauth://totp/{issuer}:{email}?secret={secret}&issuer={issuer}"}


def mfa_confirm(user_id: int, code: str) -> bool:
    with get_db() as db:
        user = db.get(User, user_id)
        if not totp_ok(user.totp_secret, code):
            raise AccountError("That code is not right. Check the time on your phone and try again.")
        user.mfa_enabled = True
        return True


def mfa_disable(user_id: int, password: str):
    with get_db() as db:
        user = db.get(User, user_id)
        if not password_matches(password, user.password_hash):
            raise AccountError("Password is incorrect.")
        user.mfa_enabled, user.totp_secret = False, None
