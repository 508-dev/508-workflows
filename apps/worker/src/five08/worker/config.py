"""Configuration for webhook ingest and worker services."""

from urllib.parse import urlparse

from pydantic import AliasChoices, Field, PrivateAttr, field_validator, model_validator

from five08.openai_fallback import (
    OpenAICompatibleProvider,
    build_openai_compatible_provider_attempts,
)
from five08.settings import SharedSettings


class WorkerSettings(SharedSettings):
    """Worker-specific settings layered on top of shared stack settings."""

    _crm_intake_completed_field: str = PrivateAttr(default="")

    worker_name: str = "worker"
    worker_queue_names: str = "jobs.default"
    worker_burst: bool = False
    discord_bot_internal_base_url: str = "http://127.0.0.1:3000"

    espo_base_url: str = ""
    espo_api_key: str = ""
    google_forms_allowed_form_ids: str = ""
    onboarding_tally_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ONBOARDING_TALLY_API_KEY", "TALLY_API_KEY"),
    )
    onboarding_tally_allowed_form_ids: str = Field(
        default="",
        validation_alias=AliasChoices(
            "ONBOARDING_TALLY_ALLOWED_FORM_IDS",
            "TALLY_ALLOWED_FORM_IDS",
        ),
    )
    onboarding_tally_webhook_signing_secret: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "ONBOARDING_TALLY_WEBHOOK_SIGNING_SECRET",
            "TALLY_WEBHOOK_SIGNING_SECRET",
        ),
    )

    openai_api_key: str | None = None
    openai_base_url: str | None = None
    openai_model: str = "gpt-5-mini"
    openai_direct_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "OPENAI_DIRECT_API_KEY",
            "OPENAI_API_KEY_DIRECT",
            "openai_direct_api_key",
            "openai_api_key_direct",
        ),
    )
    openai_direct_base_url: str | None = None
    openai_direct_model: str | None = None
    fireworks_api_key: str | None = None
    openrouter_api_key: str | None = None
    agent_planner_model: str = "accounts/fireworks/models/kimi-k2p6"
    agent_fallback_model: str = "gpt-4.1-mini"
    agent_intent_normalizer_enabled: bool = True
    agent_intent_normalizer_timeout_seconds: float = 3.0
    agent_fast_api_key: str | None = None
    agent_fast_base_url: str | None = None
    agent_fast_model: str | None = None
    agent_strong_api_key: str | None = None
    agent_strong_base_url: str | None = None
    agent_strong_model: str | None = None
    agent_reasoning_api_key: str | None = None
    agent_reasoning_base_url: str | None = None
    agent_reasoning_model: str | None = None
    resume_ai_api_key: str | None = None
    resume_ai_base_url: str | None = None
    resume_ai_model: str = "gpt-4.1-mini"
    resume_extractor_max_tokens: int = 2000
    resume_extractor_version: str = "v1"
    max_file_size_mb: int = 10
    allowed_file_types: str = "pdf,docx"
    max_attachments_per_contact: int = 3
    crm_sync_enabled: bool = True
    crm_sync_interval_seconds: int = 900
    crm_sync_page_size: int = 200

    @property
    def worker_queue_name(self) -> str:
        queue_names = [
            name.strip() for name in self.worker_queue_names.split(",") if name.strip()
        ]
        if len(queue_names) > 1:
            raise ValueError(
                "WORKER_QUEUE_NAMES currently supports one queue name. "
                "Configure a single queue to align actor registration and worker consume set."
            )
        if queue_names:
            return queue_names[0]
        return self.redis_queue_name

    email_resume_intake_enabled: bool = False
    check_email_wait: int = 2
    email_username: str | None = None
    email_password: str | None = None
    imap_server: str | None = None
    imap_timeout_seconds: float = 10.0
    intake_resume_fetch_timeout_seconds: float = Field(default=20.0, gt=0)
    intake_resume_max_redirects: int = Field(default=3, ge=0)
    intake_resume_allowed_hosts: str = ""
    intake_resume_require_virus_scan: bool = False
    intake_resume_virus_scan_command: str = ""
    intake_resume_virus_scan_timeout_seconds: float = Field(default=30.0, gt=0)
    email_resume_allowed_extensions: str = "pdf,docx"
    email_resume_max_file_size_mb: int = 10
    email_require_sender_auth_headers: bool = True
    oidc_issuer_url: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_scope: str = "openid profile email groups"
    oidc_groups_claim: str = "groups"
    oidc_admin_groups: str = "authentik Admins"
    oidc_callback_path: str = "/auth/callback"
    oidc_redirect_base_url: str | None = None
    auth_session_cookie_name: str = "five08_session"
    auth_session_ttl_seconds: int = Field(default=86400, ge=60)
    dashboard_default_path: str = "/dashboard"
    dashboard_public_base_url: str | None = None
    discord_bot_token: str | None = None
    discord_server_id: str | None = None
    discord_admin_roles: str = "Admin,Owner"
    discord_api_timeout_seconds: float = 8.0
    discord_link_ttl_seconds: int = 600
    discord_link_require_oidc_identity_checks: bool = True

    @field_validator("espo_base_url", "espo_api_key", mode="before")
    @classmethod
    def _strip_optional_runtime_config_string(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @model_validator(mode="after")
    def validate_required_crm_settings(self) -> "WorkerSettings":
        """Require EspoCRM settings outside local/test runtime environments."""
        env = self.environment.strip().lower()
        if env in {"local", "dev", "development", "test"}:
            return self
        if not self.espo_base_url or not self.espo_api_key:
            raise ValueError(
                "ESPO_BASE_URL and ESPO_API_KEY must be set when ENVIRONMENT "
                "is non-local."
            )
        return self

    @property
    def google_forms_allowed_form_ids_set(self) -> set[str]:
        """Allowed Google Forms IDs used by intake webhook validation."""
        return {
            form_id.strip()
            for form_id in self.google_forms_allowed_form_ids.split(",")
            if form_id.strip()
        }

    @property
    def onboarding_tally_allowed_form_ids_set(self) -> set[str]:
        """Allowed Tally form IDs used by onboarding intake webhook validation."""
        return {
            form_id.strip()
            for form_id in self.onboarding_tally_allowed_form_ids.split(",")
            if form_id.strip()
        }

    @model_validator(mode="after")
    def validate_email_resume_intake_settings(self) -> "WorkerSettings":
        """Require mailbox settings when worker-side email intake is enabled."""
        if not self.email_resume_intake_enabled:
            return self

        if not (self.email_username or "").strip():
            raise ValueError(
                "EMAIL_USERNAME must be set when EMAIL_RESUME_INTAKE_ENABLED=true"
            )
        if not (self.email_password or "").strip():
            raise ValueError(
                "EMAIL_PASSWORD must be set when EMAIL_RESUME_INTAKE_ENABLED=true"
            )
        if not (self.imap_server or "").strip():
            raise ValueError(
                "IMAP_SERVER must be set when EMAIL_RESUME_INTAKE_ENABLED=true"
            )
        return self

    @model_validator(mode="after")
    def validate_intake_resume_scan_settings(self) -> "WorkerSettings":
        """Require a scanner command when intake resume scanning is enabled."""
        if (
            self.intake_resume_require_virus_scan
            and not (self.intake_resume_virus_scan_command or "").strip()
        ):
            raise ValueError(
                "INTAKE_RESUME_VIRUS_SCAN_COMMAND must be set when "
                "INTAKE_RESUME_REQUIRE_VIRUS_SCAN=true"
            )
        return self

    @property
    def allowed_file_extensions(self) -> set[str]:
        """Allowed resume file extensions."""
        return {ext.strip().lower() for ext in self.allowed_file_types.split(",")}

    @property
    def crm_intake_completed_field(self) -> str:
        """Intake completion field remains intentionally unset until explicitly adopted."""
        return self._crm_intake_completed_field

    @crm_intake_completed_field.setter
    def crm_intake_completed_field(self, value: str) -> None:
        """Allow tests to override the unset intake-completed mapping when needed."""
        self._crm_intake_completed_field = value

    @property
    def oidc_http_timeout_seconds(self) -> float:
        """Keep OIDC network calls bounded with a fixed timeout."""
        return 8.0

    @property
    def oidc_jwks_cache_seconds(self) -> int:
        """Cache OIDC signing keys briefly to avoid repeated JWKS fetches."""
        return 300

    @property
    def auth_state_ttl_seconds(self) -> int:
        """Short-lived state tokens reduce replay risk during login."""
        return 600

    @property
    def auth_cookie_secure(self) -> bool:
        """Use secure cookies outside local/dev/test environments."""
        env = self.environment.strip().lower()
        return env not in {"local", "dev", "development", "test"}

    @property
    def auth_cookie_samesite(self) -> str:
        """Auth session cookies use SameSite=Lax."""
        return "lax"

    @property
    def parsed_resume_keywords(self) -> set[str]:
        """Keywords used to identify resume-like attachments."""
        return {
            keyword.strip().lower()
            for keyword in ("resume,cv,curriculum").split(",")
            if keyword.strip()
        }

    @property
    def intake_resume_allowed_hostnames(self) -> set[str]:
        """Optional host allowlist for intake resume URL fetches."""
        normalized_hosts: set[str] = set()
        for raw_host in self.intake_resume_allowed_hosts.split(","):
            host = raw_host.strip().lower().strip(".")
            if host:
                normalized_hosts.add(host)
        return normalized_hosts

    @property
    def resolved_resume_ai_model(self) -> str:
        """Resolve provider-specific resume model name (e.g. OpenRouter prefixes)."""
        candidate = self.resume_ai_model.strip()
        if not candidate:
            candidate = self.openai_model.strip()
        if not candidate:
            return "gpt-4.1-mini"

        # Keep explicit provider prefixes intact.
        if "/" in candidate:
            return candidate

        base_url = (self.resolved_resume_ai_base_url or "").strip()
        if not base_url:
            return candidate

        parsed = urlparse(base_url)
        host = (parsed.netloc or parsed.path).split("/")[0].split(":")[0].lower()
        if host.endswith("openrouter.ai"):
            return f"openai/{candidate}"
        return candidate

    @property
    def resolved_resume_ai_api_key(self) -> str | None:
        """Return the resume-specific API key, falling back to the OpenAI key."""
        return (self.resume_ai_api_key or "").strip() or (
            (self.openai_api_key or "").strip() or None
        )

    @property
    def resolved_resume_ai_base_url(self) -> str | None:
        """Return the resume-specific base URL, falling back to the OpenAI base URL."""
        if (self.resume_ai_api_key or "").strip() and (
            self.resume_ai_base_url or ""
        ).strip():
            return self.resume_ai_base_url
        return self.openai_base_url

    @property
    def resolved_openai_direct_api_key(self) -> str | None:
        """Return the preferred direct OpenAI key, including legacy env support."""
        return (self.openai_direct_api_key or "").strip() or None

    @property
    def resolved_resume_ai_provider_attempts(
        self,
    ) -> tuple[OpenAICompatibleProvider, ...]:
        """Ordered resume LLM providers with direct fallbacks after Bifrost."""
        return build_openai_compatible_provider_attempts(
            primary_model=self.resolved_resume_ai_model,
            primary_api_key=self.resolved_resume_ai_api_key,
            primary_base_url=self.resolved_resume_ai_base_url,
            openai_direct_api_key=self.resolved_openai_direct_api_key,
            openai_direct_base_url=self.openai_direct_base_url,
            openai_direct_model=self.openai_direct_model or self.agent_fallback_model,
            fireworks_api_key=self.fireworks_api_key,
            openrouter_api_key=self.openrouter_api_key,
        )

    @property
    def oidc_admin_group_names(self) -> set[str]:
        """Lower-cased configured OIDC admin group names."""
        values = [item.strip() for item in self.oidc_admin_groups.split(",")]
        return {value.casefold() for value in values if value}

    @property
    def discord_admin_role_names(self) -> set[str]:
        """Lower-cased configured Discord admin role names."""
        values = [item.strip() for item in self.discord_admin_roles.split(",")]
        return {value.casefold() for value in values if value}


settings = WorkerSettings()  # type: ignore[call-arg]
