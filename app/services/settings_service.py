"""
Typed settings validation and scheduler bookkeeping stored in the database.
Per-agent profile and automation live on the agent (see services/agents.py);
secrets never live here, they stay in the environment.
"""

import json

from app.core.database import get_db
from app.models.app_setting import AppSetting


def coerce(defaults: dict, values: dict) -> dict:
    """Validate `values` against the keys and types of `defaults`."""
    unknown = set(values) - set(defaults)
    if unknown:
        raise ValueError(f"Unknown settings: {', '.join(sorted(unknown))}")
    clean = {}
    for key, value in values.items():
        expected = type(defaults[key])
        if expected is bool:
            value = bool(value)
        elif expected is int:
            value = int(value)
        elif expected is list:
            value = [int(v) for v in value]
        else:
            value = str(value)
        clean[key] = value
    return clean


class SettingsService:

    def get_state(self, key: str, default=None):
        with get_db() as db:
            row = db.get(AppSetting, f"state.{key}")
            return json.loads(row.value) if row else default

    def set_state(self, key: str, value):
        with get_db() as db:
            row = db.get(AppSetting, f"state.{key}")
            if row:
                row.value = json.dumps(value)
            else:
                db.add(AppSetting(key=f"state.{key}", value=json.dumps(value)))

    def delete_state(self, prefix: str):
        with get_db() as db:
            db.query(AppSetting).filter(AppSetting.key.startswith(f"state.{prefix}")).delete(synchronize_session=False)
