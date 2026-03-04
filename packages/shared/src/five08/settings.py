"""Shared configuration settings across services."""

import os
import sys

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_sqlalchemy_postgres_url(url: str) -> str:
    """Normalize psycopg DSN for SQLAlchemy usage."""
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


class SharedSettings(BaseSettings):
    """Base settings shared by all services in the monorepo."""

    environment: str = "local"
    log_level: str = "INFO"

    sentry_dsn: str | None = None
    sentry_environment: str | None = None
    sentry_release: str | None = None
    sentry_sample_rate: float = 1.0
    sentry_traces_sample_rate: float = 0.0
    sentry_profiles_sample_rate: float = 0.0
    sentry_send_default_pii: bool = False
    sentry_debug: bool = False

    redis_url: str = "redis://redis:6379/0"  # Docker Compose default; set REDIS_URL when running outside Compose.
    redis_queue_name: str = "jobs.default"
    redis_key_prefix: str = "jobs"
    redis_socket_connect_timeout: float | None = 5.0
    redis_socket_timeout: float | None = 5.0
    postgres_url: str = "postgresql://postgres@postgres:5432/workflows"
    job_max_attempts: int = 8
    job_retry_base_seconds: int = 5
    job_retry_max_seconds: int = 300
    job_timeout_seconds: int = 600
    job_result_ttl_seconds: int = 3600
    minio_endpoint: str = "http://minio:9000"
    minio_root_user: str = "internal"
    minio_root_password: str = ""
    minio_internal_bucket: str = "internal-transfers"

    webhook_ingest_host: str = "0.0.0.0"
    webhook_ingest_port: int = 8090
    api_shared_secret: str | None = None
    discord_logs_webhook_url: str | None = None
    discord_logs_webhook_wait: bool = True

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @classmethod
    def _skip_dotenv(cls) -> bool:
        if os.getenv("ENVIRONMENT", "").strip().lower() == "test":
            return True
        return "pytest" in sys.modules

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        if cls._skip_dotenv():
            return (init_settings, env_settings, file_secret_settings)
        return (init_settings, env_settings, dotenv_settings, file_secret_settings)

    @model_validator(mode="after")
    def validate_required_secrets(self) -> "SharedSettings":
        """Require non-empty runtime secrets in non-local runtime environments."""
        env = self.environment.strip().lower()
        if env in {"local", "dev", "development", "test"}:
            return self

        if not self.postgres_url.strip():
            raise ValueError("POSTGRES_URL must be set when ENVIRONMENT is non-local.")
        if not self.minio_root_password.strip():
            raise ValueError(
                "MINIO_ROOT_PASSWORD must be set when ENVIRONMENT is non-local."
            )
        return self

    @model_validator(mode="after")
    def validate_sentry_rates(self) -> "SharedSettings":
        """Validate optional Sentry sampling rates."""
        if not 0.0 <= self.sentry_sample_rate <= 1.0:
            raise ValueError("SENTRY_SAMPLE_RATE must be between 0.0 and 1.0")
        if not 0.0 <= self.sentry_traces_sample_rate <= 1.0:
            raise ValueError("SENTRY_TRACES_SAMPLE_RATE must be between 0.0 and 1.0")
        if not 0.0 <= self.sentry_profiles_sample_rate <= 1.0:
            raise ValueError("SENTRY_PROFILES_SAMPLE_RATE must be between 0.0 and 1.0")
        return self

    @property
    def sentry_environment_name(self) -> str:
        """Sentry environment falls back to ENVIRONMENT."""
        configured = (self.sentry_environment or "").strip()
        if configured:
            return configured
        return self.environment

    @property
    def minio_access_key(self) -> str:
        """Access key alias for MinIO clients using the old naming."""
        return self.minio_root_user

    @property
    def minio_secret_key(self) -> str:
        """Secret key alias for MinIO clients using the old naming."""
        return self.minio_root_password
