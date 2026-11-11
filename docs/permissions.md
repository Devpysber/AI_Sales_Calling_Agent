# Users, roles and permissions

Every login is a row in `users` that belongs to an `organizations` row. A session resolves to
`request.state.principal = {user_id, organization_id, role, permissions}` (`app/core/auth.py`
`current_user`). The `"api"` bearer token acts as the owner of the default organisation. Old cookies
without a `uid` still work: admin maps to `owner`, a Sales Team Account to `sales_exec`.

## Roles

| Role | Can |
|---|---|
| owner | everything |
| admin | everything except `billing.manage` |
| manager | all leads / deals / calls / WhatsApp / knowledge / automation, `leads.assign`, `audit.view`; no settings, team or billing |
| sales_exec | `leads.view`, `leads.edit`, `calls.start`, `calls.view`, `whatsapp.send`, `deals.edit`; only their own leads |
| ops | `agents.manage`, `knowledge.manage`, `automation.manage`, `calls.view`, `calls.start`, `leads.view`, `leads.view_all`, `audit.view` |
| viewer | `leads.view`, `leads.view_all`, `calls.view`, `audit.view`; read-only (any non-GET `/api/` call except `/api/auth/*` is 403 in `auth_middleware`) |

Permissions: `leads.view`, `leads.edit`, `leads.delete`, `leads.export`, `leads.assign`,
`leads.view_all`, `deals.edit`, `calls.start`, `calls.view`, `agents.manage`, `knowledge.manage`,
`automation.manage`, `settings.manage`, `team.manage`, `billing.manage`, `whatsapp.send`, `audit.view`.
The source of truth is `PERMISSIONS` in `app/core/permissions.py`.

## Protecting a route

```python
from app.core.permissions import can, require

@router.post("/{lead_id}/deal", dependencies=[Depends(require("deals.edit"))])
def edit_deal(...): ...

if can(request, "leads.export"): ...      # inline check
```

A denied call is `403 "You don't have permission to do this."`. `require(perm, legacy_team=True)` also
lets a Sales Team Account through; use it only on routes those accounts could already use before roles
existed. `require_admin` (owner/admin sessions and the API token) is unchanged.

## Organisation isolation

`workspace` (`app/api/deps.py`) answers 404 for an agent whose `organization_id` differs from the
principal's; `GET /api/agents`, `/live` and `/overview` list only the principal's organisation. New
agents take the creator's organisation. Agents with `organization_id` NULL are adopted by the default
organisation at startup (`user_service.ensure_bootstrap`).

## sales_exec lead scope

Without `leads.view_all`, the `lead_scope` router dependency sets `LEAD_OWNER_SCOPE`; `CRMService`
then filters every query and lookup to `leads.owner_user_id == principal.user_id` (someone else's lead
is a 404) and stamps new leads with the creator. Assign with
`POST /api/agents/{agent_id}/leads/{lead_id}/assign {"user_id": 5}` (`leads.assign`; `null` unassigns),
or in bulk with `POST /api/agents/{agent_id}/leads/bulk/assign {"ids": [...], "user_id": 5}` (same
permission, same validation). `GET /api/agents/{agent_id}/leads/assignable-users` lists active users in
the caller's own organisation, for populating either control.

`GET /api/agents/{agent_id}/analytics/leaderboard` names every rep individually (unlike the aggregate
`/analytics` and `/analytics/channels` reports), so it requires `leads.view_all` on top of `workspace`
scoping — a `sales_exec` gets 403 there even though they can call `/analytics` itself.

## Adding a permission

1. Add the name to `ALL` and to the roles that hold it in `app/core/permissions.py`.
2. Guard the route with `Depends(require("new.perm"))`.
3. Add a case to `tests/test_permissions.py`. The permission list reaches the client in `GET /api/auth/me`.
