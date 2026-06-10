"""Runtime configuration registry and database-backed overrides."""

from __future__ import annotations

import logging
import os
import sys
import time
from base64 import urlsafe_b64encode
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import psycopg
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)

RuntimeConfigValueType = Literal["string", "bool", "int", "float", "url", "csv"]


@dataclass(frozen=True)
class RuntimeConfigDefinition:
    """One admin-dashboard configurable setting."""

    key: str
    attr: str
    label: str
    category: str
    description: str
    value_type: RuntimeConfigValueType = "string"
    is_secret: bool = False
    env_names: tuple[str, ...] = ()
    restart_required: bool = False

    @property
    def primary_env_name(self) -> str:
        return self.env_names[0] if self.env_names else self.key


_DEFINITIONS: tuple[RuntimeConfigDefinition, ...] = (
    RuntimeConfigDefinition(
        key="ESPO_BASE_URL",
        attr="espo_base_url",
        label="EspoCRM base URL",
        category="CRM",
        description="Base URL used for CRM API calls and dashboard profile links.",
        value_type="url",
        env_names=("ESPO_BASE_URL",),
    ),
    RuntimeConfigDefinition(
        key="ESPO_API_KEY",
        attr="espo_api_key",
        label="EspoCRM API key",
        category="CRM",
        description="API key used by API, worker, and bot CRM clients.",
        is_secret=True,
        env_names=("ESPO_API_KEY",),
    ),
    RuntimeConfigDefinition(
        key="ERPNEXT_BASE_URL",
        attr="erpnext_base_url",
        label="ERPNext base URL",
        category="Projects",
        description="ERPNext endpoint for project, invoice, and engineer workflows.",
        value_type="url",
        env_names=("ERPNEXT_BASE_URL",),
    ),
    RuntimeConfigDefinition(
        key="ERPNEXT_API_KEY",
        attr="erpnext_api_key",
        label="ERPNext API key",
        category="Projects",
        description="ERPNext token used for project and user provisioning calls.",
        is_secret=True,
        env_names=("ERPNEXT_API_KEY",),
    ),
    RuntimeConfigDefinition(
        key="AUTHENTIK_API_BASE_URL",
        attr="authentik_api_base_url",
        label="Authentik API base URL",
        category="Onboarding",
        description="Authentik API endpoint used for SSO account provisioning.",
        value_type="url",
        env_names=("AUTHENTIK_API_BASE_URL",),
    ),
    RuntimeConfigDefinition(
        key="AUTHENTIK_API_TOKEN",
        attr="authentik_api_token",
        label="Authentik API token",
        category="Onboarding",
        description="Token used to create and update Authentik users.",
        is_secret=True,
        env_names=("AUTHENTIK_API_TOKEN",),
    ),
    RuntimeConfigDefinition(
        key="OUTLINE_BASE_URL",
        attr="outline_base_url",
        label="Outline base URL",
        category="Onboarding",
        description="Outline workspace URL used for wiki/project document calls.",
        value_type="url",
        env_names=("OUTLINE_BASE_URL",),
    ),
    RuntimeConfigDefinition(
        key="OUTLINE_API_KEY",
        attr="outline_api_key",
        label="Outline API key",
        category="Onboarding",
        description="API key used to add users and read project wiki pages.",
        is_secret=True,
        env_names=("OUTLINE_API_KEY",),
    ),
    RuntimeConfigDefinition(
        key="MIGADU_API_USER",
        attr="migadu_api_user",
        label="Migadu API user",
        category="Onboarding",
        description="Migadu API username for mailbox provisioning.",
        env_names=("MIGADU_API_USER",),
    ),
    RuntimeConfigDefinition(
        key="MIGADU_API_KEY",
        attr="migadu_api_key",
        label="Migadu API key",
        category="Onboarding",
        description="Migadu API key for mailbox provisioning.",
        is_secret=True,
        env_names=("MIGADU_API_KEY",),
    ),
    RuntimeConfigDefinition(
        key="DOCUSEAL_BASE_URL",
        attr="docuseal_base_url",
        label="DocuSeal base URL",
        category="Onboarding",
        description="DocuSeal API endpoint used for agreement workflows.",
        value_type="url",
        env_names=("DOCUSEAL_BASE_URL",),
    ),
    RuntimeConfigDefinition(
        key="DOCUSEAL_API_KEY",
        attr="docuseal_api_key",
        label="DocuSeal API key",
        category="Onboarding",
        description="DocuSeal API key for agreement workflows.",
        is_secret=True,
        env_names=("DOCUSEAL_API_KEY",),
    ),
    RuntimeConfigDefinition(
        key="DOCUSEAL_MEMBER_AGREEMENT_TEMPLATE_ID",
        attr="docuseal_member_agreement_template_id",
        label="DocuSeal member agreement template",
        category="Onboarding",
        description="Template ID used to filter/sign member agreements.",
        value_type="int",
        env_names=("DOCUSEAL_MEMBER_AGREEMENT_TEMPLATE_ID",),
    ),
    RuntimeConfigDefinition(
        key="OPENAI_API_KEY",
        attr="openai_api_key",
        label="OpenAI API key",
        category="AI",
        description="Primary OpenAI-compatible API key.",
        is_secret=True,
        env_names=("OPENAI_API_KEY",),
    ),
    RuntimeConfigDefinition(
        key="OPENAI_BASE_URL",
        attr="openai_base_url",
        label="OpenAI base URL",
        category="AI",
        description="Primary OpenAI-compatible base URL, including Bifrost when used.",
        value_type="url",
        env_names=("OPENAI_BASE_URL",),
    ),
    RuntimeConfigDefinition(
        key="OPENAI_MODEL",
        attr="openai_model",
        label="OpenAI model",
        category="AI",
        description="Default model for OpenAI-compatible calls.",
        env_names=("OPENAI_MODEL",),
    ),
    RuntimeConfigDefinition(
        key="OPENAI_DIRECT_API_KEY",
        attr="openai_direct_api_key",
        label="Direct OpenAI API key",
        category="AI",
        description="Fallback direct OpenAI API key.",
        is_secret=True,
        env_names=("OPENAI_DIRECT_API_KEY", "OPENAI_API_KEY_DIRECT"),
    ),
    RuntimeConfigDefinition(
        key="OPENAI_DIRECT_BASE_URL",
        attr="openai_direct_base_url",
        label="Direct OpenAI base URL",
        category="AI",
        description="Fallback direct OpenAI-compatible base URL.",
        value_type="url",
        env_names=("OPENAI_DIRECT_BASE_URL",),
    ),
    RuntimeConfigDefinition(
        key="OPENAI_DIRECT_MODEL",
        attr="openai_direct_model",
        label="Direct OpenAI model",
        category="AI",
        description="Fallback direct OpenAI-compatible model.",
        env_names=("OPENAI_DIRECT_MODEL",),
    ),
    RuntimeConfigDefinition(
        key="FIREWORKS_API_KEY",
        attr="fireworks_api_key",
        label="Fireworks API key",
        category="AI",
        description="Fireworks fallback API key.",
        is_secret=True,
        env_names=("FIREWORKS_API_KEY",),
    ),
    RuntimeConfigDefinition(
        key="OPENROUTER_API_KEY",
        attr="openrouter_api_key",
        label="OpenRouter API key",
        category="AI",
        description="OpenRouter fallback API key.",
        is_secret=True,
        env_names=("OPENROUTER_API_KEY",),
    ),
    RuntimeConfigDefinition(
        key="RESUME_AI_API_KEY",
        attr="resume_ai_api_key",
        label="Resume AI API key",
        category="AI",
        description="Resume extraction provider key; falls back to the primary key when unset.",
        is_secret=True,
        env_names=("RESUME_AI_API_KEY",),
    ),
    RuntimeConfigDefinition(
        key="RESUME_AI_BASE_URL",
        attr="resume_ai_base_url",
        label="Resume AI base URL",
        category="AI",
        description="Resume extraction provider base URL.",
        value_type="url",
        env_names=("RESUME_AI_BASE_URL",),
    ),
    RuntimeConfigDefinition(
        key="RESUME_AI_MODEL",
        attr="resume_ai_model",
        label="Resume AI model",
        category="AI",
        description="Resume extraction model name.",
        env_names=("RESUME_AI_MODEL",),
    ),
    RuntimeConfigDefinition(
        key="AGENT_FAST_API_KEY",
        attr="agent_fast_api_key",
        label="Agent fast API key",
        category="Agent",
        description="API key for fast agent model tier.",
        is_secret=True,
        env_names=("AGENT_FAST_API_KEY",),
    ),
    RuntimeConfigDefinition(
        key="AGENT_FAST_BASE_URL",
        attr="agent_fast_base_url",
        label="Agent fast base URL",
        category="Agent",
        description="Base URL for fast agent model tier.",
        value_type="url",
        env_names=("AGENT_FAST_BASE_URL",),
    ),
    RuntimeConfigDefinition(
        key="AGENT_FAST_MODEL",
        attr="agent_fast_model",
        label="Agent fast model",
        category="Agent",
        description="Model name for fast agent model tier.",
        env_names=("AGENT_FAST_MODEL",),
    ),
    RuntimeConfigDefinition(
        key="AGENT_STRONG_API_KEY",
        attr="agent_strong_api_key",
        label="Agent strong API key",
        category="Agent",
        description="API key for strong agent model tier.",
        is_secret=True,
        env_names=("AGENT_STRONG_API_KEY",),
    ),
    RuntimeConfigDefinition(
        key="AGENT_STRONG_BASE_URL",
        attr="agent_strong_base_url",
        label="Agent strong base URL",
        category="Agent",
        description="Base URL for strong agent model tier.",
        value_type="url",
        env_names=("AGENT_STRONG_BASE_URL",),
    ),
    RuntimeConfigDefinition(
        key="AGENT_STRONG_MODEL",
        attr="agent_strong_model",
        label="Agent strong model",
        category="Agent",
        description="Model name for strong agent model tier.",
        env_names=("AGENT_STRONG_MODEL",),
    ),
    RuntimeConfigDefinition(
        key="AGENT_REASONING_API_KEY",
        attr="agent_reasoning_api_key",
        label="Agent reasoning API key",
        category="Agent",
        description="API key for reasoning agent model tier.",
        is_secret=True,
        env_names=("AGENT_REASONING_API_KEY",),
    ),
    RuntimeConfigDefinition(
        key="AGENT_REASONING_BASE_URL",
        attr="agent_reasoning_base_url",
        label="Agent reasoning base URL",
        category="Agent",
        description="Base URL for reasoning agent model tier.",
        value_type="url",
        env_names=("AGENT_REASONING_BASE_URL",),
    ),
    RuntimeConfigDefinition(
        key="AGENT_REASONING_MODEL",
        attr="agent_reasoning_model",
        label="Agent reasoning model",
        category="Agent",
        description="Model name for reasoning agent model tier.",
        env_names=("AGENT_REASONING_MODEL",),
    ),
    RuntimeConfigDefinition(
        key="GITHUB_API_TOKEN",
        attr="github_api_token",
        label="GitHub API token",
        category="Agent",
        description="GitHub token used by agent workflows.",
        is_secret=True,
        env_names=("GITHUB_API_TOKEN",),
    ),
    RuntimeConfigDefinition(
        key="GITHUB_DEFAULT_REPO",
        attr="github_default_repo",
        label="GitHub default repo",
        category="Agent",
        description="Default GitHub repo for agent workflows.",
        env_names=("GITHUB_DEFAULT_REPO",),
    ),
    RuntimeConfigDefinition(
        key="GITHUB_ALLOWED_REPOS",
        attr="github_allowed_repos",
        label="GitHub allowed repos",
        category="Agent",
        description="Comma-separated repo allowlist for agent workflows.",
        value_type="csv",
        env_names=("GITHUB_ALLOWED_REPOS",),
    ),
    RuntimeConfigDefinition(
        key="DISCORD_LOGS_WEBHOOK_URL",
        attr="discord_logs_webhook_url",
        label="Discord logs webhook URL",
        category="Observability",
        description="Webhook URL for operational/job notifications.",
        value_type="url",
        is_secret=True,
        env_names=("DISCORD_LOGS_WEBHOOK_URL",),
    ),
    RuntimeConfigDefinition(
        key="SENTRY_DSN",
        attr="sentry_dsn",
        label="Sentry DSN",
        category="Observability",
        description="Sentry DSN for error reporting.",
        value_type="url",
        is_secret=True,
        env_names=("SENTRY_DSN",),
        restart_required=True,
    ),
    RuntimeConfigDefinition(
        key="LANGFUSE_BASE_URL",
        attr="langfuse_base_url",
        label="Langfuse base URL",
        category="Observability",
        description="Langfuse endpoint for AI tracing.",
        value_type="url",
        env_names=("LANGFUSE_BASE_URL",),
    ),
    RuntimeConfigDefinition(
        key="EMAIL_USERNAME",
        attr="email_username",
        label="Mailbox username",
        category="Intake",
        description="Mailbox username for resume intake.",
        env_names=("EMAIL_USERNAME",),
    ),
    RuntimeConfigDefinition(
        key="EMAIL_PASSWORD",
        attr="email_password",
        label="Mailbox password",
        category="Intake",
        description="Mailbox password or app password for resume intake.",
        is_secret=True,
        env_names=("EMAIL_PASSWORD",),
    ),
    RuntimeConfigDefinition(
        key="IMAP_SERVER",
        attr="imap_server",
        label="IMAP server",
        category="Intake",
        description="IMAP server for mailbox resume intake.",
        env_names=("IMAP_SERVER",),
    ),
    RuntimeConfigDefinition(
        key="INTAKE_RESUME_ALLOWED_HOSTS",
        attr="intake_resume_allowed_hosts",
        label="Resume URL allowed hosts",
        category="Intake",
        description="Comma-separated host allowlist for intake resume URL fetches.",
        value_type="csv",
        env_names=("INTAKE_RESUME_ALLOWED_HOSTS",),
    ),
    RuntimeConfigDefinition(
        key="CRM_SYNC_INTERVAL_SECONDS",
        attr="crm_sync_interval_seconds",
        label="CRM sync interval",
        category="Operations",
        description="Seconds between automatic CRM sync runs.",
        value_type="int",
        env_names=("CRM_SYNC_INTERVAL_SECONDS",),
    ),
    RuntimeConfigDefinition(
        key="CRM_SYNC_PAGE_SIZE",
        attr="crm_sync_page_size",
        label="CRM sync page size",
        category="Operations",
        description="Page size for CRM sync pulls.",
        value_type="int",
        env_names=("CRM_SYNC_PAGE_SIZE",),
    ),
    RuntimeConfigDefinition(
        key="MAX_ATTACHMENTS_PER_CONTACT",
        attr="max_attachments_per_contact",
        label="Max attachments per contact",
        category="Operations",
        description="Maximum CRM attachments inspected per contact.",
        value_type="int",
        env_names=("MAX_ATTACHMENTS_PER_CONTACT",),
    ),
    RuntimeConfigDefinition(
        key="MAX_FILE_SIZE_MB",
        attr="max_file_size_mb",
        label="Max resume file size MB",
        category="Operations",
        description="Maximum resume attachment size in MiB.",
        value_type="int",
        env_names=("MAX_FILE_SIZE_MB",),
    ),
    RuntimeConfigDefinition(
        key="ALLOWED_FILE_TYPES",
        attr="allowed_file_types",
        label="Allowed resume file types",
        category="Operations",
        description="Comma-separated allowed resume file extensions.",
        value_type="csv",
        env_names=("ALLOWED_FILE_TYPES",),
    ),
    RuntimeConfigDefinition(
        key="GIG_RECRUITING_STALE_DAYS",
        attr="gig_recruiting_stale_days",
        label="Stale recruiting days",
        category="Operations",
        description="Days before recruiting gigs are considered stale.",
        value_type="int",
        env_names=("GIG_RECRUITING_STALE_DAYS",),
    ),
    RuntimeConfigDefinition(
        key="GIG_RECRUITING_REMINDER_MAX_AGE_DAYS",
        attr="gig_recruiting_reminder_max_age_days",
        label="Recruiting reminder max age",
        category="Operations",
        description="Maximum age in days for recruiting reminders.",
        value_type="int",
        env_names=("GIG_RECRUITING_REMINDER_MAX_AGE_DAYS",),
    ),
    RuntimeConfigDefinition(
        key="KIMAI_BASE_URL",
        attr="kimai_base_url",
        label="Kimai base URL",
        category="Legacy",
        description="Kimai endpoint for legacy time tracking workflows.",
        value_type="url",
        env_names=("KIMAI_BASE_URL",),
    ),
    RuntimeConfigDefinition(
        key="KIMAI_API_TOKEN",
        attr="kimai_api_token",
        label="Kimai API token",
        category="Legacy",
        description="Kimai API token for legacy time tracking workflows.",
        is_secret=True,
        env_names=("KIMAI_API_TOKEN",),
    ),
)

_DEFINITIONS_BY_KEY = {definition.key: definition for definition in _DEFINITIONS}
_DEFINITIONS_BY_ATTR = {definition.attr: definition for definition in _DEFINITIONS}
_CACHE_TTL_SECONDS = 5.0
_CACHE: dict[str, tuple[float, dict[str, str]]] = {}
_ENCRYPTED_PREFIX = "fernet:v1:"
_ENCRYPTION_KEY_ENV_NAMES = ("CONFIG_SECRET_KEY",)


def mask_runtime_secret(value: object) -> str | None:
    """Return a short confirmable mask without exposing the full secret."""
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) <= 10:
        return f"{text[:2]}...{text[-2:]}" if len(text) > 4 else "****"
    return f"{text[:5]}...{text[-5:]}"


def _runtime_config_secret_key() -> str | None:
    for env_name in _ENCRYPTION_KEY_ENV_NAMES:
        value = os.environ.get(env_name)
        if value is not None and value.strip():
            return value.strip()
    dotenv_values = _parse_dotenv_keys()
    for env_name in _ENCRYPTION_KEY_ENV_NAMES:
        value = dotenv_values.get(env_name)
        if value is not None and value.strip():
            return value.strip()
    return None


def runtime_secret_encryption_configured() -> bool:
    """Return whether dashboard-managed secrets can be encrypted/decrypted."""
    return _runtime_config_secret_key() is not None


def _runtime_config_fernet() -> Fernet:
    secret_key = _runtime_config_secret_key()
    if not secret_key:
        raise RuntimeError(
            "CONFIG_SECRET_KEY must be configured to save dashboard secrets"
        )
    derived_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"five08-runtime-config-v1",
        info=b"dashboard-runtime-secret-values",
    ).derive(secret_key.encode("utf-8"))
    return Fernet(urlsafe_b64encode(derived_key))


def _encrypt_secret_value(value: str) -> str:
    token = _runtime_config_fernet().encrypt(value.encode("utf-8")).decode("ascii")
    return f"{_ENCRYPTED_PREFIX}{token}"


def _decrypt_secret_value(value: str) -> str:
    if not value.startswith(_ENCRYPTED_PREFIX):
        logger.warning("Ignoring unencrypted runtime secret value")
        return ""
    token = value.removeprefix(_ENCRYPTED_PREFIX)
    try:
        return _runtime_config_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, RuntimeError, UnicodeDecodeError):
        logger.warning("Unable to decrypt runtime secret value")
        return ""


def runtime_config_definitions() -> tuple[RuntimeConfigDefinition, ...]:
    """Return all dashboard-configurable definitions."""
    return _DEFINITIONS


def runtime_config_definition_for_key(key: str) -> RuntimeConfigDefinition | None:
    """Return one definition by canonical key."""
    return _DEFINITIONS_BY_KEY.get(key.strip().upper())


def runtime_config_definition_for_attr(attr: str) -> RuntimeConfigDefinition | None:
    """Return one definition by settings attribute name."""
    return _DEFINITIONS_BY_ATTR.get(attr)


def _parse_dotenv_keys(path: Path = Path(".env")) -> dict[str, str]:
    if not path.exists():
        return {}
    parsed: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'\"")
            if key:
                parsed[key] = value
    except OSError:
        return {}
    return parsed


def env_value_for_definition(definition: RuntimeConfigDefinition) -> str | None:
    """Return an explicit environment or dotenv value when non-empty."""
    dotenv_values: dict[str, str] | None = None
    for env_name in definition.env_names:
        value = os.environ.get(env_name)
        if value is not None and value.strip():
            return value
        if dotenv_values is None:
            dotenv_values = _parse_dotenv_keys()
        dotenv_value = dotenv_values.get(env_name)
        if dotenv_value is not None and dotenv_value.strip():
            return dotenv_value
    return None


def definition_is_env_locked(definition: RuntimeConfigDefinition) -> bool:
    """Return whether a non-empty env/dotenv value should win over DB config."""
    return env_value_for_definition(definition) is not None


def runtime_config_db_overlay_enabled() -> bool:
    """Return whether settings attribute reads should consult the runtime DB."""
    if os.getenv("ENVIRONMENT", "").strip().casefold() == "test":
        return False
    if "pytest" in sys.modules and os.getenv("RUNTIME_CONFIG_TEST_ENABLE") != "true":
        return False
    return True


def _cache_key(settings: Any) -> str:
    return str(object.__getattribute__(settings, "postgres_url"))


def _load_db_values(settings: Any) -> dict[str, str]:
    key = _cache_key(settings)
    now = time.monotonic()
    cached = _CACHE.get(key)
    if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    values: dict[str, str] = {}
    try:
        with psycopg.connect(key) as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                cursor.execute("SELECT key, value FROM runtime_config_values")
                for row in cursor.fetchall():
                    row_key = str(row["key"])
                    definition = _DEFINITIONS_BY_KEY.get(row_key)
                    if definition is None:
                        continue
                    row_value = str(row["value"])
                    if definition.is_secret:
                        row_value = _decrypt_secret_value(row_value)
                    values[row_key] = row_value
    except Exception:
        logger.debug("Runtime configuration DB overlay is unavailable", exc_info=True)
    _CACHE[key] = (now, values)
    return values


def invalidate_runtime_config_cache(settings: Any | None = None) -> None:
    """Clear cached runtime config values."""
    if settings is None:
        _CACHE.clear()
        return
    _CACHE.pop(_cache_key(settings), None)


def coerce_runtime_config_value(
    definition: RuntimeConfigDefinition,
    value: object,
) -> str:
    """Validate and normalize an admin-supplied runtime config value."""
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif value is None:
        text = ""
    else:
        text = str(value).strip()

    if definition.value_type == "bool":
        normalized = text.casefold()
        if normalized in {"1", "true", "yes", "on"}:
            return "true"
        if normalized in {"0", "false", "no", "off"}:
            return "false"
        raise ValueError(f"{definition.key} must be a boolean")
    if definition.value_type == "int":
        try:
            return str(int(text))
        except ValueError as exc:
            raise ValueError(f"{definition.key} must be an integer") from exc
    if definition.value_type == "float":
        try:
            return str(float(text))
        except ValueError as exc:
            raise ValueError(f"{definition.key} must be a number") from exc
    if definition.value_type == "url" and text:
        if "://" not in text:
            raise ValueError(f"{definition.key} must be a URL")
    if definition.value_type == "csv":
        return ",".join(part.strip() for part in text.split(",") if part.strip())
    return text


def _coerce_for_settings(definition: RuntimeConfigDefinition, value: str) -> object:
    if definition.value_type == "bool":
        return value.casefold() in {"1", "true", "yes", "on"}
    if definition.value_type == "int":
        return int(value)
    if definition.value_type == "float":
        return float(value)
    return value


def resolve_runtime_setting_value(
    settings: Any,
    attr: str,
    default_value: object,
) -> object:
    """Return the env-locked, DB-backed, or default value for one setting attr."""
    definition = runtime_config_definition_for_attr(attr)
    if (
        definition is None
        or definition_is_env_locked(definition)
        or not runtime_config_db_overlay_enabled()
    ):
        return default_value
    raw_value = _load_db_values(settings).get(definition.key)
    if raw_value is None:
        return default_value
    try:
        return _coerce_for_settings(definition, raw_value)
    except Exception:
        logger.warning("Ignoring invalid runtime config value for %s", definition.key)
        return default_value


def list_runtime_config(settings: Any) -> list[dict[str, object]]:
    """Return dashboard-safe runtime config metadata and effective values."""
    db_values = _load_db_values(settings)
    items: list[dict[str, object]] = []
    for definition in _DEFINITIONS:
        env_locked = definition_is_env_locked(definition)
        raw_default = object.__getattribute__(settings, definition.attr)
        db_value = db_values.get(definition.key)
        effective = resolve_runtime_setting_value(
            settings, definition.attr, raw_default
        )
        source = "default"
        if env_locked:
            source = "env"
        elif db_value is not None:
            source = "database"
        configured = bool(str(effective or "").strip())
        item: dict[str, object] = {
            "key": definition.key,
            "label": definition.label,
            "category": definition.category,
            "description": definition.description,
            "value_type": definition.value_type,
            "is_secret": definition.is_secret,
            "env_locked": env_locked,
            "source": source,
            "configured": configured,
            "restart_required": definition.restart_required,
            "secret_encryption_configured": (
                runtime_secret_encryption_configured() if definition.is_secret else None
            ),
        }
        if not definition.is_secret:
            item["value"] = effective
        else:
            item["masked_value"] = mask_runtime_secret(effective)
        items.append(item)
    return items


def set_runtime_config_value(
    settings: Any,
    definition: RuntimeConfigDefinition,
    value: object,
    *,
    updated_by_provider: str | None = None,
    updated_by_subject: str | None = None,
) -> None:
    """Persist one runtime config value."""
    if definition_is_env_locked(definition):
        raise ValueError(f"{definition.key} is configured by environment")
    normalized = coerce_runtime_config_value(definition, value)
    stored_value = (
        _encrypt_secret_value(normalized) if definition.is_secret else normalized
    )
    query = """
        INSERT INTO runtime_config_values (
            key,
            value,
            updated_by_provider,
            updated_by_subject
        ) VALUES (%s, %s, %s, %s)
        ON CONFLICT (key) DO UPDATE SET
            value = EXCLUDED.value,
            updated_by_provider = EXCLUDED.updated_by_provider,
            updated_by_subject = EXCLUDED.updated_by_subject,
            updated_at = NOW()
    """
    with psycopg.connect(_cache_key(settings)) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (
                    definition.key,
                    stored_value,
                    updated_by_provider,
                    updated_by_subject,
                ),
            )
    invalidate_runtime_config_cache(settings)


def delete_runtime_config_value(
    settings: Any,
    definition: RuntimeConfigDefinition,
) -> None:
    """Delete one DB runtime config value."""
    if definition_is_env_locked(definition):
        raise ValueError(f"{definition.key} is configured by environment")
    with psycopg.connect(_cache_key(settings)) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM runtime_config_values WHERE key = %s",
                (definition.key,),
            )
    invalidate_runtime_config_cache(settings)
