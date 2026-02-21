"""Unit tests for skills extraction fallback heuristics."""

from five08.worker.crm.skills_extractor import SkillsExtractor


def test_heuristic_extract_includes_two_letter_skill_go() -> None:
    """Heuristic fallback should detect 2-letter skills in COMMON_SKILLS."""
    extractor = SkillsExtractor()
    extractor.client = None

    result = extractor.extract_skills("Built distributed services in Go and Docker")

    assert "go" in result.skills
    assert "docker" in result.skills
