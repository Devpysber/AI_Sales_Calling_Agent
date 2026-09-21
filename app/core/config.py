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
    # Sign-in email. The email saved on the Admin profile page takes precedence.
    admin_email: str = Field("", alias="ADMIN_EMAIL")
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
    # Last-resort tier: tried once after every primary provider/model fails (e.g. paid accounts out of credit).
    # Free-tier models cap prompt tokens, so they get a compact prompt trimmed to FALLBACK_PROMPT_CHAR_BUDGET.
    openrouter_fallback_models: str = Field(
        "nvidia/nemotron-3.5-lightning:free,nex-agi/nex-n2.5-mini:free",
        alias="OPENROUTER_FALLBACK_MODELS",
    )
    # Max characters of the compact prompt (system prompt without knowledge/brief/past calls + last turns)
    fallback_prompt_char_budget: int = Field(6000, alias="FALLBACK_PROMPT_CHAR_BUDGET")
    openrouter_embedding_model: str = Field("openai/text-embedding-3-small", alias="OPENROUTER_EMBEDDING_MODEL")
    # Post-call summaries are not latency-sensitive: try free/cheap models first, paid Sarvam as fallback.
    # Sarvam first: a summary is one request (~Rs 0.02) against ~Rs 0.15-0.20 of Gemini tokens; OpenRouter is the fallback.
    summary_llm_providers: str = Field("sarvam,openrouter", alias="SUMMARY_LLM_PROVIDERS")
    llm_timeout_seconds: float = Field(4.5, alias="LLM_TIMEOUT_SECONDS")
    # Characters of system prompt + history a live turn may carry (agent.build_messages trims knowledge first).
    # The rendered system prompt alone is ~27k chars, so a smaller budget silently drops every Knowledge passage.
    llm_prompt_char_budget: int = Field(32000, alias="LLM_PROMPT_CHAR_BUDGET")
    # Wall clock for one live turn across every provider and model in LLM_PROVIDERS. Without it a dead
    # provider set walks the whole chain (13.5s per model, then again without tools) past the 45s reply
    # deadline, and the caller hears nothing at all. Inside this budget the turn falls back to a spoken line.
    llm_stream_budget_seconds: float = Field(15.0, alias="LLM_STREAM_BUDGET_SECONDS")

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
    # Cost estimates on the Analytics page: fill in the rates from your provider invoices (0 = not shown)
    cost_currency: str = Field("₹", alias="COST_CURRENCY")
    cost_per_call_minute: float = Field(0, alias="COST_PER_CALL_MINUTE")
    cost_per_10k_tts_chars: float = Field(0, alias="COST_PER_10K_TTS_CHARS")
    cost_per_stt_hour: float = Field(0, alias="COST_PER_STT_HOUR")
    cost_per_llm_request: float = Field(0, alias="COST_PER_LLM_REQUEST")
    # Per-token LLM pricing (₹ per 1M tokens). When set and calls carry token counts, Analytics bills by tokens
    # instead of the flat per-request rate. sarvam-105b: 29.28 in / 73.20 out.
    cost_per_1m_llm_input: float = Field(0, alias="COST_PER_1M_LLM_INPUT")
    cost_per_1m_llm_output: float = Field(0, alias="COST_PER_1M_LLM_OUTPUT")
    # Playground rehearsals a team member may start per calendar month (0 = unlimited). Admin and API token are exempt.
    playground_monthly_limit: int = Field(5, alias="PLAYGROUND_MONTHLY_LIMIT")
    # Cost guardrails for a live call (see voice_stream silence_loop): the agent is steered to close
    # near the TTS character budget or the target duration; the persona's max_call_minutes stays the hard cap.
    tts_chars_per_call: int = Field(800, alias="TTS_CHARS_PER_CALL")
    call_target_minutes: float = Field(4, alias="CALL_TARGET_MINUTES")
    turn_end_grace_ms: int = Field(150, alias="TURN_END_GRACE_MS")
    # Speech-to-text is billed per second of audio sent: skip long silences (keeps pre-roll and a silent tail for VAD)
    stt_silence_gate: bool = Field(True, alias="STT_SILENCE_GATE")

    # ---------------- Plivo ----------------
    plivo_auth_id: str = Field("", alias="PLIVO_AUTH_ID")
    plivo_auth_token: str = Field("", alias="PLIVO_AUTH_TOKEN")
    plivo_phone_number: str = Field("", alias="PLIVO_PHONE_NUMBER")
    plivo_validate_signature: bool = Field(True, alias="PLIVO_VALIDATE_SIGNATURE")

    # ---------------- Email ----------------
    # Email: Resend (preferred) or SMTP
    resend_api_key: str = Field("", alias="RESEND_API_KEY")
    email_from: str = Field("", alias="EMAIL_FROM")
    email_reply_to: str = Field("", alias="EMAIL_REPLY_TO")
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

    def __getattribute__(self, name: str):
        # We define the keys that are allowed to be overridden by the database
        secret_keys = {
            "resend_api_key", "email_from", "email_reply_to", 
            "smtp_host", "smtp_port", "smtp_username", "smtp_password", "smtp_from",
            "openrouter_api_key", "sarvam_api_key",
            "plivo_auth_id", "plivo_auth_token", "plivo_phone_number"
        }
        
        # We need to bypass our own override for inner Pydantic operations and `secret_key` itself
        if name in secret_keys:
            try:
                from app.core.secrets import get_secret_from_db
                db_val = get_secret_from_db(name)
                if db_val:
                    # If the property is an int (like smtp_port), cast it
                    if name == "smtp_port":
                        return int(db_val)
                    return db_val
            except Exception:
                pass
                
        return super().__getattribute__(name)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
