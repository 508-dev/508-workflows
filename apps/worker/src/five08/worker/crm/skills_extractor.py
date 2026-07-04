"""Worker compatibility shim for shared skills extraction."""

from five08.resume_skills_extractor import COMMON_SKILLS as COMMON_SKILLS
from five08.resume_skills_extractor import (
    DEFAULT_SKILL_STRENGTH as DEFAULT_SKILL_STRENGTH,
)
from five08.resume_skills_extractor import DISALLOWED_SKILLS as DISALLOWED_SKILLS
from five08.resume_skills_extractor import SkillsExtractor as SharedSkillsExtractor
from five08.worker.config import settings

__all__ = [
    "COMMON_SKILLS",
    "DEFAULT_SKILL_STRENGTH",
    "DISALLOWED_SKILLS",
    "SkillsExtractor",
]


class SkillsExtractor(SharedSkillsExtractor):
    """Worker-specific wrapper bound to worker settings."""

    def __init__(self) -> None:
        super().__init__(
            model=settings.resolved_resume_ai_model,
            openai_api_key=settings.resolved_resume_ai_api_key,
            openai_base_url=settings.resolved_resume_ai_base_url,
            provider_attempts=settings.resolved_resume_ai_provider_attempts,
        )
