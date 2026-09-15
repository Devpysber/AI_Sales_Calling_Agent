"""
Live call session state, shared across API replicas via the store.

A Plivo call makes many separate webhook requests (answer, input, wait,
hangup) that a load balancer may route to different instances, so all
per-call state lives here rather than in process memory.
"""

import uuid
from datetime import datetime, timezone

from app.core import store

TTL = 2 * 3600


def _key(session_id: str) -> str:
    return f"session:{session_id}"


def create(**fields) -> dict:
    session = {
        "id": uuid.uuid4().hex,
        "agent_id": None,
        "lead_id": None,
        "call_id": None,
        "lead": {},
        "language": "en-IN",
        "history": [],          # [{role: assistant|customer, text, at}]
        "silent_prompts": 0,
        "pending": None,        # {"state": "processing"|"ready"|"error", ...}
        "latencies": [],
        "crm": {},
        "ended": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        **fields,
    }
    save(session)
    return session


def get(session_id: str | None) -> dict | None:
    if not session_id:
        return None
    return store.get_json(_key(session_id))


def save(session: dict):
    store.set_json(_key(session["id"]), session, ttl=TTL)


def update(session_id: str, **fields) -> dict | None:
    session = get(session_id)
    if session is None:
        return None
    session.update(fields)
    save(session)
    return session


def add_turn(session: dict, role: str, text: str):
    session["history"].append({"role": role, "text": text, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})


def active_count() -> int:
    return len(store.store.keys(store.PREFIX + "session:"))
