"""Runtime configuration from environment variables (see .env.example)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from saathi.errors import SaathiError

MIN_APPROVAL_SECRET_BYTES = 32


class ConfigError(SaathiError):
    """A deployment value is missing or unsafe. The message names the setting, never its value."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SAATHI_", extra="ignore")

    env: str = "dev"
    public_base_url: str = "http://localhost:8000"
    # One URL per runtime role (grants part, section 1). Passwords arrive in the URL from the environment; the defaults
    # name the role and carry none.
    db_url_edge: str = "postgresql://saathi_edge@localhost:5432/saathi"
    db_url_core: str = "postgresql://saathi_core@localhost:5432/saathi"
    db_url_model: str = "postgresql://saathi_model@localhost:5432/saathi"
    db_url_owner: str = "postgresql://saathi_owner@localhost:5432/saathi"
    media_dir: str = "/var/lib/saathi/media"
    trace_dir: str = "/var/lib/saathi/traces"
    health_path: str = "/health"
    # Secrets: a value or a file, never a default. Each is its own secret (PL-14). A file wins over the value.
    approval_secret: SecretStr = Field(default=SecretStr(""))
    approval_secret_file: str = ""
    kek_file: str = ""                  # key-encryption key; mounted into the worker only
    sender_hmac_key_file: str = ""      # deployment key of the edge's sender hash

    # --- WhatsApp Cloud API -------------------------------------------------
    wa_phone_number_id: str = ""
    wa_access_token: SecretStr = SecretStr("")
    wa_app_secret: SecretStr = SecretStr("")      # verifies X-Hub-Signature-256
    wa_verify_token: SecretStr = SecretStr("")    # webhook verification handshake
    wa_graph_version: str = "v23.0"

    # Household values (people, approvers, caps, names) are versioned `household_config` rows, never settings (RQ-110).

    # --- Telegram -----------------------------------------------------------
    tg_caregiver_token: SecretStr = SecretStr("")
    tg_elder_token: SecretStr = SecretStr("")
    tg_elder_secret: SecretStr = SecretStr("")    # verifies the elder bot's webhook
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

    roster_path: str = ""           # private TOML roster (schema: infra/roster.example.toml); empty = no model tier
    usd_to_inr: float = 88.0

    # --- ASR ----------------------------------------------------------------
    asr_provider: str = "whispercpp"
    whispercpp_url: str = "http://whisper:8080"

    # --- Observability -------------------------------------------------------
    langfuse_host: str = "http://langfuse-web:3000"
    langfuse_public_key: str = ""
    langfuse_secret_key: SecretStr = SecretStr("")


def read_secret_file(path: str) -> bytes:
    """The bytes of a secret file with trailing newlines removed. `ConfigError` names the setting, never the content."""
    try:
        return Path(path).read_bytes().rstrip(b"\r\n")
    except OSError as exc:
        raise ConfigError("a secret file cannot be read") from exc


def approval_secret_bytes(s: Settings) -> bytes:
    """The approval signing secret from its file when one is set, else from the environment value."""
    if s.approval_secret_file:
        return read_secret_file(s.approval_secret_file)
    return s.approval_secret.get_secret_value().encode()


def require_secrets(s: Settings) -> None:
    """Raise `ConfigError` on an empty or under 32 byte approval secret (change 20, PL-14). There is no default."""
    if len(approval_secret_bytes(s)) < MIN_APPROVAL_SECRET_BYTES:
        raise ConfigError(f"the approval secret is empty or shorter than {MIN_APPROVAL_SECRET_BYTES} bytes")


@lru_cache
def get_settings() -> Settings:
    return Settings()
