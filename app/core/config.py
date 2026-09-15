"""
Application configuration, loaded from environment / .env.

Import with: from app.core.config import settings
"""

from functools import lru_cache

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    # ---------------- Application ----------------
    app_name: str = Field("AI Voice Sales Agent", alias="APP_NAME")
    app_version: str = Field("3.0.0", alias="APP_VERSION")
    environment: str = Field("development", alias="ENVIRONMENT")  # development | production
    debug: bool = Field(False, alias="DEBUG")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    # Public HTTPS URL Plivo uses for webhooks and audio
    public_base_url: str = Field("", alias="PUBLIC_BASE_URL")
    cors_origins: str = Field("", alias="CORS_ORIGINS")  # comma separated, dev only

    # ---------------- Infrastructure ----------------
    database_url: str = Field("sqlite:///data/voiceagent.db", alias="DATABASE_URL")
    db_pool_size: int = Field(10, alias="DB_POOL_SIZE")
    # Required when running more than one API instance
    redis_url: str = Field("", alias="REDIS_URL")
    # Run the scheduler inside the API process (single-instance setups).
    # In scaled deployments set false on API replicas and run `python -m app.worker`.
    run_scheduler: bool = Field(True, alias="RUN_SCHEDULER")
    # Apply DB migrations on startup. Disable on scaled API replicas and run the `migrate` job once per deploy.
    run_migrations: bool = Field(True, alias="RUN_MIGRATIONS")

    # ---------------- Auth ----------------
    admin_username: str = Field("admin", alias="ADMIN_USERNAME")
    admin_password: str = Field("", alias="ADMIN_PASSWORD")
    secret_key: str = Field("", alias="SECRET_KEY")
    api_token: str = Field("", alias="API_TOKEN")

    # ---------------- LLM ----------------
    # Comma-separated provider order; the first that answers wins.
    llm_providers: str = Field("openrouter,sarvam", alias="LLM_PROVIDERS")
    openrouter_api_key: str = Field("", alias="OPENROUTER_API_KEY")
    openrouter_models: str = Field(
        "nvidia/nemotron-3.5-lightning:free,inclusionai/ling-3.0-flash-vl:free,nex-agi/nex-n2.5-mini:free",
        alias="OPENROUTER_MODELS",
    )
    openrouter_embedding_model: str = Field("openai/text-embedding-3-small", alias="OPENROUTER_EMBEDDING_MODEL")
    # Post-call summaries are not latency-sensitive: try free/cheap models first, paid Sarvam as fallback.
    summary_llm_providers: str = Field("openrouter,sarvam", alias="SUMMARY_LLM_PROVIDERS")
    llm_timeout_seconds: float = Field(4.5, alias="LLM_TIMEOUT_SECONDS")

    # ---------------- Sarvam AI ----------------
    sarvam_api_key: str = Field("", alias="SARVAM_API_KEY")
    sarvam_llm_model: str = Field("sarvam-105b", alias="SARVAM_LLM_MODEL")
    sarvam_reasoning_effort: str = Field("", alias="SARVAM_REASONING_EFFORT")
    sarvam_tts_model: str = Field("bulbul:v3", alias="SARVAM_TTS_MODEL")
    sarvam_tts_sample_rate: int = Field(8000, alias="SARVAM_TTS_SAMPLE_RATE")
    # indicf5: local open-source model on this machine's GPU. sarvam: Sarvam bulbul API.
    tts_engine: str = Field("sarvam", alias="TTS_ENGINE")
    sarvam_stt_model: str = Field("saaras:v3", alias="SARVAM_STT_MODEL")

    # ---------------- Voice pipeline ----------------
    # stream: Plivo bidirectional audio stream + Sarvam streaming STT (sub-second turn detection, barge-in).
    # gather: legacy Plivo <GetInput> speech recognition + webhook polling.
    voice_mode: str = Field("stream", alias="VOICE_MODE")
    # Pause after the caller's last words before the agent answers
    turn_end_grace_ms: int = Field(150, alias="TURN_END_GRACE_MS")
    # Speech-to-text is billed per second of audio sent: skip long silences (keeps pre-roll and a silent tail for VAD)
    stt_silence_gate: bool = Field(True, alias="STT_SILENCE_GATE")

    # ---------------- Plivo ----------------
    plivo_auth_id: str = Field("", alias="PLIVO_AUTH_ID")
    plivo_auth_token: str = Field("", alias="PLIVO_AUTH_TOKEN")
    plivo_phone_number: str = Field("", alias="PLIVO_PHONE_NUMBER")
    plivo_validate_signature: bool = Field(True, alias="PLIVO_VALIDATE_SIGNATURE")

    # ---------------- Email ----------------
    smtp_host: str = Field("", alias="SMTP_HOST")
    smtp_port: int = Field(587, alias="SMTP_PORT")
    smtp_username: str = Field("", alias="SMTP_USERNAME")
    smtp_password: str = Field("", alias="SMTP_PASSWORD")
    smtp_from: str = Field("", alias="SMTP_FROM")

    # ---------------- Files ----------------
    upload_dir: str = Field("data/uploads", alias="UPLOAD_DIR")
    legacy_excel_file: str = Field("data/Leads.xlsx", alias="EXCEL_FILE")

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def base_url(self) -> str:
        return self.public_base_url.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
