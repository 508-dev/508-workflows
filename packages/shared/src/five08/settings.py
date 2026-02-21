"""Shared configuration settings across services."""

from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_sqlalchemy_postgres_url(url: str) -> str:
    """Normalize psycopg DSN for SQLAlchemy usage."""
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


class SharedSettings(BaseSettings):
    """Base settings shared by all services in the monorepo."""

    log_level: str = "INFO"

    redis_url: str = "redis://redis:6379/0"  # Docker Compose default; set REDIS_URL when running outside Compose.
    redis_queue_name: str = "jobs.default"
    redis_key_prefix: str = "jobs"
    redis_socket_connect_timeout: float | None = 5.0
    redis_socket_timeout: float | None = 5.0
    postgres_url: str = "postgresql://postgres:postgres@postgres:5432/workflows"
    job_max_attempts: int = 8
    job_retry_base_seconds: int = 5
    job_retry_max_seconds: int = 300
    job_timeout_seconds: int = 600
    job_result_ttl_seconds: int = 3600
    minio_endpoint: str = "http://minio:9000"
    minio_root_user: str = "internal"
    minio_root_password: str = "change-me"
    minio_internal_bucket: str = "internal-transfers"

    webhook_ingest_host: str = "0.0.0.0"
    webhook_ingest_port: int = 8090
    webhook_shared_secret: str | None = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def minio_access_key(self) -> str:
        """Access key alias for MinIO clients using the old naming."""
        return self.minio_root_user

    @property
    def minio_secret_key(self) -> str:
        """Secret key alias for MinIO clients using the old naming."""
        return self.minio_root_password
