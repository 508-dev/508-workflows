"""Unit tests for heuristic skills extraction."""

from five08.worker.crm.skills_extractor import SkillsExtractor


def test_heuristic_extract_includes_two_letter_skill_go() -> None:
    """Heuristic fallback should detect 2-letter skills in COMMON_SKILLS."""
    extractor = SkillsExtractor()
    extractor.client = None

    result = extractor.extract_skills("Built distributed services in Go and Docker")

    assert "go" in result.skills
    assert "docker" in result.skills
    assert result.skill_attrs["go"].strength == 3
    assert result.skill_attrs["docker"].strength == 3


def test_heuristic_extractor_includes_two_letter_go_skill() -> None:
    """Heuristic extraction should include two-letter skill tokens like go."""
    extractor = SkillsExtractor()
    result = extractor._extract_skills_heuristic("Built services in Go and Python")

    assert "go" in result.skills
    assert "python" in result.skills


def test_normalize_extracted_payload_canonicalizes_and_clamps_strength() -> None:
    """LLM payload normalization should map aliases and clamp strengths to 1-5."""
    extractor = SkillsExtractor()

    result = extractor._normalize_extracted_payload(
        skills_value=["JS", " PM ", "A/B Testing", "Node.js", "Go-To-Market"],
        skill_attrs_value={
            "javascript": {"strength": 9},
            "product management": {"strength": 4},
            "a/b testing": {"strength": 0},
            "node.js": {"strength": 2},
            "go-to-market": {"strength": 4},
        },
        confidence=0.8,
        source="model",
    )

    assert result.skills == [
        "ab testing",
        "go to market",
        "javascript",
        "node",
        "product management",
    ]
    assert result.skill_attrs["javascript"].strength == 5
    assert result.skill_attrs["product management"].strength == 4
    assert result.skill_attrs["ab testing"].strength == 1
    assert result.skill_attrs["node"].strength == 2
    assert result.skill_attrs["go to market"].strength == 4
