import base64
from enum import StrEnum
from functools import lru_cache
from typing import Self

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

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

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

    rate_limit_default_per_minute: int = 120

    # Field-level encryption keyring: {key_id: base64(32-byte key)}; see app/core/crypto.py.
    encryption_keys: dict[str, SecretStr] = Field(
        default_factory=lambda: {"dev1": SecretStr(_DEV_ENCRYPTION_KEY)}
    )
    encryption_active_key_id: str = "dev1"
    blind_index_key: SecretStr = SecretStr(_DEV_BLIND_INDEX_KEY)

    @model_validator(mode="after")
    def _no_dev_keys_outside_dev(self) -> Self:
        if self.env in (Environment.LOCAL, Environment.TEST):
            return self
        dev_values = {_DEV_ENCRYPTION_KEY, _DEV_BLIND_INDEX_KEY}
        used = {v.get_secret_value() for v in self.encryption_keys.values()}
        used.add(self.blind_index_key.get_secret_value())
        if used & dev_values:
            raise ValueError("development encryption keys must not be used in " + self.env)
        return self

    @property
    def is_production(self) -> bool:
        return self.env == Environment.PRODUCTION

    @property
    def docs_enabled(self) -> bool:
        return self.env != Environment.PRODUCTION


@lru_cache
def get_settings() -> Settings:
    return Settings()
