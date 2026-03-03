"""Unit tests for shared skill normalization helpers."""

from five08.skills import (
    DISALLOWED_RESUME_SKILLS,
    normalize_skill,
    normalize_skill_list,
    normalize_skill_payload,
)


def test_normalize_skill_prefers_discord_friendly_canonical_forms() -> None:
    """Canonical outputs should avoid punctuation-heavy variants and initials."""
    assert normalize_skill("Node.js") == "node"
    assert normalize_skill("A/B Testing") == "ab testing"
    assert normalize_skill("GTM") == "go to market"
    assert normalize_skill("CRM") == "customer relationship management"
    assert normalize_skill("SEO") == "search engine optimization"


def test_normalize_skill_list_dedupes_after_aliasing() -> None:
    """List normalization should dedupe across equivalent aliases."""
    normalized = normalize_skill_list(["node.js", "node", "A/B Testing", "ab testing"])

    assert normalized == ["node", "ab testing"]


def test_normalize_skill_payload_merges_inline_and_structured_strengths() -> None:
    """Payload normalization should dedupe aliases and keep strongest valid strengths."""
    skills, attrs = normalize_skill_payload(
        skills_value=["Node.js (2)", "node", "Python (4)", "python"],
        skill_attrs_value={"node": {"strength": 5}, "python": {"strength": 3}},
        disallowed=DISALLOWED_RESUME_SKILLS,
    )

    assert skills == ["node", "python"]
    assert attrs == {"node": 5, "python": 4}


def test_normalize_skill_payload_drops_disallowed_terms() -> None:
    """Disallowed generic terms should be filtered from both skills and attrs."""
    skills, attrs = normalize_skill_payload(
        skills_value=["Bug Tracking (3)", "Python"],
        skill_attrs_value={"bug tracking": {"strength": 5}, "python": {"strength": 4}},
        disallowed=DISALLOWED_RESUME_SKILLS,
    )

    assert skills == ["python"]
    assert attrs == {"python": 4}
