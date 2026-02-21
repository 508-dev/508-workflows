"""Configuration for webhook ingest and worker services."""

from urllib.parse import urlparse

from pydantic import model_validator

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


settings = WorkerSettings()  # type: ignore[call-arg]
