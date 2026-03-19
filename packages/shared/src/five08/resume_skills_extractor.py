"""Skills extraction from resume text."""

import json
import logging
import re
from typing import Any

from five08.skills import (
    DISALLOWED_RESUME_SKILLS,
    normalize_skill,
    normalize_skill_payload,
)
from five08.resume_processing_models import ExtractedSkills, SkillAttributes

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

DISALLOWED_SKILLS = DISALLOWED_RESUME_SKILLS

DEFAULT_SKILL_STRENGTH = 3


class SkillsExtractor:
    """Extract skills with LLM when configured, fallback heuristics otherwise."""

    def __init__(
        self,
        *,
        model: str,
        openai_api_key: str | None,
        openai_base_url: str | None,
    ) -> None:
        self.model = model
        self.client: Any = None

        if openai_api_key and OpenAIClient is not None:
            self.client = OpenAIClient(
                api_key=openai_api_key,
                base_url=openai_base_url,
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
                            "Provide a strength from 1-5 when known, where 5 is strongest. "
                            "If uncertain, you may omit it or leave it blank. "
                            "Bias 3 for simple mentions, 4-5 for recent/current project usage, "
                            "and 1-2 for weak, outdated, or minimal exposure."
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
            if canonical in COMMON_SKILLS and canonical not in DISALLOWED_SKILLS:
                detected.add(canonical)
        for skill in COMMON_SKILLS:
            if " " not in skill or skill in DISALLOWED_SKILLS:
                continue
            if re.search(rf"\b{re.escape(skill)}\b", lowered):
                detected.add(skill)

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
            '{"skills": ["skill1", "skill2", "skill3 (4)"], '
            '"confidence": 0.8}\n'
            "Rules:\n"
            "- skills must be lowercase canonical names with minimal punctuation\n"
            '- prefer forms like "nodejs", "ab testing", "go to market"\n'
            "- optional strength may be included inline for a skill in parentheses, e.g. skill (4)\n"
            "- if strength is uncertain, omit the suffix or use an empty suffix, e.g. skill ()\n"
            "- strength is integer 1-5 (5 strongest), and should be assigned per above.\n"
            "- use 3 when a skill is simply mentioned without strong context\n"
            "- use 4 or 5 when usage is clearly current or recent in project work\n"
            "- use 2 for older, side, or weak mentions and 1 for very weak/outdated evidence\n"
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
        deduped_skills, normalized_attrs = normalize_skill_payload(
            skills_value=skills_value,
            skill_attrs_value=skill_attrs_value,
            disallowed=DISALLOWED_SKILLS,
        )
        ordered_skills = sorted(deduped_skills)
        attrs_map = {
            skill: SkillAttributes(strength=strength)
            for skill, strength in normalized_attrs.items()
        }

        return ExtractedSkills(
            skills=ordered_skills,
            skill_attrs=attrs_map,
            confidence=max(0.0, min(1.0, confidence)),
            source=source,
        )

    def _normalize_skill_name(self, value: str) -> str:
        return normalize_skill(value)

    def canonicalize_skill(self, value: str) -> str:
        """Public helper for consistent skill normalization across processors."""
        return self._normalize_skill_name(value)
