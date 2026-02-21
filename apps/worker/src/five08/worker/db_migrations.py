"""PostgreSQL migration entry points for worker jobs schema."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from five08.logging import configure_logging
from five08.worker.config import settings

_ALEMBIC_CFG_PATH = Path(__file__).resolve().parent / "alembic.ini"


def _sqlalchemy_postgres_url() -> str:
    """Return a SQLAlchemy-compatible URL from the configured Postgres URL."""
    if settings.postgres_url.startswith("postgresql://"):
        return settings.postgres_url.replace(
            "postgresql://", "postgresql+psycopg://", 1
        )
    return settings.postgres_url


def run_job_migrations() -> None:
    """Run Alembic migrations to ensure the jobs table exists and is current."""
    configure_logging(settings.log_level)
    cfg = Config(str(_ALEMBIC_CFG_PATH))
    cfg.set_main_option("sqlalchemy.url", _sqlalchemy_postgres_url())
    command.upgrade(cfg, "head")
