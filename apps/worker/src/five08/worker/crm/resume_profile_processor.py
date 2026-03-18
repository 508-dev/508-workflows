"""Worker compatibility shim for shared resume processing."""

from five08.resume_profile_processor import ResumeProcessorConfig
from five08.resume_profile_processor import (
    ResumeProfileProcessor as SharedResumeProfileProcessor,
)
from five08.worker.config import settings


class ResumeProfileProcessor(SharedResumeProfileProcessor):
    """Worker-specific wrapper bound to worker settings."""

    def __init__(self) -> None:
        super().__init__(ResumeProcessorConfig.from_settings(settings))
