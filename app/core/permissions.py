"""
Roles and permissions.

The session resolves to a principal on request.state.principal:
    {"user_id": int | None, "organization_id": int | None, "role": str, "permissions": set[str]}
Guard a route with `dependencies=[Depends(require("deals.edit"))]` or test inline with `can(request, perm)`.
See docs/permissions.md.
"""

from fastapi import HTTPException, Request

ROLES = ("owner", "admin", "manager", "sales_exec", "ops", "viewer")

ALL = {
    "leads.view", "leads.edit", "leads.delete", "leads.export", "leads.assign", "leads.view_all",
    "deals.edit", "calls.start", "calls.view", "agents.manage", "knowledge.manage", "automation.manage",
    "settings.manage", "team.manage", "billing.manage", "whatsapp.send", "audit.view",
}

PERMISSIONS: dict[str, set[str]] = {
    "owner": set(ALL),
    "admin": ALL - {"billing.manage"},
    "manager": {"leads.view", "leads.edit", "leads.delete", "leads.export", "leads.assign", "leads.view_all",
                "deals.edit", "calls.start", "calls.view", "whatsapp.send", "knowledge.manage",
                "automation.manage", "audit.view"},
    # No leads.view_all: a sales exec only sees the leads assigned to them (owner_user_id).
    "sales_exec": {"leads.view", "leads.edit", "calls.start", "calls.view", "whatsapp.send", "deals.edit"},
    "ops": {"agents.manage", "knowledge.manage", "automation.manage", "calls.view", "calls.start", "leads.view",
            "leads.view_all", "audit.view"},
    "viewer": {"leads.view", "leads.view_all", "calls.view", "audit.view"},
}


def make_principal(user_id: int | None, organization_id: int | None, role: str) -> dict:
    return {"user_id": user_id, "organization_id": organization_id, "role": role,
            "permissions": set(PERMISSIONS.get(role, ()))}


def principal_of(request: Request) -> dict | None:
    return getattr(request.state, "principal", None)


def can(request: Request, perm: str) -> bool:
    principal = principal_of(request)
    return bool(principal) and perm in principal["permissions"]


def require(perm: str, legacy_team: bool = False):
    """403 unless the caller holds `perm`.

    legacy_team=True also lets a Sales Team Account (a "team" session in the sales_exec role) through:
    those accounts could already do this before roles existed, and a first pass must not take it away.
    """
    def dependency(request: Request):
        if can(request, perm):
            return
        principal = principal_of(request)
        if legacy_team and principal and principal["role"] == "sales_exec" and getattr(request.state, "user", "") == "team":
            return
        raise HTTPException(403, "You don't have permission to do this.")
    return dependency
