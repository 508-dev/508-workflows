"""Skills extraction from resume text."""

import json
import logging
import re
from typing import Any

from five08.skills import normalize_skill
from five08.worker.config import settings
from five08.worker.models import ExtractedSkills, SkillAttributes

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
    "node",
    "docker",
    "kubernetes",
    "amazon web services",
    "google cloud",
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
    "product management",
    "go to market",
    "ab testing",
    "search engine optimization",
    "search engine marketing",
    "customer relationship management",
    "google analytics",
    "product marketing",
    "content marketing",
}

DEFAULT_SKILL_STRENGTH = 3


class SkillsExtractor:
    """Extract skills with LLM when configured, fallback heuristics otherwise."""

    def __init__(self) -> None:
        self.model = settings.resolved_resume_ai_model
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
                            "You extract professional skills from resumes for a CRM. "
                            "Focus on white-collar skills for product development orgs: "
                            "engineering, product, data, design, growth, and marketing. "
                            "Return JSON only, no prose. "
                            "Normalize skills to concise canonical names, lowercase. "
                            "Provide a strength from 1-5 for each skill, where 5 is strongest."
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

            parsed = self._parse_llm_json(content)
            confidence = float(parsed.get("confidence", 0.7))
            return self._normalize_extracted_payload(
                skills_value=parsed.get("skills", []),
                skill_attrs_value=parsed.get("skill_attrs", {}),
                confidence=confidence,
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
            canonical = self._normalize_skill_name(token)
            if canonical in COMMON_SKILLS:
                detected.add(canonical)

        sorted_skills = sorted(detected)
        return ExtractedSkills(
            skills=sorted_skills,
            skill_attrs={
                skill: SkillAttributes(strength=DEFAULT_SKILL_STRENGTH)
                for skill in sorted_skills
            },
            confidence=0.45 if sorted_skills else 0.2,
            source="heuristic",
        )

    def _create_prompt(self, resume_text: str) -> str:
        """Prompt template for LLM extraction."""
        snippet = resume_text[:8000]
        return (
            "Analyze the resume and extract a concise skill list.\n"
            "Use white-collar/product-development relevance only: engineering, product, "
            "data, design, growth, and marketing.\n"
            "Exclude personal traits and vague soft skills unless role-critical.\n"
            "Return JSON with this exact schema:\n"
            '{"skills": ["skill1", "skill2"], '
            '"skill_attrs": {"skill1": {"strength": 4}}, '
            '"confidence": 0.8}\n'
            "Rules:\n"
            "- skills must be lowercase canonical names with minimal punctuation\n"
            '- prefer forms like "nodejs", "ab testing", "go to market"\n'
            "- skill_attrs keys must match skills\n"
            "- strength is integer 1-5 (5 strongest)\n"
            "- no extra keys\n\n"
            f"Resume:\n{snippet}"
        )

    def _parse_llm_json(self, content: str) -> dict[str, Any]:
        raw = content.strip()
        if raw.startswith("```"):
            lines = [line for line in raw.splitlines() if not line.startswith("```")]
            raw = "\n".join(lines).strip()

        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("skills extraction output was not a JSON object")
        return parsed

    def _normalize_extracted_payload(
        self,
        *,
        skills_value: Any,
        skill_attrs_value: Any,
        confidence: float,
        source: str,
    ) -> ExtractedSkills:
        raw_skills = skills_value if isinstance(skills_value, list) else []
        normalized_skills: list[str] = []
        for skill in raw_skills:
            canonical = self._normalize_skill_name(str(skill))
            if canonical:
                normalized_skills.append(canonical)

        attrs_map: dict[str, SkillAttributes] = {}
        if isinstance(skill_attrs_value, dict):
            for raw_name, raw_attr in skill_attrs_value.items():
                canonical = self._normalize_skill_name(str(raw_name))
                if not canonical:
                    continue
                attrs_map[canonical] = SkillAttributes(
                    strength=self._parse_strength(raw_attr)
                )

        # Ensure attrs exists for every skill and include attr-only entries in skill list.
        deduped_skills = sorted(set(normalized_skills) | set(attrs_map.keys()))
        for skill in deduped_skills:
            if skill not in attrs_map:
                attrs_map[skill] = SkillAttributes(strength=DEFAULT_SKILL_STRENGTH)

        return ExtractedSkills(
            skills=deduped_skills,
            skill_attrs=attrs_map,
            confidence=max(0.0, min(1.0, confidence)),
            source=source,
        )

    def _parse_strength(self, value: Any) -> int:
        raw: Any = value
        if isinstance(value, dict):
            raw = value.get("strength")
        try:
            numeric = int(float(raw))
        except Exception:
            numeric = DEFAULT_SKILL_STRENGTH
        return max(1, min(5, numeric))

    def _normalize_skill_name(self, value: str) -> str:
        return normalize_skill(value)

    def canonicalize_skill(self, value: str) -> str:
        """Public helper for consistent skill normalization across processors."""
        return self._normalize_skill_name(value)
