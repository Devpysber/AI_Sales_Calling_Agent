"""
Runtime tuning overrides: plaintext settings an admin edits in the web app, read by app.core.config on every access.

Stored in AppSetting "state.runtime" as {name: value}; a 10s cache keeps a live turn from touching the DB per read.
"""
import json
import time

_cache = {"time": 0.0, "values": {}}
CACHE_TTL = 10


def _load() -> dict:
    now = time.time()
    if now - _cache["time"] < CACHE_TTL:
        return _cache["values"]
    from app.core.database import SessionLocal
    from app.models.app_setting import AppSetting
    values = {}
    try:
        with SessionLocal() as db:
            row = db.get(AppSetting, "state.runtime")
            values = json.loads(row.value) if row and row.value else {}
    except Exception:  # noqa: BLE001 - before migrations or without a DB the env values stand
        values = {}
    _cache.update(time=now, values=values if isinstance(values, dict) else {})
    return _cache["values"]


def runtime_override(name: str):
    """The admin's value for `name`, cast to the setting's type; None when unset or unusable."""
    from app.core.config import RUNTIME_KEYS
    raw = _load().get(name)
    if raw in (None, ""):
        return None
    kind = RUNTIME_KEYS[name][0]
    try:
        return kind(float(raw)) if kind is int else kind(raw)
    except (TypeError, ValueError):
        return None


def validate(name: str, raw) -> str:
    """Normalise one admin-entered value or raise ValueError with the reason in plain words."""
    from app.core.config import RUNTIME_KEYS
    if name not in RUNTIME_KEYS:
        raise ValueError(f"{name} cannot be changed from the panel.")
    kind, label, _help, bounds = RUNTIME_KEYS[name]
    value = str(raw if raw is not None else "").strip()
    if not value:
        return ""
    if kind in (int, float):
        try:
            number = int(float(value)) if kind is int else float(value)
        except ValueError:
            raise ValueError(f"{label} must be a number.")
        if bounds and not (bounds[0] <= number <= bounds[1]):
            raise ValueError(f"{label} must be between {bounds[0]} and {bounds[1]}.")
        return str(number)
    if name in ("llm_providers", "summary_llm_providers"):
        parts = [p.strip().lower() for p in value.split(",") if p.strip()]
        if not parts or any(p not in ("sarvam", "openrouter") for p in parts):
            raise ValueError(f"{label}: use only sarvam and openrouter, comma-separated.")
        return ",".join(parts)
    if name in ("openrouter_models", "openrouter_fallback_models"):
        parts = [p.strip() for p in value.split(",") if p.strip()]
        if not parts or any("/" not in p for p in parts):
            raise ValueError(f"{label}: model ids look like provider/model, comma-separated.")
        return ",".join(parts)
    if name == "stt_language_mode":
        if value.lower() not in ("call", "auto"):
            raise ValueError("Speech recognition language must be 'call' or 'auto'.")
        return value.lower()
    if name == "heal_export_token" and len(value) < 16:
        raise ValueError("Heal export token must be at least 16 characters.")
    return value


def save(values: dict) -> dict:
    """Validate and store every given key; an empty value clears the override (env/default applies again)."""
    clean = {k: validate(k, v) for k, v in values.items()}
    from app.core.database import SessionLocal
    from app.models.app_setting import AppSetting
    with SessionLocal() as db:
        row = db.get(AppSetting, "state.runtime")
        current = json.loads(row.value) if row and row.value else {}
        for k, v in clean.items():
            if v:
                current[k] = v
            else:
                current.pop(k, None)
        if row:
            row.value = json.dumps(current)
        else:
            db.add(AppSetting(key="state.runtime", value=json.dumps(current)))
        db.commit()
    _cache["time"] = 0.0
    return current


def effective() -> dict:
    """What the app is running with right now, and where each value comes from (panel or server)."""
    from app.core.config import RUNTIME_KEYS, settings
    stored = _load()
    out = {}
    for name, (kind, label, help_text, bounds) in RUNTIME_KEYS.items():
        out[name] = {"label": label, "help": help_text, "value": getattr(settings, name),
                     "source": "panel" if stored.get(name) else "server",
                     "kind": "number" if kind in (int, float) else "text",
                     "min": bounds[0] if bounds else None, "max": bounds[1] if bounds else None,
                     "secret": name == "heal_export_token"}
    return out
