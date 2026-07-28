"""Runtime configuration registry and database-backed overrides."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from base64 import urlsafe_b64encode
from dataclasses import dataclass
from functools import lru_cache
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
    legacy_keys: tuple[str, ...] = ()
    restart_required: bool = False
    min_value: float | None = None
    max_value: float | None = None

    @property
    def primary_env_name(self) -> str:
        return self.env_names[0] if self.env_names else self.key

    @property
    def all_keys(self) -> tuple[str, ...]:
        """Return the canonical dashboard key followed by compatible legacy keys."""
        return (self.key, *self.legacy_keys)


@dataclass(frozen=True)
class RuntimeConfigDBSnapshot:
    """Cached runtime configuration DB rows."""

    values: dict[str, str]
    present_keys: frozenset[str]


_DEFINITIONS: tuple[RuntimeConfigDefinition, ...] = (
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
        key="OUTLINE_BASE_URL",
        attr="outline_base_url",
        label="Outline base URL",
        category="Onboarding",
        description="Outline workspace URL used for wiki/project document calls.",
        value_type="url",
        env_names=("OUTLINE_BASE_URL",),
    ),
    RuntimeConfigDefinition(
        key="OUTLINE_ADMIN_API_KEY",
        attr="outline_admin_api_key",
        label="Outline admin API key",
        category="Onboarding",
        description="Privileged Outline key used for invitations and project wiki pages.",
        is_secret=True,
        env_names=("OUTLINE_ADMIN_API_KEY", "OUTLINE_API_KEY"),
        legacy_keys=("OUTLINE_API_KEY",),
    ),
    RuntimeConfigDefinition(
        key="MIGADU_API_USER",
        attr="migadu_api_user",
        label="Migadu API user",
        category="Mailbox",
        description="Migadu API username used for mailbox automation and newsletter dry runs.",
        env_names=("MIGADU_API_USER", "MIGADU_USER"),
    ),
    RuntimeConfigDefinition(
        key="MIGADU_API_KEY",
        attr="migadu_api_key",
        label="Migadu API key",
        category="Mailbox",
        description="Migadu API key used for mailbox automation and newsletter dry runs.",
        is_secret=True,
        env_names=("MIGADU_API_KEY",),
    ),
    RuntimeConfigDefinition(
        key="MIGADU_MAILBOX_DOMAIN",
        attr="migadu_mailbox_domain",
        label="Migadu mailbox domain",
        category="Mailbox",
        description="Migadu domain scanned for 508 member newsletter sync and mailbox creation.",
        env_names=("MIGADU_MAILBOX_DOMAIN", "MIGADU_DOMAIN"),
    ),
    RuntimeConfigDefinition(
        key="BREVO_API_KEY",
        attr="brevo_api_key",
        label="Brevo API key",
        category="Newsletter",
        description="Brevo API key used to add 508 member contacts to the newsletter list.",
        is_secret=True,
        env_names=("BREVO_API_KEY",),
    ),
    RuntimeConfigDefinition(
        key="BREVO_API_BASE_URL",
        attr="brevo_api_base_url",
        label="Brevo API base URL",
        category="Newsletter",
        description="Brevo API endpoint for newsletter contact sync.",
        value_type="url",
        env_names=("BREVO_API_BASE_URL",),
    ),
    RuntimeConfigDefinition(
        key="BREVO_API_TIMEOUT_SECONDS",
        attr="brevo_api_timeout_seconds",
        label="Brevo API timeout seconds",
        category="Newsletter",
        description="Network timeout for Brevo newsletter requests.",
        value_type="float",
        env_names=("BREVO_API_TIMEOUT_SECONDS",),
        min_value=1,
    ),
    RuntimeConfigDefinition(
        key="BREVO_508_MEMBERS_NEWSLETTER_LIST_ID",
        attr="brevo_508_members_newsletter_list_id",
        label="Brevo 508 members list ID",
        category="Newsletter",
        description="Optional explicit Brevo list ID; when unset the list is resolved by name.",
        value_type="int",
        env_names=("BREVO_508_MEMBERS_NEWSLETTER_LIST_ID",),
        min_value=1,
    ),
    RuntimeConfigDefinition(
        key="BREVO_508_MEMBERS_NEWSLETTER_LIST_NAME",
        attr="brevo_508_members_newsletter_list_name",
        label="Brevo 508 members list name",
        category="Newsletter",
        description="Brevo list name used when the explicit list ID is unset.",
        env_names=("BREVO_508_MEMBERS_NEWSLETTER_LIST_NAME",),
    ),
    RuntimeConfigDefinition(
        key="KEILA_API_KEY",
        attr="keila_api_key",
        label="Keila API key",
        category="Newsletter",
        description="Keila API key used to tag 508 member contacts.",
        is_secret=True,
        env_names=("KEILA_API_KEY",),
    ),
    RuntimeConfigDefinition(
        key="KEILA_API_BASE_URL",
        attr="keila_api_base_url",
        label="Keila API base URL",
        category="Newsletter",
        description="Keila API endpoint for newsletter contact sync.",
        value_type="url",
        env_names=("KEILA_API_BASE_URL", "KEILA_BASE_URL"),
    ),
    RuntimeConfigDefinition(
        key="KEILA_API_TIMEOUT_SECONDS",
        attr="keila_api_timeout_seconds",
        label="Keila API timeout seconds",
        category="Newsletter",
        description="Network timeout for Keila newsletter requests.",
        value_type="float",
        env_names=("KEILA_API_TIMEOUT_SECONDS",),
        min_value=1,
    ),
    RuntimeConfigDefinition(
        key="NEWSLETTER_SYNC_ENABLED",
        attr="newsletter_sync_enabled",
        label="Newsletter sync enabled",
        category="Newsletter",
        description="Whether the API starts the recurring 508 members newsletter sync scheduler.",
        value_type="bool",
        env_names=("NEWSLETTER_SYNC_ENABLED",),
        restart_required=True,
    ),
    RuntimeConfigDefinition(
        key="NEWSLETTER_SYNC_INTERVAL_SECONDS",
        attr="newsletter_sync_interval_seconds",
        label="Newsletter sync interval seconds",
        category="Newsletter",
        description="Seconds between recurring 508 members newsletter sync enqueue attempts.",
        value_type="int",
        env_names=("NEWSLETTER_SYNC_INTERVAL_SECONDS",),
        restart_required=True,
        min_value=60,
    ),
    RuntimeConfigDefinition(
        key="NEWSLETTER_SYNC_EXCLUDED_MAILBOXES",
        attr="newsletter_sync_excluded_mailboxes",
        label="Newsletter excluded mailboxes",
        category="Newsletter",
        description="Comma-separated Migadu mailbox local-parts or full addresses skipped by the 508 members resync.",
        value_type="csv",
        env_names=("NEWSLETTER_SYNC_EXCLUDED_MAILBOXES",),
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
        min_value=1,
    ),
    RuntimeConfigDefinition(
        key="ONBOARDING_EMAIL_SMTP_SERVER",
        attr="onboarding_email_smtp_server",
        label="Onboarding email SMTP server",
        category="Onboarding",
        description="SMTP server used to send onboarding emails.",
        env_names=("ONBOARDING_EMAIL_SMTP_SERVER", "SMTP_SERVER"),
    ),
    RuntimeConfigDefinition(
        key="ONBOARDING_EMAIL_SMTP_PORT",
        attr="onboarding_email_smtp_port",
        label="Onboarding email SMTP port",
        category="Onboarding",
        description="SMTP port used to send onboarding emails.",
        value_type="int",
        env_names=("ONBOARDING_EMAIL_SMTP_PORT", "SMTP_PORT"),
        min_value=1,
    ),
    RuntimeConfigDefinition(
        key="ONBOARDING_EMAIL_SMTP_USE_SSL",
        attr="onboarding_email_smtp_use_ssl",
        label="Onboarding email SMTP SSL",
        category="Onboarding",
        description="Use implicit TLS when connecting to the onboarding SMTP server.",
        value_type="bool",
        env_names=("ONBOARDING_EMAIL_SMTP_USE_SSL", "SMTP_USE_SSL"),
    ),
    RuntimeConfigDefinition(
        key="ONBOARDING_EMAIL_SMTP_STARTTLS",
        attr="onboarding_email_smtp_starttls",
        label="Onboarding email SMTP STARTTLS",
        category="Onboarding",
        description="Upgrade the onboarding SMTP connection with STARTTLS.",
        value_type="bool",
        env_names=("ONBOARDING_EMAIL_SMTP_STARTTLS", "SMTP_STARTTLS"),
    ),
    RuntimeConfigDefinition(
        key="ONBOARDING_EMAIL_SMTP_USERNAME",
        attr="onboarding_email_smtp_username",
        label="Onboarding email SMTP username",
        category="Onboarding",
        description="SMTP username used to authenticate onboarding email sends.",
        env_names=("ONBOARDING_EMAIL_SMTP_USERNAME", "SMTP_USERNAME"),
    ),
    RuntimeConfigDefinition(
        key="ONBOARDING_EMAIL_SMTP_PASSWORD",
        attr="onboarding_email_smtp_password",
        label="Onboarding email SMTP password",
        category="Onboarding",
        description="SMTP password or app password used to send onboarding emails.",
        is_secret=True,
        env_names=("ONBOARDING_EMAIL_SMTP_PASSWORD", "SMTP_PASSWORD"),
    ),
    RuntimeConfigDefinition(
        key="ONBOARDING_EMAIL_SENDER_EMAIL",
        attr="onboarding_email_sender_email",
        label="Onboarding email sender address",
        category="Onboarding",
        description=(
            "From email address for onboarding sends. The email uses the sender's "
            "name as the display name and sets Reply-To to the sender's email."
        ),
        env_names=("ONBOARDING_EMAIL_SENDER_EMAIL",),
    ),
    RuntimeConfigDefinition(
        key="ONBOARDING_EMAIL_SMTP_TIMEOUT_SECONDS",
        attr="onboarding_email_smtp_timeout_seconds",
        label="Onboarding email SMTP timeout seconds",
        category="Onboarding",
        description="SMTP timeout in seconds for onboarding email sends.",
        value_type="float",
        env_names=("ONBOARDING_EMAIL_SMTP_TIMEOUT_SECONDS", "SMTP_TIMEOUT_SECONDS"),
        min_value=0.1,
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
        key="JOB_LEAD_CLASSIFIER_ENABLED",
        attr="job_lead_classifier_enabled",
        label="Job lead classifier enabled",
        category="AI",
        description="Use an LLM to classify scraped HN job leads before keyword fallback.",
        value_type="bool",
        env_names=("JOB_LEAD_CLASSIFIER_ENABLED",),
    ),
    RuntimeConfigDefinition(
        key="JOB_LEAD_CLASSIFIER_MODEL",
        attr="job_lead_classifier_model",
        label="Job lead classifier model",
        category="AI",
        description="Optional model override for HN job lead classification.",
        env_names=("JOB_LEAD_CLASSIFIER_MODEL",),
    ),
    RuntimeConfigDefinition(
        key="JOB_LEAD_CLASSIFIER_TIMEOUT_SECONDS",
        attr="job_lead_classifier_timeout_seconds",
        label="Job lead classifier timeout seconds",
        category="AI",
        description="Timeout in seconds for each job lead classification LLM call.",
        value_type="float",
        env_names=("JOB_LEAD_CLASSIFIER_TIMEOUT_SECONDS",),
        min_value=0.1,
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
        restart_required=True,
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
        key="ONBOARDING_TALLY_API_KEY",
        attr="onboarding_tally_api_key",
        label="Onboarding Tally API key",
        category="Intake",
        description="Optional Tally API key for onboarding-form backfills.",
        is_secret=True,
        env_names=("ONBOARDING_TALLY_API_KEY", "TALLY_API_KEY"),
    ),
    RuntimeConfigDefinition(
        key="ONBOARDING_TALLY_WEBHOOK_SIGNING_SECRET",
        attr="onboarding_tally_webhook_signing_secret",
        label="Onboarding Tally webhook signing secret",
        category="Intake",
        description="Secret used to verify onboarding Tally webhook signatures.",
        is_secret=True,
        env_names=(
            "ONBOARDING_TALLY_WEBHOOK_SIGNING_SECRET",
            "TALLY_WEBHOOK_SIGNING_SECRET",
        ),
    ),
    RuntimeConfigDefinition(
        key="ONBOARDING_TALLY_ALLOWED_FORM_IDS",
        attr="onboarding_tally_allowed_form_ids",
        label="Onboarding Tally allowed form IDs",
        category="Intake",
        description="Required comma-separated allowlist of Tally form IDs accepted by onboarding intake.",
        value_type="csv",
        env_names=("ONBOARDING_TALLY_ALLOWED_FORM_IDS", "TALLY_ALLOWED_FORM_IDS"),
    ),
    RuntimeConfigDefinition(
        key="MAX_ATTACHMENTS_PER_CONTACT",
        attr="max_attachments_per_contact",
        label="Max attachments per contact",
        category="Operations",
        description="Maximum CRM attachments inspected per contact.",
        value_type="int",
        env_names=("MAX_ATTACHMENTS_PER_CONTACT",),
        min_value=1,
    ),
    RuntimeConfigDefinition(
        key="MAX_FILE_SIZE_MB",
        attr="max_file_size_mb",
        label="Max resume file size MB",
        category="Operations",
        description="Maximum resume attachment size in MiB.",
        value_type="int",
        env_names=("MAX_FILE_SIZE_MB",),
        min_value=1,
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
        min_value=1,
    ),
    RuntimeConfigDefinition(
        key="GIG_CONTACTED_REMINDER_DAYS",
        attr="gig_contacted_reminder_days",
        label="Contacted gig reminder days",
        category="Operations",
        description="Days a gig can remain contacted before a status reminder.",
        value_type="int",
        env_names=("GIG_CONTACTED_REMINDER_DAYS",),
        min_value=1,
    ),
    RuntimeConfigDefinition(
        key="GIG_RECRUITING_REMINDER_MAX_AGE_DAYS",
        attr="gig_recruiting_reminder_max_age_days",
        label="Recruiting reminder max age",
        category="Operations",
        description="Maximum age in days for recruiting reminders.",
        value_type="int",
        env_names=("GIG_RECRUITING_REMINDER_MAX_AGE_DAYS",),
        min_value=1,
    ),
)

_DEFINITIONS_BY_KEY = {
    key: definition for definition in _DEFINITIONS for key in definition.all_keys
}
_DEFINITIONS_BY_ATTR = {definition.attr: definition for definition in _DEFINITIONS}
_CACHE_TTL_SECONDS = 5.0
_CACHE: dict[str, tuple[float, RuntimeConfigDBSnapshot]] = {}
_CACHE_LOCK = threading.Lock()
_ENCRYPTED_PREFIX = "fernet:v1:"
_ENCRYPTION_KEY_ENV_NAMES = ("CONFIG_SECRET_KEY",)
_SECRET_MASK_PREFIXES = (
    "sk-or-v1-",
    "sk_or_v1_",
    "sk-or-",
    "sk_or_",
    "sk-live-",
    "sk-test-",
    "pk-live-",
    "pk-test-",
    "sk_live_",
    "sk_test_",
    "pk_live_",
    "pk_test_",
    "sk-",
    "sk_",
    "pk-",
    "pk_",
)


def mask_runtime_secret(value: object) -> str | None:
    """Return a short confirmable mask without exposing the full secret."""
    text = str(value or "").strip()
    if not text:
        return None
    meaningful = text
    for prefix in _SECRET_MASK_PREFIXES:
        if text.casefold().startswith(prefix):
            meaningful = text[len(prefix) :]
            break
    if len(meaningful) <= 6:
        return (
            f"{meaningful[:2]}...{meaningful[-2:]}" if len(meaningful) > 4 else "****"
        )
    return f"{meaningful[:3]}...{meaningful[-3:]}"


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


def _decrypt_secret_value(value: str) -> str | None:
    if not value.startswith(_ENCRYPTED_PREFIX):
        logger.warning("Ignoring unencrypted runtime secret value")
        return None
    token = value.removeprefix(_ENCRYPTED_PREFIX)
    try:
        return _runtime_config_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, RuntimeError, UnicodeDecodeError):
        logger.warning("Unable to decrypt runtime secret value")
        return None


def runtime_config_definitions() -> tuple[RuntimeConfigDefinition, ...]:
    """Return all dashboard-configurable definitions."""
    return _DEFINITIONS


def runtime_config_definition_for_key(key: str) -> RuntimeConfigDefinition | None:
    """Return one definition by canonical key."""
    return _DEFINITIONS_BY_KEY.get(key.strip().upper())


def runtime_config_definition_for_attr(attr: str) -> RuntimeConfigDefinition | None:
    """Return one definition by settings attribute name."""
    return _DEFINITIONS_BY_ATTR.get(attr)


@lru_cache(maxsize=1)
def _parse_dotenv_keys_cached(path_str: str) -> dict[str, str]:
    path = Path(path_str)
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


def _parse_dotenv_keys(path: Path = Path(".env")) -> dict[str, str]:
    return _parse_dotenv_keys_cached(str(path))


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


def _empty_db_snapshot() -> RuntimeConfigDBSnapshot:
    return RuntimeConfigDBSnapshot(values={}, present_keys=frozenset())


def _load_db_snapshot(settings: Any) -> RuntimeConfigDBSnapshot:
    key = _cache_key(settings)
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1]

    values: dict[str, str] = {}
    present_keys: set[str] = set()
    candidates: dict[str, tuple[RuntimeConfigDefinition, str]] = {}
    try:
        with psycopg.connect(key) as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                cursor.execute("SELECT key, value FROM runtime_config_values")
                for row in cursor.fetchall():
                    row_key = str(row["key"]).strip().upper()
                    definition = _DEFINITIONS_BY_KEY.get(row_key)
                    if definition is None:
                        continue

                    canonical_key = definition.key
                    if row_key != canonical_key and canonical_key in candidates:
                        continue
                    candidates[canonical_key] = (definition, str(row["value"]))

        for canonical_key, (definition, row_value) in candidates.items():
            present_keys.add(canonical_key)
            if definition.is_secret:
                decrypted_value = _decrypt_secret_value(row_value)
                if decrypted_value is None:
                    continue
                row_value = decrypted_value
            values[canonical_key] = row_value
    except Exception:
        logger.debug("Runtime configuration DB overlay is unavailable", exc_info=True)
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
            if cached is not None:
                return cached[1]
        return _empty_db_snapshot()

    snapshot = RuntimeConfigDBSnapshot(
        values=values,
        present_keys=frozenset(present_keys),
    )
    with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic(), snapshot)
    return snapshot


def _load_db_values(settings: Any) -> dict[str, str]:
    return dict(_load_db_snapshot(settings).values)


def invalidate_runtime_config_cache(settings: Any | None = None) -> None:
    """Clear cached runtime config values."""
    with _CACHE_LOCK:
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

    if not definition.is_secret and definition.value_type in {"string", "url", "csv"}:
        if not text:
            raise ValueError(f"{definition.key} must not be blank")
    if definition.value_type == "bool":
        normalized = text.casefold()
        if normalized in {"1", "true", "yes", "on"}:
            return "true"
        if normalized in {"0", "false", "no", "off"}:
            return "false"
        raise ValueError(f"{definition.key} must be a boolean")
    if definition.value_type == "int":
        try:
            normalized_int = int(text)
        except ValueError as exc:
            raise ValueError(f"{definition.key} must be an integer") from exc
        if definition.min_value is not None and normalized_int < definition.min_value:
            raise ValueError(
                f"{definition.key} must be greater than or equal to "
                f"{definition.min_value:g}"
            )
        if definition.max_value is not None and normalized_int > definition.max_value:
            raise ValueError(
                f"{definition.key} must be less than or equal to "
                f"{definition.max_value:g}"
            )
        return str(normalized_int)
    if definition.value_type == "float":
        try:
            normalized_float = float(text)
        except ValueError as exc:
            raise ValueError(f"{definition.key} must be a number") from exc
        if definition.min_value is not None and normalized_float < definition.min_value:
            raise ValueError(
                f"{definition.key} must be greater than or equal to "
                f"{definition.min_value:g}"
            )
        if definition.max_value is not None and normalized_float > definition.max_value:
            raise ValueError(
                f"{definition.key} must be less than or equal to "
                f"{definition.max_value:g}"
            )
        return str(normalized_float)
    if definition.value_type == "url" and text:
        if "://" not in text:
            raise ValueError(f"{definition.key} must be a URL")
    if definition.value_type == "csv":
        normalized_csv = ",".join(
            part.strip() for part in text.split(",") if part.strip()
        )
        if not normalized_csv:
            raise ValueError(f"{definition.key} must not be blank")
        return normalized_csv
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
        or not runtime_config_db_overlay_enabled()
        or definition_is_env_locked(definition)
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
    db_snapshot = _load_db_snapshot(settings)
    db_values = db_snapshot.values
    db_overlay_enabled = runtime_config_db_overlay_enabled()
    items: list[dict[str, object]] = []
    for definition in _DEFINITIONS:
        env_locked = definition_is_env_locked(definition)
        raw_default = object.__getattribute__(settings, definition.attr)
        db_present = definition.key in db_snapshot.present_keys
        db_value = db_values.get(definition.key)
        effective = raw_default
        source = "default"
        if env_locked:
            source = "env"
        elif db_present:
            source = "database"
            if db_value is not None and db_overlay_enabled:
                try:
                    effective = _coerce_for_settings(definition, db_value)
                except Exception:
                    logger.warning(
                        "Ignoring invalid runtime config value for %s",
                        definition.key,
                    )
        configured = (
            env_locked
            or db_present
            or definition.value_type in {"bool", "int", "float"}
            or bool(str(effective or "").strip())
        )
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
            item["masked_value"] = (
                mask_runtime_secret(effective)
                if source == "database" and db_value is not None
                else None
            )
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
            for legacy_key in definition.legacy_keys:
                cursor.execute(
                    "DELETE FROM runtime_config_values WHERE key = %s",
                    (legacy_key,),
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
            for key in definition.all_keys:
                cursor.execute(
                    "DELETE FROM runtime_config_values WHERE key = %s",
                    (key,),
                )
    invalidate_runtime_config_cache(settings)
