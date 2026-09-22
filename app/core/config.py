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
    heal_export_token: str = Field("", alias="HEAL_EXPORT_TOKEN")   # read-only token for the cloud fix agent; empty = generated in the DB
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
    # The model bills by token, not by width, so a narrower vector costs the same to make and a
    # third of the storage and cosine work. 512 keeps the ranking this knowledge base needs.
    openrouter_embedding_dimensions: int = Field(512, alias="OPENROUTER_EMBEDDING_DIMENSIONS")
    # Embeddings: providers are tried in order until one answers. OpenRouter leads because it bills
    # by token — a whole knowledge base costs about a tenth of a cent — while Gemini's free tier
    # counts every passage as one of 1,000 daily requests, so a single upload nearly exhausts a day.
    # Gemini stays as the free fallback for when OpenRouter runs out of credit.
    embedding_providers: str = Field("openrouter,gemini", alias="EMBEDDING_PROVIDERS")
    gemini_api_key: str = Field("", alias="GEMINI_API_KEY")
    gemini_embedding_model: str = Field("gemini-embedding-001", alias="GEMINI_EMBEDDING_MODEL")
    # Gemini returns 3072 numbers by default; 768 is the documented smaller size, a quarter of the
    # storage and the same ranking in practice. Vectors are normalised before use either way.
    gemini_embedding_dimensions: int = Field(768, alias="GEMINI_EMBEDDING_DIMENSIONS")
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
    tts_chars_per_call: int = Field(650, alias="TTS_CHARS_PER_CALL")   # ~2.5 min of speech; the prompt steers at 75% and wraps at 100%
    call_target_minutes: float = Field(4, alias="CALL_TARGET_MINUTES")
    turn_end_grace_ms: int = Field(150, alias="TURN_END_GRACE_MS")
    # Speech-to-text is billed per second of audio sent: skip long silences (keeps pre-roll and a silent tail for VAD)
    stt_silence_gate: bool = Field(True, alias="STT_SILENCE_GATE")
    # "call": recognise in the call's language (lead's, else the persona default); "auto": let Sarvam detect per segment.
    stt_language_mode: str = Field("call", alias="STT_LANGUAGE_MODE")

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
        if name in RUNTIME_KEYS:
            # Tuning an admin changes from Settings without a redeploy: plaintext, validated on save.
            try:
                from app.core.runtime import runtime_override
                value = runtime_override(name)
                if value is not None:
                    return value
            except Exception:
                pass
        # We define the keys that are allowed to be overridden by the database
        secret_keys = {
            "resend_api_key", "email_from", "email_reply_to", 
            "smtp_host", "smtp_port", "smtp_username", "smtp_password", "smtp_from",
            "openrouter_api_key", "sarvam_api_key", "gemini_api_key",
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


# Settings the admin may change from the web app (Settings -> Runtime tuning). name -> (type, label, help, (min, max) or None)
RUNTIME_KEYS = {
    "tts_chars_per_call": (int, "TTS characters per call", "Spoken-character budget the agent is steered to close at (TTS is billed per character).", (200, 5000)),
    "call_target_minutes": (float, "Target call minutes", "Soft target the agent is steered to wrap up at; max_call_minutes per agent is the hard cap.", (1, 30)),
    "llm_providers": (str, "Live LLM order", "Comma-separated: sarvam, openrouter. First that answers wins.", None),
    "summary_llm_providers": (str, "Summary LLM order", "Provider order for post-call summaries and other offline work.", None),
    "sarvam_llm_model": (str, "Sarvam LLM model", "e.g. sarvam-105b", None),
    "openrouter_models": (str, "OpenRouter models", "Comma-separated, tried in order.", None),
    "openrouter_fallback_models": (str, "OpenRouter fallback models", "Last-resort tier after every primary model fails.", None),
    "llm_prompt_char_budget": (int, "Prompt character budget", "Max characters of the live prompt before knowledge/history are trimmed.", (8000, 64000)),
    "playground_monthly_limit": (int, "Playground rehearsals per member per month", "0 = unlimited.", (0, 10000)),
    "heal_export_token": (str, "Heal export token", "Token the cloud fix agent presents; rotate here or in Health & heal.", None),
    "stt_language_mode": (str, "Speech recognition language", "'call' = the call's language (steady Hindi/Hinglish in Devanagari); 'auto' = detect per phrase (drifts into other Indic scripts).", None),
}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
