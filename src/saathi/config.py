"""Runtime configuration from environment variables (see .env.example)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SAATHI_", extra="ignore")

    env: str = "dev"
    public_base_url: str = "http://localhost:8000"
    database_url: str = "postgresql://saathi:saathi@localhost:5432/saathi"
    media_dir: str = "/var/lib/saathi/media"
    approval_secret: SecretStr = Field(default=SecretStr(""))

    # --- WhatsApp Cloud API -------------------------------------------------
    wa_phone_number_id: str = ""
    wa_access_token: SecretStr = SecretStr("")
    wa_app_secret: SecretStr = SecretStr("")      # verifies X-Hub-Signature-256
    wa_verify_token: SecretStr = SecretStr("")    # webhook verification handshake
    wa_graph_version: str = "v23.0"

    # --- People (kept in .env only; never committed) ---------------------
    caregiver_name: str = "your family member"   # used in replies to the elder
    persona_name: str = "Saathi"
    elder_phones: list[str] = []                  # E.164 numbers allowed to use the helper

    # --- Telegram -----------------------------------------------------------
    tg_bot_token: SecretStr = SecretStr("")
    tg_primary_approver_id: int = 0
    tg_backup_approver_ids: list[int] = []
    tg_ops_chat_id: int = 0

    # --- Model tiers (all chat-completions-compatible HTTP endpoints) ----
    t1_base_url: str = "http://ollama:11434/v1"   # local, in-compose
    t1_model: str = ""             # required to enable T1; empty = tier disabled
    t2_base_url: str = ""
    t2_api_key: SecretStr = SecretStr("")
    t2_model: str = ""          # empty = tier disabled
    t2_vision_model: str = ""
    t2_free_base_url: str = ""   # NON-PERSONAL data only
    t2_free_api_key: SecretStr = SecretStr("")
    t2_free_model: str = ""
    t3_base_url: str = ""
    t3_api_key: SecretStr = SecretStr("")
    t3_model: str = ""
    t4_base_url: str = ""
    t4_api_key: SecretStr = SecretStr("")
    t4_model: str = ""

    paid_monthly_cap_inr: float = 0.0   # 0 = paid tiers disabled until set privately
    usd_to_inr: float = 88.0

    # --- ASR ----------------------------------------------------------------
    asr_provider: str = "whispercpp"
    whispercpp_url: str = "http://whisper:8080"

    # --- Observability -------------------------------------------------------
    langfuse_host: str = "http://langfuse-web:3000"
    langfuse_public_key: str = ""
    langfuse_secret_key: SecretStr = SecretStr("")


@lru_cache
def get_settings() -> Settings:
    return Settings()
