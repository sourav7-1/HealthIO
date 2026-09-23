from enum import StrEnum
from functools import lru_cache

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


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
        "postgresql+asyncpg://healthio:healthio@localhost:5432/healthio"
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

    @property
    def is_production(self) -> bool:
        return self.env == Environment.PRODUCTION

    @property
    def docs_enabled(self) -> bool:
        return self.env != Environment.PRODUCTION


@lru_cache
def get_settings() -> Settings:
    return Settings()
