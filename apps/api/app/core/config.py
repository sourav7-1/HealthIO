import base64
from enum import StrEnum
from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


# Development-only keys. The validator below refuses them outside local/test.
_DEV_ENCRYPTION_KEY = base64.b64encode(b"dev-only-key-do-not-use-in-prod!").decode()
_DEV_BLIND_INDEX_KEY = base64.b64encode(b"dev-only-blind-index-key-32bytes").decode()
_DEV_JWT_SEED = base64.b64encode(b"dev-only-jwt-ed25519-seed-32byte").decode()


class Settings(BaseSettings):
    """Application settings, read from environment variables (prefix HIO_) and .env."""

    model_config = SettingsConfigDict(
        env_prefix="HIO_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Environment = Environment.LOCAL
    app_name: str = "Health Io API"
    api_prefix: str = "/api/v1"
    log_level: str = "INFO"
    log_json: bool = False

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    database_url: PostgresDsn = PostgresDsn(
        "postgresql+asyncpg://healthio:healthio@localhost:5433/healthio"
    )
    database_pool_size: int = 10
    database_echo: bool = False

    redis_url: RedisDsn = RedisDsn("redis://localhost:6379/0")
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    s3_endpoint_url: str | None = "http://localhost:9000"
    s3_region: str = "ap-south-1"
    s3_bucket: str = "healthio-local"
    s3_access_key_id: SecretStr = SecretStr("healthio")
    s3_secret_access_key: SecretStr = SecretStr("healthio-secret")
    s3_presign_ttl_seconds: int = 300
    # Endpoint browsers use for presigned URLs (differs from s3_endpoint_url inside Docker).
    s3_public_endpoint_url: str | None = None
    upload_max_bytes: int = 15 * 1024 * 1024
    # Until the ClamAV sidecar lands (roadmap Phase 1b/20), uploads are accepted after
    # type/size/magic-byte checks. Set true to require an antivirus verdict instead.
    upload_virus_scan_required: bool = False

    rate_limit_default_per_minute: int = 120

    # Field-level encryption keyring: {key_id: base64(32-byte key)}; see app/core/crypto.py.
    encryption_keys: dict[str, SecretStr] = Field(
        default_factory=lambda: {"dev1": SecretStr(_DEV_ENCRYPTION_KEY)}
    )
    encryption_active_key_id: str = "dev1"
    blind_index_key: SecretStr = SecretStr(_DEV_BLIND_INDEX_KEY)

    # --- AI document reading (app/ai, app/modules/extraction; AI_SAFETY.md §4) ---
    # "disabled": no model is called; uploads open the review screen for manual entry.
    # "anthropic": Claude reads the image (needs anthropic_api_key).
    ai_provider: Literal["disabled", "anthropic"] = "disabled"
    anthropic_api_key: SecretStr | None = None
    ai_vision_model: str = "claude-sonnet-5"
    ai_request_timeout_seconds: float = 60.0
    # OCR engine used alongside the vision model: "none", or "tesseract" (binary required).
    ai_ocr_engine: Literal["none", "tesseract"] = "none"
    # Run extraction inside the request (local dev without a worker) or on Celery.
    ai_jobs_inline: bool = True

    # --- reminders and Web Push (app/modules/reminders/engine.py) ---
    # VAPID keys for Web Push (generate: uv run python -m scripts.generate_vapid_keys).
    # Without them, reminders are in-app only.
    webpush_vapid_public_key: str | None = None
    webpush_vapid_private_key: SecretStr | None = None
    webpush_subject: str = "mailto:reminders@healthio.local"
    # Doses are created this far ahead so reminders can go out on time.
    reminder_horizon_hours: int = 48

    # --- authentication (app/modules/identity) ---
    # Ed25519 signing keys for access tokens: {key_id: base64(32-byte seed)}. Old key IDs
    # stay here during rotation so tokens they signed remain verifiable until expiry.
    jwt_signing_keys: dict[str, SecretStr] = Field(
        default_factory=lambda: {"devjwt1": SecretStr(_DEV_JWT_SEED)}
    )
    jwt_active_key_id: str = "devjwt1"
    jwt_issuer: str = "healthio-api"
    jwt_audience: str = "healthio"
    access_token_ttl_seconds: int = 600
    refresh_token_ttl_seconds: int = 14 * 24 * 3600  # sliding: each refresh extends it
    session_absolute_ttl_seconds: int = 30 * 24 * 3600  # hard cap, then log in again
    refresh_cookie_name: str = "hio_refresh"

    password_min_length: int = 10
    password_max_length: int = 128
    # Argon2id cost (OWASP: m >= 19 MiB, t >= 2). Tests lower these for speed.
    argon2_time_cost: int = 3
    argon2_memory_kib: int = 65536
    argon2_parallelism: int = 1

    login_max_failures: int = 5  # consecutive failures before a temporary lock
    login_lockout_seconds: int = 900
    login_rate_limit_per_minute: int = 10  # per client IP
    auth_email_rate_limit_per_hour: int = 5  # verification/reset emails per address

    email_verification_ttl_seconds: int = 24 * 3600
    password_reset_ttl_seconds: int = 30 * 60

    # Outgoing email (Mailpit in development). "memory" keeps messages in-process (tests).
    mail_backend: str = "smtp"
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    mail_from: str = "Health Io <no-reply@healthio.local>"
    public_web_url: str = "http://localhost:5173"

    @model_validator(mode="after")
    def _no_dev_keys_outside_dev(self) -> Self:
        if self.env in (Environment.LOCAL, Environment.TEST):
            return self
        dev_values = {_DEV_ENCRYPTION_KEY, _DEV_BLIND_INDEX_KEY, _DEV_JWT_SEED}
        used = {v.get_secret_value() for v in self.encryption_keys.values()}
        used |= {v.get_secret_value() for v in self.jwt_signing_keys.values()}
        used.add(self.blind_index_key.get_secret_value())
        if used & dev_values:
            raise ValueError("development keys must not be used in " + self.env)
        return self

    @property
    def cookie_secure(self) -> bool:
        return self.env not in (Environment.LOCAL, Environment.TEST)

    @property
    def is_production(self) -> bool:
        return self.env == Environment.PRODUCTION

    @property
    def docs_enabled(self) -> bool:
        return self.env != Environment.PRODUCTION


@lru_cache
def get_settings() -> Settings:
    return Settings()
