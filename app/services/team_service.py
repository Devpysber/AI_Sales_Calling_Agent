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
