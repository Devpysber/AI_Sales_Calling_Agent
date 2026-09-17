import json
import base64
import hashlib
import time
from typing import Dict, Any

from cryptography.fernet import Fernet

from app.core.database import SessionLocal
from app.models.app_setting import AppSetting

# Cache secrets for 10 seconds to avoid hitting the DB on every single config read
_cache: Dict[str, Any] = {"time": 0, "secrets": {}}
CACHE_TTL = 10

def _get_cipher():
    # To avoid circular imports, import settings here
    from app.core.config import settings
    if not settings.secret_key:
        return None
    # Derive a 32-byte URL-safe base64 key from the app's SECRET_KEY
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.secret_key.encode()).digest())
    return Fernet(key)

def get_all_secrets_from_db() -> dict:
    global _cache
    now = time.time()
    if now - _cache["time"] < CACHE_TTL:
        return _cache["secrets"]

    with SessionLocal() as db:
        row = db.get(AppSetting, "system.secrets")
        if not row or not row.value:
            _cache = {"time": now, "secrets": {}}
            return {}
            
        cipher = _get_cipher()
        if not cipher:
            return {}
            
        try:
            decrypted = cipher.decrypt(row.value.encode()).decode()
            secrets = json.loads(decrypted)
            _cache = {"time": now, "secrets": secrets}
            return secrets
        except Exception:
            return {}

def get_secret_from_db(key: str):
    secrets = get_all_secrets_from_db()
    return secrets.get(key)

def set_secrets_in_db(new_secrets: dict):
    global _cache
    with SessionLocal() as db:
        row = db.get(AppSetting, "system.secrets")
        current = {}
        cipher = _get_cipher()
        if not cipher:
            raise ValueError("SECRET_KEY must be set in .env to use database secrets.")
        
        if row and row.value:
            try:
                decrypted = cipher.decrypt(row.value.encode()).decode()
                current = json.loads(decrypted)
            except Exception:
                pass
        
        # We allow removing secrets by setting them to empty string
        for k, v in new_secrets.items():
            if v == "" and k in current:
                del current[k]
            elif v != "":
                current[k] = v
                
        encrypted = cipher.encrypt(json.dumps(current).encode()).decode()
        
        if row:
            row.value = encrypted
        else:
            db.add(AppSetting(key="system.secrets", value=encrypted))
        db.commit()
        
    # Invalidate cache
    _cache["time"] = 0
