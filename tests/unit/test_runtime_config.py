"""Unit tests for dashboard-managed runtime configuration."""

from pathlib import Path

import pytest

from five08.runtime_config import (
    RuntimeConfigDBSnapshot,
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


def _db_snapshot(values: dict[str, str]) -> RuntimeConfigDBSnapshot:
    return RuntimeConfigDBSnapshot(
        values=values,
        present_keys=frozenset(values.keys()),
    )


def test_secret_mask_shows_confirmable_edges() -> None:
    assert mask_runtime_secret("sk-abcdefghijklmnopqrstuvwxyz") == "abc...xyz"
    assert mask_runtime_secret("sk_or_v1_abcdefghijklmnopqrstuvwxyz") == "abc...xyz"
    assert mask_runtime_secret("sk-test-secret-value") == "sec...lue"
    assert mask_runtime_secret("pk_live_abcdefghijklmnopqrstuvwxyz") == "abc...xyz"
    assert mask_runtime_secret("espo-secret-key") == "esp...key"
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
    monkeypatch.setattr("five08.runtime_config._parse_dotenv_keys", lambda: {})
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    settings = WorkerSettings(espo_base_url="", espo_api_key="")
    invalidate_runtime_config_cache(settings)

    values = _load_db_values(settings)

    assert "OPENAI_API_KEY" not in values
    assert values["OPENAI_MODEL"] == "gpt-test"
    items = list_runtime_config(settings)
    item = next(entry for entry in items if entry["key"] == "OPENAI_API_KEY")
    assert item["source"] == "database"
    assert item["masked_value"] is None


def test_load_db_values_returns_cached_values_on_db_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from five08 import runtime_config

    class FakeCursor:
        def __enter__(self) -> "FakeCursor":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def execute(self, query: str) -> None:
            assert "runtime_config_values" in query

        def fetchall(self) -> list[dict[str, str]]:
            return [{"key": "OPENAI_MODEL", "value": "gpt-cached"}]

    class FakeConnection:
        def __enter__(self) -> "FakeConnection":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def cursor(self, **_: object) -> FakeCursor:
            return FakeCursor()

    settings = WorkerSettings(espo_base_url="", espo_api_key="")
    invalidate_runtime_config_cache(settings)
    monkeypatch.setattr(
        "five08.runtime_config.psycopg.connect",
        lambda _: FakeConnection(),
    )

    assert _load_db_values(settings)["OPENAI_MODEL"] == "gpt-cached"

    with runtime_config._CACHE_LOCK:
        cache_key = runtime_config._cache_key(settings)
        cached_snapshot = runtime_config._CACHE[cache_key][1]
        runtime_config._CACHE[cache_key] = (0.0, cached_snapshot)

    def fail_connect(_: str) -> object:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr("five08.runtime_config.psycopg.connect", fail_connect)

    assert _load_db_values(settings)["OPENAI_MODEL"] == "gpt-cached"


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


def test_runtime_config_list_does_not_mask_env_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definition = runtime_config_definition_for_key("OPENAI_API_KEY")
    assert definition is not None
    monkeypatch.setattr(
        "five08.runtime_config._load_db_snapshot",
        lambda settings: _db_snapshot({}),
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret-value")

    settings = WorkerSettings(espo_base_url="", espo_api_key="")
    items = list_runtime_config(settings)
    item = next(entry for entry in items if entry["key"] == "OPENAI_API_KEY")

    assert item["source"] == "env"
    assert item["env_locked"] is True
    assert item["masked_value"] is None
    assert "value" not in item


def test_runtime_config_list_masks_database_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definition = runtime_config_definition_for_key("OPENAI_API_KEY")
    assert definition is not None
    monkeypatch.setattr(
        "five08.runtime_config._load_db_snapshot",
        lambda settings: _db_snapshot({definition.key: "sk-test-secret-value"}),
    )
    monkeypatch.setattr("five08.runtime_config._parse_dotenv_keys", lambda: {})
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("RUNTIME_CONFIG_TEST_ENABLE", "true")

    settings = WorkerSettings(espo_base_url="", espo_api_key="")
    items = list_runtime_config(settings)
    item = next(entry for entry in items if entry["key"] == "OPENAI_API_KEY")

    assert item["source"] == "database"
    assert item["env_locked"] is False
    assert item["masked_value"] == "sec...lue"
    assert "value" not in item


def test_runtime_config_list_allows_clearing_invalid_database_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definition = runtime_config_definition_for_key("OPENAI_API_KEY")
    assert definition is not None
    monkeypatch.setattr(
        "five08.runtime_config._load_db_snapshot",
        lambda settings: RuntimeConfigDBSnapshot(
            values={},
            present_keys=frozenset({definition.key}),
        ),
    )
    monkeypatch.setattr("five08.runtime_config._parse_dotenv_keys", lambda: {})
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    settings = WorkerSettings(espo_base_url="", espo_api_key="")
    items = list_runtime_config(settings)
    item = next(entry for entry in items if entry["key"] == definition.key)

    assert item["source"] == "database"
    assert item["configured"] is True
    assert item["masked_value"] is None
    assert "value" not in item


def test_runtime_config_value_type_validation() -> None:
    definition = runtime_config_definition_for_key("MAX_FILE_SIZE_MB")
    assert definition is not None

    assert coerce_runtime_config_value(definition, "30") == "30"
    with pytest.raises(ValueError, match="integer"):
        coerce_runtime_config_value(definition, "soon")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("OPENAI_MODEL", ""),
        ("DOCUSEAL_BASE_URL", " "),
        ("GITHUB_ALLOWED_REPOS", ",, "),
    ],
)
def test_runtime_config_rejects_blank_non_secret_overrides(
    key: str,
    value: str,
) -> None:
    definition = runtime_config_definition_for_key(key)
    assert definition is not None

    with pytest.raises(ValueError, match="must not be blank"):
        coerce_runtime_config_value(definition, value)


@pytest.mark.parametrize(
    "key",
    [
        "DOCUSEAL_MEMBER_AGREEMENT_TEMPLATE_ID",
        "GIG_RECRUITING_STALE_DAYS",
        "GIG_RECRUITING_REMINDER_MAX_AGE_DAYS",
    ],
)
@pytest.mark.parametrize("value", ["0", "-1"])
def test_runtime_config_numeric_bounds_are_preserved(
    key: str,
    value: str,
) -> None:
    definition = runtime_config_definition_for_key(key)
    assert definition is not None

    with pytest.raises(ValueError, match="greater than or equal to 1"):
        coerce_runtime_config_value(definition, value)


@pytest.mark.parametrize(
    "key",
    [
        "DISCORD_LOGS_WEBHOOK_URL",
        "NEWSLETTER_SYNC_ENABLED",
        "NEWSLETTER_SYNC_INTERVAL_SECONDS",
    ],
)
def test_startup_bound_runtime_config_is_restart_required(key: str) -> None:
    definition = runtime_config_definition_for_key(key)
    assert definition is not None
    assert definition.restart_required is True


@pytest.mark.parametrize(
    "key",
    [
        "BREVO_API_KEY",
        "BREVO_API_BASE_URL",
        "BREVO_API_TIMEOUT_SECONDS",
        "BREVO_508_MEMBERS_NEWSLETTER_LIST_ID",
        "BREVO_508_MEMBERS_NEWSLETTER_LIST_NAME",
        "MIGADU_API_USER",
        "MIGADU_API_KEY",
        "MIGADU_MAILBOX_DOMAIN",
        "KEILA_API_KEY",
        "KEILA_API_BASE_URL",
        "KEILA_API_TIMEOUT_SECONDS",
        "NEWSLETTER_SYNC_ENABLED",
        "NEWSLETTER_SYNC_INTERVAL_SECONDS",
        "NEWSLETTER_SYNC_EXCLUDED_MAILBOXES",
    ],
)
def test_newsletter_settings_are_dashboard_configurable(key: str) -> None:
    definition = runtime_config_definition_for_key(key)

    assert definition is not None
    assert definition.category in {"Newsletter", "Mailbox"}


def test_core_crm_auth_and_mailbox_settings_are_not_dashboard_configurable() -> None:
    assert runtime_config_definition_for_key("ESPO_BASE_URL") is None
    assert runtime_config_definition_for_key("ESPO_API_KEY") is None
    assert runtime_config_definition_for_key("AUTHENTIK_API_BASE_URL") is None
    assert runtime_config_definition_for_key("AUTHENTIK_API_TOKEN") is None
    assert runtime_config_definition_for_key("CRM_SYNC_INTERVAL_SECONDS") is None
    assert runtime_config_definition_for_key("CRM_SYNC_PAGE_SIZE") is None


def test_migadu_runtime_config_accepts_short_env_aliases() -> None:
    migadu_user = runtime_config_definition_for_key("MIGADU_API_USER")
    migadu_domain = runtime_config_definition_for_key("MIGADU_MAILBOX_DOMAIN")
    keila_base_url = runtime_config_definition_for_key("KEILA_API_BASE_URL")

    assert migadu_user is not None
    assert "MIGADU_USER" in migadu_user.env_names
    assert migadu_domain is not None
    assert "MIGADU_DOMAIN" in migadu_domain.env_names
    assert keila_base_url is not None
    assert "KEILA_BASE_URL" in keila_base_url.env_names


def test_onboarding_email_smtp_settings_are_dashboard_configurable() -> None:
    assert runtime_config_definition_for_key("ONBOARDING_EMAIL_SMTP_SERVER") is not None
    assert (
        runtime_config_definition_for_key("ONBOARDING_EMAIL_SMTP_PASSWORD") is not None
    )
    assert (
        runtime_config_definition_for_key("ONBOARDING_EMAIL_SENDER_EMAIL") is not None
    )
    password = runtime_config_definition_for_key("ONBOARDING_EMAIL_SMTP_PASSWORD")
    assert password is not None
    assert password.is_secret is True
    assert password.category == "Onboarding"


def test_tally_settings_are_dashboard_configurable() -> None:
    api_key = runtime_config_definition_for_key("ONBOARDING_TALLY_API_KEY")
    signing_secret = runtime_config_definition_for_key(
        "ONBOARDING_TALLY_WEBHOOK_SIGNING_SECRET"
    )
    allowed_forms = runtime_config_definition_for_key(
        "ONBOARDING_TALLY_ALLOWED_FORM_IDS"
    )

    assert api_key is not None
    assert api_key.is_secret is True
    assert api_key.category == "Intake"
    assert signing_secret is not None
    assert signing_secret.is_secret is True
    assert signing_secret.category == "Intake"
    assert allowed_forms is not None
    assert allowed_forms.value_type == "csv"
    assert allowed_forms.category == "Intake"


def test_runtime_config_list_marks_numeric_values_as_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definition = runtime_config_definition_for_key("MAX_FILE_SIZE_MB")
    assert definition is not None
    monkeypatch.setenv("RUNTIME_CONFIG_TEST_ENABLE", "true")
    monkeypatch.setattr(
        "five08.runtime_config._load_db_snapshot",
        lambda settings: _db_snapshot({definition.key: "1"}),
    )
    monkeypatch.setattr("five08.runtime_config._parse_dotenv_keys", lambda: {})

    settings = WorkerSettings(espo_base_url="", espo_api_key="")
    items = list_runtime_config(settings)
    item = next(entry for entry in items if entry["key"] == definition.key)

    assert item["source"] == "database"
    assert item["configured"] is True
    assert item["value"] == 1


def test_parse_dotenv_keys_reads_env_file_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from five08 import runtime_config

    runtime_config._parse_dotenv_keys_cached.cache_clear()
    env_path = tmp_path / ".env"
    env_path.write_text("OPENAI_API_KEY=from-dotenv\n", encoding="utf-8")
    read_count = 0
    original_read_text = Path.read_text

    def counted_read_text(self: Path, *args: object, **kwargs: object) -> str:
        nonlocal read_count
        if self == env_path:
            read_count += 1
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted_read_text)

    path_str = str(env_path)
    assert (
        runtime_config._parse_dotenv_keys_cached(path_str)["OPENAI_API_KEY"]
        == "from-dotenv"
    )
    assert (
        runtime_config._parse_dotenv_keys_cached(path_str)["OPENAI_API_KEY"]
        == "from-dotenv"
    )
    assert read_count == 1
