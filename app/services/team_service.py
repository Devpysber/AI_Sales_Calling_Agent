"""
The sales team behind an agent: who they are, which line rings them, and how they are described
on a call.

Team members are stored once (Integrations & system -> Sales Team Accounts) and used in three
places: their logins, the addresses a message to "the team" reaches, and — from here — the numbers
a forwarded call hunts through and the people the agent can name to a caller.
"""

from app.services.settings_service import SettingsService


def members() -> list[dict]:
    return SettingsService().get_state("team_members") or []


def _digits(value: str) -> str:
    return "".join(c for c in (value or "") if c.isdigit())


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
        people.extend(agents.get_profile(agent_id).get("team_members") or [])
    people.extend(members())
    for m in people:
        if _digits(m.get("phone", "")) == wanted and (m.get("name") or "").strip():
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
