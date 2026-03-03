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


def test_normalize_extracted_payload_canonicalizes_and_validates_strength() -> None:
    """LLM payload normalization should map aliases and ignore out-of-range strengths."""
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
    assert "javascript" not in result.skill_attrs
    assert result.skill_attrs["product management"].strength == 4
    assert "ab testing" not in result.skill_attrs
    assert result.skill_attrs["node"].strength == 2
    assert result.skill_attrs["go to market"].strength == 4


def test_normalize_extracted_payload_parses_inline_strength_suffixes() -> None:
    """Inline strengths like `skill (4)` should be parsed when included in the skills list."""
    extractor = SkillsExtractor()

    result = extractor._normalize_extracted_payload(
        skills_value=[
            "Python (4)",
            "Code Review (5)",
            "Testing (3)",
            "Code Quality (2)",
            "UI/UX (4)",
            "TypeScript",
        ],
        skill_attrs_value=None,
        confidence=0.9,
        source="model",
    )

    assert result.skills == ["python", "typescript", "ui ux"]
    assert result.skill_attrs["python"].strength == 4
    assert "code review" not in result.skills
    assert "testing" not in result.skills
    assert "code quality" not in result.skills


def test_normalize_extracted_payload_keeps_ab_testing_and_ui_ux() -> None:
    """Keep technology-like and product-specific terms while dropping broad generic terms."""
    extractor = SkillsExtractor()

    result = extractor._normalize_extracted_payload(
        skills_value=["AB Testing", "UI/UX", "database optimization", "testing"],
        skill_attrs_value={
            "ab testing": {"strength": 5},
            "ui/ux": {"strength": 4},
            "testing": {"strength": 5},
            "database optimization": {"strength": 3},
        },
        confidence=0.85,
        source="model",
    )

    assert result.skills == [
        "ab testing",
        "database optimization",
        "ui ux",
    ]
    assert result.skill_attrs["ab testing"].strength == 5
    assert result.skill_attrs["ui ux"].strength == 4
    assert result.skill_attrs["database optimization"].strength == 3
    assert "testing" not in result.skills
    assert "testing" not in result.skill_attrs
