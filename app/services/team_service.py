"""
The sales team behind an agent: who they are, which line rings them, and how they are described
on a call.

Team members are stored once (Integrations & system -> Sales Team Accounts) and used in three
places: their logins, the addresses a message to "the team" reaches, and — from here — the numbers
a forwarded call hunts through and the people the agent can name to a caller.
"""

import ast
from app.services.crm_service import normalize_phone
from app.services.settings_service import SettingsService

def members() -> list[dict]:
    raw_members = SettingsService().get_state("team_members") or []
    clean_members = []
    for m in raw_members:
        if isinstance(m, str):
            try:
                # Handle corrupted DB state where dicts were saved as strings
                clean_members.append(ast.literal_eval(m))
            except Exception:
                pass
        else:
            clean_members.append(m)
    return clean_members


def _digits(value: str) -> str:
    return "".join(c for c in (normalize_phone(value) or "") if c.isdigit())


def call_numbers(role: str | None = None) -> list[str]:
    """Team lines, in the order they were added, as digits Plivo can dial."""
    out: list[str] = []
    for m in members():
        if role and (m.get("role") or "").lower() != role.lower():
            continue
        number = _digits(m.get("phone", ""))
        if 11 <= len(number) <= 15 and number not in out:
            out.append(number)
    return out


def directory_lines() -> list[str]:
    """One spoken-safe line per member, for the prompt: a name the agent can promise a caller."""
    lines = []
    for m in members():
        name = (m.get("name") or "").strip()
        if not name:
            continue
        role = (m.get("role") or "Sales").strip()
        notes = (m.get("notes") or "").strip()
        lines.append(f"- {name} — {role}{' — ' + notes if notes else ''}")
    return lines


def name_for(number: str | None, agent_id: int | None = None) -> str | None:
    """
    The person behind a phone number: the agent's own team list first, then the shared Sales Team Accounts.
    Used so a transferred call reads "Ashish Sharma" rather than a bare number.
    """
    wanted = _digits(number or "")
    if not wanted:
        return None
    people: list[dict] = []
    if agent_id is not None:
        from app.services import agents
        local_members = agents.get_profile(agent_id).get("team_members")
        if isinstance(local_members, list):
            people.extend(local_members)
    
    global_members = members()
    if isinstance(global_members, list):
        people.extend(global_members)
        
    for m in people:
        if isinstance(m, dict) and _digits(m.get("phone", "")) == wanted and (m.get("name") or "").strip():
            return m["name"].strip()
    return None


DEFAULT_AGENT_LIMIT = 1


def by_id(member_id: str) -> dict | None:
    return next((m for m in members() if m.get("id") == member_id), None)


def agent_limit(member: dict) -> int:
    """How many workspaces this member may create. Missing or invalid values fall back to one."""
    try:
        limit = int(member.get("max_agents", DEFAULT_AGENT_LIMIT))
    except (TypeError, ValueError):
        return DEFAULT_AGENT_LIMIT
    return max(0, limit)


def is_team_number(number: str | None, agent_id: int | None = None) -> bool:
    """
    True when this number belongs to the people behind the agent: a team member's line, or the
    number the agent transfers callers to. They ring their own agent to check it, not to buy.
    """
    wanted = _digits(number or "")
    if not wanted:
        return False
    if name_for(number, agent_id) is not None:
        return True
    lines = call_numbers()
    if agent_id is not None:
        from app.services import agents
        raw = str(agents.get_profile(agent_id).get("transfer_number") or "")
        lines = lines + [_digits(part) for part in raw.split(",")]
    return wanted in [line for line in lines if line]


def is_admin_number(number: str | None) -> bool:
    """True when this number belongs to the account owner (Admin profile phone) or a member with role 'Admin'."""
    wanted = _digits(number or "")
    if not wanted:
        return False
    try:
        from app.core.auth import _profile
        if _digits(str(_profile().get("phone") or "")) == wanted:
            return True
    except Exception:  # noqa: BLE001 - profile store unavailable: fall through to the member list
        pass
    for m in members():
        if (m.get("role") or "Sales").lower() == "admin":
            if _digits(m.get("phone", "")) == wanted:
                return True
    return False


def agents_for(number: str | None) -> list[int]:
    """Agents whose own team list carries this phone number, in id order; empty for a number no agent lists."""
    wanted = _digits(number or "")
    if not wanted:
        return []
    from app.services import agents
    out = []
    for aid in agents.ids():
        try:
            local = agents.get_profile(aid).get("team_members") or []
        except Exception:  # noqa: BLE001 - a broken profile is simply not that colleague's agent
            continue
        if any(isinstance(m, dict) and _digits(str(m.get("phone") or "")) == wanted for m in local):
            out.append(aid)
    return out
