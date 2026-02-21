"""Unit tests for shared settings validation."""

import pytest
from pydantic import ValidationError

from five08.settings import SharedSettings


def test_non_local_settings_accept_explicit_values() -> None:
    """Non-local settings should validate when values are provided directly."""
    settings = SharedSettings(
        runtime_env="production",
        postgres_url="postgresql://user:pass@db.example.com:5432/workflows",
        minio_root_password="secret",
    )

    assert settings.runtime_env == "production"


def test_non_local_settings_require_non_empty_secrets() -> None:
    """Non-local settings should reject empty runtime secret values."""
    with pytest.raises(ValidationError, match="MINIO_ROOT_PASSWORD must be set"):
        SharedSettings(
            runtime_env="production",
            postgres_url="postgresql://user:pass@db.example.com:5432/workflows",
            minio_root_password=" ",
        )
