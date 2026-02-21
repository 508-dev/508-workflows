"""Configuration for webhook ingest and worker services."""

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

    max_file_size_mb: int = 10
    allowed_file_types: str = "pdf,doc,docx,txt"
    resume_keywords: str = "resume,cv,curriculum"
    max_attachments_per_contact: int = 3

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


settings = WorkerSettings()  # type: ignore[call-arg]
