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


DEFAULT_POSTGRES_URL = "postgresql://postgres:postgres@127.0.0.1:5432/workflows"


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
    postgres_url: str = DEFAULT_POSTGRES_URL
    job_max_attempts: int = 8
    job_retry_base_seconds: int = 5
    job_retry_max_seconds: int = 300
    job_timeout_seconds: int = 600
    job_result_ttl_seconds: int = 3600
    gig_recruiting_stale_days: int = Field(default=7, ge=1)
    gig_contacted_reminder_days: int = Field(default=5, ge=1)
    gig_recruiting_reminder_max_age_days: int = Field(default=90, ge=1)
    onboarding_reminders_enabled: bool = False
    onboarding_reminder_stale_days: int = Field(default=7, ge=1)
    onboarding_reminder_repeat_days: int = Field(default=7, ge=1)
    onboarding_reminder_check_seconds: int = Field(default=3600, ge=60)
    discord_onboarding_volunteers_channel_id: str | None = None
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
    # The Discord agent gateway accepts role-bearing requests. Keep its
    # credential separate from general internal API callers so a holder of the
    # latter cannot fabricate privileged agent context.
    agent_shared_secret: str | None = None
    # The legacy API-secret fallback is deliberately opt-in and only honored
    # in explicit local/test environments. It exists solely to ease developer
    # migration to AGENT_SHARED_SECRET.
    agent_allow_legacy_api_secret: bool = False
    # Agent authorization is bound to immutable Discord snowflake IDs in
    # deployed environments. A combined role (for example Billing / ERP Dev)
    # is intentionally listed in every applicable bundle.
    agent_discord_guild_ids: str = ""
    agent_discord_admin_role_ids: str = ""
    agent_discord_steering_committee_role_ids: str = ""
    agent_discord_billing_role_ids: str = ""
    agent_discord_erp_developer_role_ids: str = ""
    # These preserve established project-manager and engineer capability
    # bundles without relying on mutable role names in production.
    agent_discord_project_manager_role_ids: str = ""
    agent_discord_engineer_role_ids: str = ""
    # Role-name matching is a local/test-only migration convenience. It is
    # never effective in deployed environments, even if set accidentally.
    agent_allow_role_name_fallback: bool = False
    webhook_shared_secret: str | None = None
    discord_logs_webhook_url: str | None = None
    discord_logs_webhook_wait: bool = True
    docuseal_base_url: str | None = None
    docuseal_api_key: str | None = None
    docuseal_member_agreement_template_id: int | None = None
    # GitHub issues are the canonical todo backend.  The default is deliberately
    # in code so every deployment gets the sentinel todo repository without a
    # redundant environment setting.
    github_default_repo: str = "508-dev/todos"
    github_organization: str = "508-dev"
    github_member_extra_repos: str = ""
    github_steering_all_installed_repos: bool = True
    github_steering_extra_repos: str = ""

    # GitHub App credentials are preferred. GitHub recommends the Client ID as
    # the JWT issuer. GITHUB_APP_ID remains an input-only compatibility alias
    # while deployments migrate. GITHUB_API_TOKEN and GITHUB_ALLOWED_REPOS also
    # remain as a temporary compatibility path.
    github_app_client_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "GITHUB_APP_CLIENT_ID",
            "GITHUB_APP_ID",
            "github_app_client_id",
            "github_app_id",
        ),
    )
    github_app_installation_id: str | None = None
    github_app_private_key: str | None = None
    github_api_token: str | None = None
    github_allowed_repos: str = ""
    # Public-web research is deliberately configured separately from internal
    # integrations. The registry only uses providers with usable credentials or
    # an explicitly configured SearXNG endpoint.
    agent_web_search_provider_order: str = "searxng,brave,firecrawl"
    agent_web_search_timeout_seconds: float = Field(default=5.0, ge=1.0, le=60.0)
    agent_web_default_result_limit: int = Field(default=5, ge=1, le=10)
    agent_planning_max_steps: int = Field(default=3, ge=1, le=5)
    # A caller-visible bound for planning/read requests. It protects Discord
    # interaction handling even if a DNS lookup or an upstream stream ignores
    # a lower-level request timeout.
    agent_request_response_budget_seconds: float = Field(
        default=55.0,
        ge=1.0,
        le=55.0,
    )
    # Keep one synchronous Discord-facing public-web loop inside the response
    # budget even when all configured providers fail over.
    agent_public_web_deadline_seconds: float = Field(
        default=50.0,
        ge=5.0,
        le=55.0,
    )
    searxng_base_url: str | None = None
    searxng_search_language: str | None = None
    brave_search_api_key: str | None = None
    brave_search_base_url: str = "https://api.search.brave.com"
    brave_search_country: str | None = None
    brave_search_language: str | None = None
    firecrawl_api_key: str | None = None
    firecrawl_base_url: str = "https://api.firecrawl.dev"
    erpnext_base_url: str | None = None
    erpnext_api_key: str | None = None
    erpnext_api_timeout_seconds: float = 20.0
    # The ERPNext credentials used by the agent are bound to one resolved
    # Discord organization. Leave this unset to fail closed for ERP reads.
    agent_erp_organization_id: str | None = None
    migadu_api_user: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "MIGADU_API_USER",
            "MIGADU_USER",
            "migadu_api_user",
        ),
    )
    migadu_api_key: str | None = None
    migadu_mailbox_domain: str = Field(
        default="508.dev",
        validation_alias=AliasChoices(
            "MIGADU_MAILBOX_DOMAIN",
            "MIGADU_DOMAIN",
            "migadu_mailbox_domain",
        ),
    )
    authentik_api_base_url: str | None = None
    authentik_api_token: str | None = None
    authentik_api_timeout_seconds: float = 20.0
    authentik_recovery_email_stage_id: str | None = None
    authentik_recovery_email_stage_name: str = "default-recovery-email"
    outline_base_url: str = "https://app.getoutline.com"
    outline_admin_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "OUTLINE_ADMIN_API_KEY",
            "outline_admin_api_key",
        ),
    )
    legacy_outline_admin_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OUTLINE_API_KEY", "outline_api_key"),
        exclude=True,
        repr=False,
    )
    # Shared member-safe content credential used by Discord wiki search and
    # project wiki matching. Keep it separate from the invitation-only key.
    outline_contents_api_key: str | None = None
    outline_api_timeout_seconds: float = 20.0
    brevo_api_key: str | None = None
    brevo_api_base_url: str = "https://api.brevo.com/v3"
    brevo_api_timeout_seconds: float = 20.0
    brevo_508_members_newsletter_list_id: int | None = Field(default=None, ge=1)
    brevo_508_members_newsletter_list_name: str = "508 members"
    keila_api_key: str | None = None
    keila_api_base_url: str = Field(
        default="https://app.keila.io",
        validation_alias=AliasChoices(
            "KEILA_API_BASE_URL",
            "KEILA_BASE_URL",
            "keila_api_base_url",
        ),
    )
    keila_api_timeout_seconds: float = 20.0
    newsletter_sync_enabled: bool = False
    newsletter_sync_interval_seconds: int = Field(default=604800, ge=60)
    newsletter_sync_excluded_mailboxes: str = ""
    onboarding_email_smtp_server: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "ONBOARDING_EMAIL_SMTP_SERVER",
            "SMTP_SERVER",
            "onboarding_email_smtp_server",
            "smtp_server",
        ),
    )
    onboarding_email_smtp_port: int = Field(
        default=465,
        validation_alias=AliasChoices(
            "ONBOARDING_EMAIL_SMTP_PORT",
            "SMTP_PORT",
            "onboarding_email_smtp_port",
            "smtp_port",
        ),
    )
    onboarding_email_smtp_use_ssl: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "ONBOARDING_EMAIL_SMTP_USE_SSL",
            "SMTP_USE_SSL",
            "onboarding_email_smtp_use_ssl",
            "smtp_use_ssl",
        ),
    )
    onboarding_email_smtp_starttls: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "ONBOARDING_EMAIL_SMTP_STARTTLS",
            "SMTP_STARTTLS",
            "onboarding_email_smtp_starttls",
            "smtp_starttls",
        ),
    )
    onboarding_email_smtp_username: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "ONBOARDING_EMAIL_SMTP_USERNAME",
            "SMTP_USERNAME",
            "onboarding_email_smtp_username",
            "smtp_username",
        ),
    )
    onboarding_email_smtp_password: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "ONBOARDING_EMAIL_SMTP_PASSWORD",
            "SMTP_PASSWORD",
            "onboarding_email_smtp_password",
            "smtp_password",
        ),
    )
    onboarding_email_sender_email: str = "onboarding@508.dev"
    onboarding_email_smtp_timeout_seconds: float = Field(
        default=20.0,
        validation_alias=AliasChoices(
            "ONBOARDING_EMAIL_SMTP_TIMEOUT_SECONDS",
            "SMTP_TIMEOUT_SECONDS",
            "onboarding_email_smtp_timeout_seconds",
            "smtp_timeout_seconds",
        ),
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    def __getattribute__(self, name: str) -> object:
        value = super().__getattribute__(name)
        if name.startswith("_"):
            return value
        try:
            from five08.runtime_config import resolve_runtime_setting_value

            return resolve_runtime_setting_value(self, name, value)
        except ImportError:
            return value

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

    @field_validator("brevo_508_members_newsletter_list_id", mode="before")
    @classmethod
    def _normalize_brevo_508_members_newsletter_list_id(
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
                    "BREVO_508_MEMBERS_NEWSLETTER_LIST_ID must be an integer"
                ) from exc
        raise TypeError("BREVO_508_MEMBERS_NEWSLETTER_LIST_ID must be an integer")

    @field_validator(
        "agent_discord_guild_ids",
        "agent_discord_admin_role_ids",
        "agent_discord_steering_committee_role_ids",
        "agent_discord_billing_role_ids",
        "agent_discord_erp_developer_role_ids",
        "agent_discord_project_manager_role_ids",
        "agent_discord_engineer_role_ids",
        mode="before",
    )
    @classmethod
    def _normalize_discord_id_list(cls, value: object) -> str:
        """Normalize comma-separated positive Discord IDs at config load time."""

        if value is None:
            return ""
        if not isinstance(value, str):
            raise TypeError("Discord ID mappings must be comma-separated strings")
        normalized: list[str] = []
        for raw_id in value.split(","):
            discord_id = raw_id.strip()
            if not discord_id:
                continue
            if not discord_id.isdecimal() or int(discord_id) <= 0:
                raise ValueError(
                    "Discord ID mappings must contain positive decimal IDs"
                )
            if discord_id not in normalized:
                normalized.append(discord_id)
        return ",".join(normalized)

    @model_validator(mode="after")
    def _reject_everyone_agent_role_bindings(self) -> "SharedSettings":
        """Reject a guild's ``@everyone`` role as an agent capability grant.

        Discord assigns the guild's own snowflake to its ``@everyone`` role.
        Allowing an agent role bundle to contain any allowed guild ID would
        grant that bundle to every member of that guild.
        """

        guild_ids = self._discord_ids(self.agent_discord_guild_ids)
        configured_role_ids = set().union(*self.agent_discord_role_id_bindings.values())
        overlapping_ids = sorted(guild_ids & configured_role_ids)
        if overlapping_ids:
            raise ValueError(
                "Agent Discord role IDs must not include an allowed guild ID "
                "because that is the @everyone role"
            )
        return self

    @model_validator(mode="after")
    def _resolve_outline_admin_api_key_alias(self) -> "SharedSettings":
        """Prefer a non-empty canonical Outline key, then its legacy alias."""
        canonical_value = object.__getattribute__(self, "outline_admin_api_key")
        legacy_value = object.__getattribute__(
            self,
            "legacy_outline_admin_api_key",
        )
        canonical_key = (canonical_value or "").strip()
        legacy_key = (legacy_value or "").strip()
        object.__setattr__(
            self,
            "outline_admin_api_key",
            canonical_value
            if canonical_key
            else (legacy_value if legacy_key else None),
        )
        return self

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

    @property
    def sentry_environment_name(self) -> str:
        """Sentry environment always follows the app runtime environment."""
        return self.environment

    @property
    def outline_api_key(self) -> str | None:
        """Return the deprecated compatibility alias for the admin Outline key."""
        return self.outline_admin_api_key

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

    @staticmethod
    def _discord_ids(value: str) -> frozenset[str]:
        return frozenset(item for item in value.split(",") if item)

    @property
    def agent_discord_role_id_bindings(self) -> dict[str, frozenset[str]]:
        """Return the configured Discord role-ID grants by policy bundle."""

        return {
            "admin": self._discord_ids(self.agent_discord_admin_role_ids),
            "steering_committee": self._discord_ids(
                self.agent_discord_steering_committee_role_ids
            ),
            "billing": self._discord_ids(self.agent_discord_billing_role_ids),
            "erp_developer": self._discord_ids(
                self.agent_discord_erp_developer_role_ids
            ),
            "project_manager": self._discord_ids(
                self.agent_discord_project_manager_role_ids
            ),
            "engineer": self._discord_ids(self.agent_discord_engineer_role_ids),
        }

    @property
    def agent_discord_guild_id_set(self) -> frozenset[str]:
        """Return configured Discord guilds allowed to use the agent."""

        return self._discord_ids(self.agent_discord_guild_ids)

    @property
    def agent_role_name_fallback_enabled(self) -> bool:
        """Allow role names only after an explicit local/test opt-in."""

        local_environments = {"local", "development", "dev", "test", "testing"}
        return (
            bool(self.agent_allow_role_name_fallback)
            and self.environment.strip().casefold() in local_environments
        )
