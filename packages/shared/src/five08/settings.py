"""Shared configuration settings across services."""

import os
import sys

from pydantic import AliasChoices, Field, field_validator, model_validator
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
    sentry_send_default_pii: bool = False
    sentry_debug: bool = False
    langfuse_base_url: str | None = None

    # Local development defaults to host-run app services. Containerized runtimes
    # should inject Docker-network service URLs explicitly.
    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_queue_name: str = "jobs.default"
    redis_key_prefix: str = "jobs"
    redis_socket_connect_timeout: float | None = 5.0
    redis_socket_timeout: float | None = 5.0
    postgres_url: str = "postgresql://postgres:postgres@127.0.0.1:5432/workflows"
    job_max_attempts: int = 8
    job_retry_base_seconds: int = 5
    job_retry_max_seconds: int = 300
    job_timeout_seconds: int = 600
    job_result_ttl_seconds: int = 3600
    gig_recruiting_stale_days: int = Field(default=7, ge=1)
    gig_recruiting_reminder_max_age_days: int = Field(default=90, ge=1)
    minio_endpoint: str = "http://127.0.0.1:9000"
    minio_root_user: str = "internal"
    minio_root_password: str = ""
    minio_internal_bucket: str = "internal-transfers"

    web_host: str = Field(
        default="0.0.0.0",
        validation_alias=AliasChoices("WEB_HOST", "WEBHOOK_INGEST_HOST"),
    )
    web_port: int = Field(
        default=8090,
        validation_alias=AliasChoices("WEB_PORT", "WEBHOOK_INGEST_PORT"),
    )
    api_shared_secret: str | None = None
    webhook_shared_secret: str | None = None
    discord_logs_webhook_url: str | None = None
    discord_logs_webhook_wait: bool = True
    docuseal_base_url: str | None = None
    docuseal_api_key: str | None = None
    docuseal_member_agreement_template_id: int | None = None
    github_api_token: str | None = None
    github_default_repo: str | None = None
    github_allowed_repos: str = ""
    kimai_base_url: str | None = None
    kimai_api_token: str | None = None
    erpnext_base_url: str | None = None
    erpnext_api_key: str | None = None
    erpnext_api_timeout_seconds: float = 20.0
    migadu_api_user: str | None = None
    migadu_api_key: str | None = None
    migadu_mailbox_domain: str = "508.dev"
    authentik_api_base_url: str | None = None
    authentik_api_token: str | None = None
    authentik_api_timeout_seconds: float = 20.0
    authentik_recovery_email_stage_id: str | None = None
    authentik_recovery_email_stage_name: str = "default-recovery-email"
    outline_base_url: str = "https://app.getoutline.com"
    outline_api_key: str | None = None
    outline_api_timeout_seconds: float = 20.0
    brevo_api_key: str | None = None
    brevo_api_base_url: str = "https://api.brevo.com/v3"
    brevo_api_timeout_seconds: float = 20.0
    brevo_508_members_newsletter_list_id: int | None = Field(default=None, ge=1)
    brevo_508_members_newsletter_list_name: str = "508 members"

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    @field_validator("docuseal_member_agreement_template_id", mode="before")
    @classmethod
    def _normalize_docuseal_member_agreement_template_id(
        cls,
        value: object,
    ) -> int | None:
        if value is None:
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return None
            try:
                return int(normalized)
            except ValueError as exc:
                raise ValueError(
                    "DOCUSEAL_MEMBER_AGREEMENT_TEMPLATE_ID must be an integer"
                ) from exc
        raise TypeError("DOCUSEAL_MEMBER_AGREEMENT_TEMPLATE_ID must be an integer")

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

    @property
    def sentry_environment_name(self) -> str:
        """Sentry environment always follows the app runtime environment."""
        return self.environment

    @property
    def sentry_release(self) -> str | None:
        """Sentry release is not runtime-configurable."""
        return None

    @property
    def sentry_sample_rate(self) -> float:
        """Sentry event sampling stays enabled when Sentry is configured."""
        return 1.0

    @property
    def sentry_traces_sample_rate(self) -> float:
        """Tracing is disabled until the project explicitly needs it."""
        return 0.0

    @property
    def sentry_profiles_sample_rate(self) -> float:
        """Profiling is disabled until the project explicitly needs it."""
        return 0.0

    @property
    def minio_access_key(self) -> str:
        """Access key alias for MinIO clients using the old naming."""
        return self.minio_root_user

    @property
    def minio_secret_key(self) -> str:
        """Secret key alias for MinIO clients using the old naming."""
        return self.minio_root_password
