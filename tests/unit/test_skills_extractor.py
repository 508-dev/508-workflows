"""Unit tests for heuristic skills extraction."""

from five08.worker.crm.skills_extractor import SkillsExtractor


def test_heuristic_extract_includes_two_letter_skill_go() -> None:
    """Heuristic fallback should detect 2-letter skills in COMMON_SKILLS."""
    extractor = SkillsExtractor()
    extractor.client = None

    result = extractor.extract_skills("Built distributed services in Go and Docker")

    assert "go" in result.skills
    assert "docker" in result.skills


def test_heuristic_extractor_includes_two_letter_go_skill() -> None:
    """Heuristic extraction should include two-letter skill tokens like go."""
    extractor = SkillsExtractor()
    result = extractor._extract_skills_heuristic("Built services in Go and Python")

    assert "go" in result.skills
    assert "python" in result.skills
