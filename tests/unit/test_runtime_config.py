"""Unit tests for dashboard-managed runtime configuration."""

import pytest

from five08.runtime_config import (
    _decrypt_secret_value,
    _encrypt_secret_value,
    _load_db_values,
    coerce_runtime_config_value,
    definition_is_env_locked,
    invalidate_runtime_config_cache,
    list_runtime_config,
    mask_runtime_secret,
    runtime_config_definition_for_key,
    set_runtime_config_value,
)
from five08.worker.config import WorkerSettings


def test_secret_mask_shows_confirmable_edges() -> None:
    assert mask_runtime_secret("sk-abcdefghijklmnopqrstuvwxyz") == "sk-ab...vwxyz"
    assert mask_runtime_secret("abcdef") == "ab...ef"
    assert mask_runtime_secret("abc") == "****"
    assert mask_runtime_secret("") is None


def test_runtime_secret_values_encrypt_and_decrypt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIG_SECRET_KEY", "unit-test-runtime-secret")

    encrypted = _encrypt_secret_value("sk-test-secret")

    assert encrypted.startswith("fernet:v1:")
    assert "sk-test-secret" not in encrypted
    assert _decrypt_secret_value(encrypted) == "sk-test-secret"


def test_load_db_values_skips_invalid_secret_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeCursor:
        def __enter__(self) -> "FakeCursor":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def execute(self, query: str) -> None:
            assert "runtime_config_values" in query

        def fetchall(self) -> list[dict[str, str]]:
            return [
                {"key": "OPENAI_API_KEY", "value": "plaintext-secret"},
                {"key": "OPENAI_MODEL", "value": "gpt-test"},
            ]

    class FakeConnection:
        def __enter__(self) -> "FakeConnection":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def cursor(self, **_: object) -> FakeCursor:
            return FakeCursor()

    monkeypatch.setattr(
        "five08.runtime_config.psycopg.connect",
        lambda _: FakeConnection(),
    )

    settings = WorkerSettings(espo_base_url="", espo_api_key="")
    invalidate_runtime_config_cache(settings)

    values = _load_db_values(settings)

    assert "OPENAI_API_KEY" not in values
    assert values["OPENAI_MODEL"] == "gpt-test"


def test_saving_secret_requires_encryption_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CONFIG_SECRET_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("five08.runtime_config._parse_dotenv_keys", lambda: {})
    definition = runtime_config_definition_for_key("OPENAI_API_KEY")
    assert definition is not None

    with pytest.raises(RuntimeError, match="CONFIG_SECRET_KEY"):
        set_runtime_config_value(
            object(),
            definition,
            "sk-test-secret",
        )


def test_env_value_locks_matching_runtime_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definition = runtime_config_definition_for_key("OPENAI_DIRECT_API_KEY")
    assert definition is not None
    monkeypatch.setenv("OPENAI_API_KEY_DIRECT", "legacy-direct-key")

    assert definition_is_env_locked(definition)


def test_runtime_config_list_masks_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definition = runtime_config_definition_for_key("OPENAI_API_KEY")
    assert definition is not None
    monkeypatch.setattr("five08.runtime_config._load_db_values", lambda settings: {})
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret-value")

    settings = WorkerSettings(espo_base_url="", espo_api_key="")
    items = list_runtime_config(settings)
    item = next(entry for entry in items if entry["key"] == "OPENAI_API_KEY")

    assert item["source"] == "env"
    assert item["env_locked"] is True
    assert item["masked_value"] == "sk-te...value"
    assert "value" not in item


def test_runtime_config_value_type_validation() -> None:
    definition = runtime_config_definition_for_key("CRM_SYNC_INTERVAL_SECONDS")
    assert definition is not None

    assert coerce_runtime_config_value(definition, "30") == "30"
    with pytest.raises(ValueError, match="integer"):
        coerce_runtime_config_value(definition, "soon")


def test_startup_bound_espo_settings_are_restart_required() -> None:
    base_url_definition = runtime_config_definition_for_key("ESPO_BASE_URL")
    api_key_definition = runtime_config_definition_for_key("ESPO_API_KEY")

    assert base_url_definition is not None
    assert api_key_definition is not None
    assert base_url_definition.restart_required is True
    assert api_key_definition.restart_required is True
