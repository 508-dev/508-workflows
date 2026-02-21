"""Unit tests for shared skill normalization helpers."""

from five08.skills import normalize_skill, normalize_skill_list


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
