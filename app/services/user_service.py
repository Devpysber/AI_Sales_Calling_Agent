"""
Users and organisations.

Logins used to be one admin (admin_profile) plus "Sales Team Accounts" (team_members). Both are now
mirrored as rows in `users` by ensure_bootstrap, so a session can carry a real user id and role. The
old stores stay authoritative for passwords of team accounts (their password page still writes there).
"""

import hashlib
import hmac
from datetime import datetime, timezone

from sqlalchemy import select, update

from app.core.config import settings
from app.core.database import get_db
from app.core.logging import get_logger
from app.models.agent import Agent
from app.models.user import Organization, User

log = get_logger(__name__)
PBKDF2_ROUNDS = 240_000


def _check_hash(stored: str, password: str) -> bool:
    """Both formats in the wild: "pbkdf2$<salt hex>$<digest>" (admin) and "<salt>$<digest>" (team accounts)."""
    parts = stored.split("$")
    try:
        if len(parts) == 3 and parts[0] == "pbkdf2":
            salt, digest = bytes.fromhex(parts[1]), parts[2]
        elif len(parts) == 2:
            salt, digest = parts[0].encode(), parts[1]
        else:
            return False
    except ValueError:
        return False
    return hmac.compare_digest(digest, hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS).hex())


def verify_password(user: dict, password: str) -> bool:
    if user.get("legacy_team_id"):
        # The team account's own record is where its password page writes: follow it, and a removed member fails.
        from app.services import team_service
        member = team_service.by_id(user["legacy_team_id"])
        stored = (member or {}).get("password_hash")
        return bool(stored) and _check_hash(stored, password)
    stored = user.get("password_hash")
    if stored:
        return _check_hash(stored, password)
    return user.get("role") == "owner" and bool(settings.admin_password) and hmac.compare_digest(password, settings.admin_password)


def _row(user: User | None) -> dict | None:
    if user is None:
        return None
    return {**user.to_dict(), "password_hash": user.password_hash, "legacy_team_id": user.legacy_team_id}


def find_by_email(email: str) -> dict | None:
    with get_db() as db:
        return _row(db.scalar(select(User).where(User.email == (email or "").strip().lower())))


def get(user_id: int) -> dict | None:
    with get_db() as db:
        return _row(db.get(User, user_id))


def list_users() -> list[dict]:
    """A bulk listing, unlike get()/find_by_email() which exist for auth checks: strips password_hash
    so a future caller can't accidentally leak it the way a single get() reasonably carries it."""
    with get_db() as db:
        return [{k: v for k, v in _row(u).items() if k != "password_hash"} for u in db.scalars(select(User))]


def default_org() -> dict | None:
    with get_db() as db:
        org = db.scalar(select(Organization).order_by(Organization.id).limit(1))
        return org.to_dict() if org else None


def organization(org_id: int | None) -> dict | None:
    with get_db() as db:
        org = db.get(Organization, org_id) if org_id else None
        return org.to_dict() if org else None


def touch_login(user_id: int):
    with get_db() as db:
        db.execute(update(User).where(User.id == user_id).values(
            last_login_at=datetime.now(timezone.utc).replace(tzinfo=None)))


def _member_email(member: dict) -> str:
    return (member.get("email") or "").strip().lower()


def ensure_bootstrap():
    """Adopt today's logins and agents into an organisation. Safe to call any number of times."""
    from app.core import auth
    from app.services import team_service
    profile = auth._profile()
    members = [m for m in team_service.members() if isinstance(m, dict) and m.get("id") and _member_email(m)]
    with get_db() as db:
        org = db.scalar(select(Organization).order_by(Organization.id).limit(1))
        if org is None:
            org = Organization(name=(profile.get("company") or "").strip() or "Default")
            db.add(org)
            db.flush()
        db.execute(update(Agent).where(Agent.organization_id.is_(None)).values(organization_id=org.id))
        taken = set(db.scalars(select(User.email)))
        if not taken:
            email = auth.login_email() or settings.admin_username.strip().lower()
            db.add(User(organization_id=org.id, email=email, role="owner", status="active",
                        name=profile.get("display_name") or settings.admin_username,
                        password_hash=profile.get("password_hash") or None, phone=profile.get("phone") or None))
            taken.add(email)
        known = set(db.scalars(select(User.legacy_team_id).where(User.legacy_team_id.is_not(None))))
        for m in members:
            if m["id"] in known or _member_email(m) in taken:
                continue
            db.add(User(organization_id=org.id, email=_member_email(m), role="sales_exec", status="active",
                        name=m.get("name") or None, password_hash=m.get("password_hash"),
                        legacy_team_id=m["id"], phone=m.get("phone") or None))
            taken.add(_member_email(m))


def sync_owner():
    """After the admin changes their email, name or password: keep the owner's row in step."""
    from app.core import auth
    ensure_bootstrap()
    profile = auth._profile()
    email = auth.login_email() or settings.admin_username.strip().lower()
    with get_db() as db:
        owner = db.scalar(select(User).where(User.role == "owner").order_by(User.id).limit(1))
        if owner is None:
            return
        if db.scalar(select(User.id).where(User.email == email, User.id != owner.id)) is None:
            owner.email = email
        owner.name = profile.get("display_name") or owner.name
        owner.password_hash = profile.get("password_hash") or None


def sync_team_member(member: dict):
    """After a Sales Team Account is added or edited."""
    ensure_bootstrap()
    email = _member_email(member)
    with get_db() as db:
        user = db.scalar(select(User).where(User.legacy_team_id == member.get("id")))
        if user is None:
            return
        user.status = "active"
        user.name = member.get("name") or user.name
        user.phone = member.get("phone") or None
        if email and email != user.email and db.scalar(select(User.id).where(User.email == email)) is None:
            user.email = email


def disable_team_member(member_id: str):
    with get_db() as db:
        db.execute(update(User).where(User.legacy_team_id == member_id).values(status="disabled"))


def find_by_legacy_team_id(team_id: str | None) -> dict | None:
    if not team_id:
        return None
    with get_db() as db:
        return _row(db.scalar(select(User).where(User.legacy_team_id == team_id)))
