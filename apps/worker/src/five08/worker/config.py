"""Configuration for webhook ingest and worker services."""

from urllib.parse import urlparse

from pydantic import field_validator, model_validator

from five08.settings import SharedSettings


class WorkerSettings(SharedSettings):
    """Worker-specific settings layered on top of shared stack settings."""

    worker_name: str = "integrations-worker"
    worker_queue_names: str = "jobs.default"
    worker_burst: bool = False

    espo_base_url: str
    espo_api_key: str
    crm_linkedin_field: str = "cLinkedInUrl"

    openai_api_key: str | None = None
    openai_base_url: str | None = None
    openai_model: str = "gpt-4o-mini"
    resume_ai_model: str = "gpt-4o-mini"
    resume_extractor_version: str = "v1"
    docuseal_member_agreement_template_id: int | None = None

    max_file_size_mb: int = 10
    allowed_file_types: str = "pdf,doc,docx,txt"
    resume_keywords: str = "resume,cv,curriculum"
    max_attachments_per_contact: int = 3
    crm_sync_enabled: bool = True
    crm_sync_interval_seconds: int = 900
    crm_sync_page_size: int = 200
    email_resume_intake_enabled: bool = False
    check_email_wait: int = 2
    email_username: str | None = None
    email_password: str | None = None
    imap_server: str | None = None
    email_resume_allowed_extensions: str = "pdf,doc,docx"
    email_resume_max_file_size_mb: int = 10
    email_require_sender_auth_headers: bool = True
    oidc_issuer_url: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_scope: str = "openid profile email groups"
    oidc_groups_claim: str = "groups"
    oidc_admin_groups: str = "Admin,Owner,Steering Committee"
    oidc_callback_path: str = "/auth/callback"
    oidc_redirect_base_url: str | None = None
    oidc_http_timeout_seconds: float = 8.0
    oidc_jwks_cache_seconds: int = 300
    auth_state_ttl_seconds: int = 600
    auth_session_ttl_seconds: int = 28800
    auth_session_cookie_name: str = "five08_session"
    auth_cookie_secure: bool = False
    auth_cookie_samesite: str = "lax"
    dashboard_default_path: str = "/dashboard"
    dashboard_public_base_url: str | None = None
    discord_bot_token: str | None = None
    discord_admin_guild_id: str | None = None
    discord_admin_roles: str = "Admin,Owner,Steering Committee"
    discord_api_timeout_seconds: float = 8.0
    discord_link_ttl_seconds: int = 600

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
    def validate_auth_cookie_samesite(self) -> "WorkerSettings":
        """Normalize and validate cookie SameSite policy."""
        normalized = self.auth_cookie_samesite.strip().lower()
        if normalized not in {"lax", "strict", "none"}:
            raise ValueError("AUTH_COOKIE_SAMESITE must be one of: lax, strict, none")
        self.auth_cookie_samesite = normalized
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
    def parsed_resume_keywords(self) -> set[str]:
        """Keywords used to identify resume-like attachments."""
        return {
            keyword.strip().lower()
            for keyword in self.resume_keywords.split(",")
            if keyword.strip()
        }

    @property
    def resolved_resume_ai_model(self) -> str:
        """Resolve provider-specific resume model name (e.g. OpenRouter prefixes)."""
        candidate = self.resume_ai_model.strip()
        if not candidate:
            candidate = self.openai_model.strip()
        if not candidate:
            return "gpt-4o-mini"

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
