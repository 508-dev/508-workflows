"""Configuration for webhook ingest and worker services."""

from urllib.parse import urlparse

from pydantic import Field, PrivateAttr, field_validator, model_validator

from five08.settings import SharedSettings


class WorkerSettings(SharedSettings):
    """Worker-specific settings layered on top of shared stack settings."""

    _crm_linkedin_field: str = PrivateAttr(default="cLinkedIn")
    _crm_intake_completed_field: str = PrivateAttr(default="")

    worker_name: str = "worker"
    worker_queue_names: str = "jobs.default"
    worker_burst: bool = False

    espo_base_url: str
    espo_api_key: str
    google_forms_allowed_form_ids: str = ""

    openai_api_key: str | None = None
    openai_base_url: str | None = None
    openai_model: str = "gpt-5-mini"
    resume_ai_model: str = "gpt-5-mini"
    resume_extractor_version: str = "v1"
    docuseal_member_agreement_template_id: int | None = None

    max_file_size_mb: int = 10
    allowed_file_types: str = "pdf,doc,docx,txt"
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
    email_resume_allowed_extensions: str = "pdf,doc,docx"
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
    dashboard_default_path: str = "/dashboard"
    dashboard_public_base_url: str | None = None
    discord_bot_token: str | None = None
    discord_admin_guild_id: str | None = None
    discord_admin_roles: str = "Admin,Owner"
    discord_api_timeout_seconds: float = 8.0
    discord_link_ttl_seconds: int = 600
    discord_link_require_oidc_identity_checks: bool = True

    @property
    def google_forms_allowed_form_ids_set(self) -> set[str]:
        """Allowed Google Forms IDs used by intake webhook validation."""
        return {
            form_id.strip()
            for form_id in self.google_forms_allowed_form_ids.split(",")
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
            return int(normalized)
        raise TypeError("DOCUSEAL_MEMBER_AGREEMENT_TEMPLATE_ID must be an integer")

    @property
    def allowed_file_extensions(self) -> set[str]:
        """Allowed resume file extensions."""
        return {ext.strip().lower() for ext in self.allowed_file_types.split(",")}

    @property
    def crm_linkedin_field(self) -> str:
        """Resume/profile sync always writes LinkedIn URLs to the canonical CRM field."""
        return self._crm_linkedin_field

    @crm_linkedin_field.setter
    def crm_linkedin_field(self, value: str) -> None:
        """Allow controlled runtime overrides without reintroducing env loading."""
        self._crm_linkedin_field = value

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
    def auth_session_ttl_seconds(self) -> int:
        """Dashboard sessions expire after one workday."""
        return 28800

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
            return "gpt-5-mini"

        # Keep explicit provider prefixes intact.
        if "/" in candidate:
            return candidate

        base_url = (self.openai_base_url or "").strip()
        if not base_url:
            return candidate

        parsed = urlparse(base_url)
        host = (parsed.netloc or parsed.path).split("/")[0].split(":")[0].lower()
        if host.endswith("openrouter.ai"):
            return f"openai/{candidate}"
        return candidate

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
