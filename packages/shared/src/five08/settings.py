"""Shared configuration settings across services."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class SharedSettings(BaseSettings):
    """Base settings shared by all services in the monorepo."""

    log_level: str = "INFO"

    redis_url: str = "redis://redis:6379/0"
    redis_queue_name: str = "jobs.default"
    redis_key_prefix: str = "jobs"
    job_timeout_seconds: int = 600
    job_result_ttl_seconds: int = 3600

    webhook_ingest_host: str = "0.0.0.0"
    webhook_ingest_port: int = 8090
    webhook_shared_secret: str | None = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
