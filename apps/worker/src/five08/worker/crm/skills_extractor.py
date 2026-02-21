"""Skills extraction from resume text."""

import json
import logging
import re
from typing import Any

from five08.worker.config import settings
from five08.worker.models import ExtractedSkills

logger = logging.getLogger(__name__)

try:  # pragma: no cover - import success depends on installed dependencies
    from openai import OpenAI as OpenAIClient
except Exception:  # pragma: no cover
    OpenAIClient = None  # type: ignore[misc,assignment]

COMMON_SKILLS = {
    "python",
    "javascript",
    "typescript",
    "java",
    "go",
    "rust",
    "docker",
    "kubernetes",
    "aws",
    "gcp",
    "azure",
    "postgresql",
    "mysql",
    "redis",
    "react",
    "django",
    "flask",
    "fastapi",
    "git",
    "linux",
}


class SkillsExtractor:
    """Extract skills with LLM when configured, fallback heuristics otherwise."""

    def __init__(self) -> None:
        self.model = settings.openai_model
        self.client: Any = None

        if settings.openai_api_key and OpenAIClient is not None:
            self.client = OpenAIClient(
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url,
            )

    def extract_skills(self, resume_text: str) -> ExtractedSkills:
        """Extract skills from resume text."""
        if self.client is None:
            return self._extract_skills_heuristic(resume_text)

        prompt = self._create_prompt(resume_text)
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Extract professional and technical skills from resume text. "
                            "Return only JSON."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=1200,
            )
            content = response.choices[0].message.content
            if not content:
                raise ValueError("LLM returned empty content")

            parsed = json.loads(content)
            skills = parsed.get("skills", [])
            confidence = float(parsed.get("confidence", 0.7))

            if not isinstance(skills, list):
                raise ValueError("skills must be a list")

            normalized = [skill.strip() for skill in skills if str(skill).strip()]
            return ExtractedSkills(
                skills=sorted(set(normalized)),
                confidence=max(0.0, min(1.0, confidence)),
                source=self.model,
            )
        except Exception as exc:
            logger.warning("LLM skills extraction failed, using fallback: %s", exc)
            return self._extract_skills_heuristic(resume_text)

    def _extract_skills_heuristic(self, resume_text: str) -> ExtractedSkills:
        """Simple keyword and token-based extraction fallback."""
        lowered = resume_text.lower()
        token_matches = re.findall(r"\b[a-z][a-z0-9+#\-.]{1,24}\b", lowered)
        detected: set[str] = set()
        for token in token_matches:
            if token in COMMON_SKILLS:
                detected.add(token)

        return ExtractedSkills(
            skills=sorted(detected),
            confidence=0.45 if detected else 0.2,
            source="heuristic",
        )

    def _create_prompt(self, resume_text: str) -> str:
        """Prompt template for LLM extraction."""
        snippet = resume_text[:8000]
        return (
            "Analyze the resume and extract a concise skill list.\n"
            "Return JSON with this exact schema:\n"
            '{"skills": ["skill1", "skill2"], "confidence": 0.8}\n\n'
            f"Resume:\n{snippet}"
        )
