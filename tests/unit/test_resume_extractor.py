"""Unit tests for resume extractor helpers."""

from unittest.mock import Mock, patch

from five08.resume_extractor import _coerce_email_list
from five08.resume_extractor import ResumeProfileExtractor


def test_coerce_email_list_skips_non_string_entries() -> None:
    """Non-string iterables should be ignored while extracting emails."""
    assert _coerce_email_list(
        [
            "lead@example.com",
            None,
            123,
            {"email": "bad@example.com"},
            ["nested@example.com"],
        ]
    ) == ["lead@example.com"]


def test_coerce_email_list_extracts_emails_from_string_items() -> None:
    """String list items should be parsed for embedded email values."""
    assert _coerce_email_list(
        ["Lead <lead@example.com>", "bad", b"secondary@example.com, alt@example.com"]
    ) == ["lead@example.com", "secondary@example.com", "alt@example.com"]


def test_extract_website_links_includes_scheme_less_domains() -> None:
    """Website extraction should normalize bare domains into valid URLs."""
    links = ResumeProfileExtractor._extract_website_links(
        "Portfolio: michaelwu.dev and www.example.org/about me@example.com"
    )

    assert "https://michaelwu.dev" in links
    assert "https://example.org/about" in links
    assert "https://example.com" not in links


def test_extract_website_links_accepts_uppercase_scheme() -> None:
    """Uppercase HTTP(S) schemes should be treated as valid URLs."""
    links = ResumeProfileExtractor._extract_website_links(
        "Portfolio: HTTPS://Example.com/path"
    )

    assert any(link.casefold() == "https://example.com/path" for link in links)


def test_extract_profile_links_route_social_urls_away_from_website() -> None:
    """Disallowed social/technical URLs should not be stored as personal websites."""
    extractor = ResumeProfileExtractor(api_key=None)
    result = extractor.extract(
        "GitHub: https://github.com/wumichaelm\n"
        "LinkedIn: linkedin.com/in/wumichaelm\n"
        "Personal blog: michaelwu.dev\n"
        "Also: Node.js\n"
    )

    assert result.website_links == ["https://michaelwu.dev"]
    assert result.github_username == "wumichaelm"
    assert result.linkedin_url == "https://linkedin.com/in/wumichaelm"
    assert all("node.js" not in link.casefold() for link in result.website_links)


def test_split_name_prefers_llm_output() -> None:
    """Split-name should prefer LLM output when it is available."""
    extractor = ResumeProfileExtractor(api_key="test-key")
    extractor.client = Mock()

    with patch.object(
        extractor,
        "_split_name_with_llm",
        return_value=("Ada", "Lovelace"),
    ) as mock_llm_split:
        first_name, last_name = extractor.split_name("Ada Lovelace")

    assert first_name == "Ada"
    assert last_name == "Lovelace"
    mock_llm_split.assert_called_once_with("Ada Lovelace")


def test_split_name_falls_back_to_heuristic_without_name_hints() -> None:
    """Split-name should still split names using heuristics when LLM fails."""
    extractor = ResumeProfileExtractor(api_key="test-key")
    extractor.client = Mock()

    with patch.object(extractor, "_split_name_with_llm", side_effect=RuntimeError()):
        first_name, last_name = extractor.split_name("Dr. Grace Hopper")

    assert first_name == "Grace"
    assert last_name == "Hopper"


def test_split_name_single_token_returns_unknown_last_name() -> None:
    """Single token names should use a placeholder last name."""
    extractor = ResumeProfileExtractor(api_key=None)

    first_name, last_name = extractor.split_name("Cher")

    assert first_name == "Cher"
    assert last_name == "Unknown"


def test_split_name_ignores_numeric_last_token() -> None:
    """Fallback names should avoid non-alpha trailing tokens as last names."""
    extractor = ResumeProfileExtractor(api_key=None)

    first_name, last_name = extractor.split_name("Person 508")

    assert first_name == "Person"
    assert last_name == "Unknown"


def test_split_name_treats_placeholder_hints_as_missing() -> None:
    """Placeholder first/last hints should defer to parsed name when available."""
    extractor = ResumeProfileExtractor(api_key=None)

    first_name, last_name = extractor.split_name(
        "Ada Lovelace",
        first_name_hint="Unknown",
        last_name_hint="N/A",
    )

    assert first_name == "Ada"
    assert last_name == "Lovelace"


def test_split_name_missing_full_name_uses_default_fallbacks() -> None:
    """No full name should use the default candidate fallback values."""
    extractor = ResumeProfileExtractor(api_key=None)

    first_name, last_name = extractor.split_name(None)

    assert first_name == "Resume"
    assert last_name == "Candidate"


def test_split_name_with_llm_partial_last_name_falls_back_to_heuristics() -> None:
    """Partial LLM output should be completed by heuristic parsing."""

    class _FakeChatCompletions:
        @staticmethod
        def create(**_: object) -> object:
            return type(
                "Response",
                (),
                {
                    "choices": [
                        type(
                            "Choice",
                            (),
                            {
                                "message": type(
                                    "Message",
                                    (),
                                    {
                                        "content": '{"firstName": null, "lastName": "Lovelace"}'
                                    },
                                )()
                            },
                        )()
                    ]
                },
            )()

    extractor = ResumeProfileExtractor(api_key="test-key")
    extractor.client = type(
        "Client",
        (),
        {"chat": type("Chat", (), {"completions": _FakeChatCompletions()})()},
    )()
    extractor.model = "fake-model"

    first_name, last_name = extractor._split_name_with_llm("Ada Lovelace")

    assert first_name == "Ada"
    assert last_name == "Lovelace"


def test_extract_profile_backfills_website_and_social_urls_from_markdown() -> None:
    """Markdown links should be split by website vs social and routed correctly."""

    class _FakeChatCompletions:
        @staticmethod
        def create(**_: object) -> object:
            return type(
                "Response",
                (),
                {
                    "choices": [
                        type(
                            "Choice",
                            (),
                            {
                                "message": type(
                                    "Message",
                                    (),
                                    {
                                        "content": (
                                            '{"name": null, "email": null, '
                                            '"github_username": null, '
                                            '"linkedin_url": null, '
                                            '"website_url_candidates": ['
                                            '{"url": "https://michaelwu.dev", "kind": "personal_website", "confidence": 0.96, "reason": "explicit portfolio"}, '
                                            '{"url": "https://github.com/wumichaelm", "kind": "social_profile", "confidence": 0.99, "reason": "explicit github"}, '
                                            '{"url": "linkedin.com/in/wumichaelm", "kind": "social_profile", "confidence": 0.99, "reason": "explicit linkedin"}, '
                                            '{"url": "https://x.com/wumwu", "kind": "social_profile", "confidence": 0.93, "reason": "explicit twitter replacement"} '
                                            "], "
                                            '"website_links": null, '
                                            '"social_links": [], '
                                            '"phone": null, "skills": [], '
                                            '"skill_attrs": null, "confidence": 0.8}'
                                        )
                                    },
                                )()
                            },
                        )()
                    ]
                },
            )()

    extractor = ResumeProfileExtractor(api_key="test-key")
    extractor.client = type(
        "Client",
        (),
        {"chat": type("Chat", (), {"completions": _FakeChatCompletions()})()},
    )()
    extractor.model = "fake-model"

    result = extractor.extract(
        "Portfolio: [Personal Site](https://michaelwu.dev)\n"
        "GitHub: [repo](https://github.com/wumichaelm)\n"
        "LinkedIn: [my profile](linkedin.com/in/wumichaelm)\n"
        "Follow: [social](https://x.com/wumwu)\n"
    )

    assert result.website_links == ["https://michaelwu.dev"]
    assert result.github_username == "wumichaelm"
    assert result.linkedin_url == "https://linkedin.com/in/wumichaelm"
    assert result.social_links == ["https://x.com/wumwu"]


def test_extract_backfills_linkedin_and_website_when_llm_omits_them() -> None:
    """LLM mode should backfill missing links from the resume text heuristics."""

    class _FakeChatCompletions:
        @staticmethod
        def create(**_: object) -> object:
            return type(
                "Response",
                (),
                {
                    "choices": [
                        type(
                            "Choice",
                            (),
                            {
                                "message": type(
                                    "Message",
                                    (),
                                    {
                                        "content": (
                                            '{"name": null, "email": null, '
                                            '"github_username": null, '
                                            '"linkedin_url": null, '
                                            '"website_url_candidates": [], '
                                            '"website_links": [], '
                                            '"social_links": [], '
                                            '"phone": null, "skills": [], '
                                            '"skill_attrs": null, "confidence": 0.8}'
                                        )
                                    },
                                )()
                            },
                        )()
                    ]
                },
            )()

    extractor = ResumeProfileExtractor(api_key="test-key")
    extractor.client = type(
        "Client",
        (),
        {"chat": type("Chat", (), {"completions": _FakeChatCompletions()})()},
    )()
    extractor.model = "fake-model"

    result = extractor.extract(
        "LinkedIn: linkedin.com/in/wumichaelm\nWebsite: michaelwu.dev"
    )

    assert result.linkedin_url == "https://linkedin.com/in/wumichaelm"
    assert "https://michaelwu.dev" in result.website_links


def test_extract_ignores_low_confidence_website_candidate() -> None:
    """Personal website candidates should pass a confidence threshold."""

    class _FakeChatCompletions:
        @staticmethod
        def create(**_: object) -> object:
            return type(
                "Response",
                (),
                {
                    "choices": [
                        type(
                            "Choice",
                            (),
                            {
                                "message": type(
                                    "Message",
                                    (),
                                    {
                                        "content": (
                                            '{"name": null, "email": null, '
                                            '"github_username": null, '
                                            '"linkedin_url": null, '
                                            '"website_url_candidates": ['
                                            '{"url": "https://example-personal-stub.io", '
                                            '"kind": "personal_website", "confidence": 0.60, '
                                            '"reason": "low confidence"} '
                                            "], "
                                            '"website_links": [], '
                                            '"social_links": [], '
                                            '"phone": null, "skills": [], '
                                            '"skill_attrs": null, "confidence": 0.8}'
                                        )
                                    },
                                )()
                            },
                        )()
                    ]
                },
            )()

    extractor = ResumeProfileExtractor(api_key="test-key")
    extractor.client = type(
        "Client",
        (),
        {"chat": type("Chat", (), {"completions": _FakeChatCompletions()})()},
    )()
    extractor.model = "fake-model"

    result = extractor.extract("Name: Michael Wu")

    assert result.website_links == []
    assert result.social_links == []


def test_extract_linkedin_url_supports_hyphenated_slugs() -> None:
    """LinkedIn profile extraction should include hyphenated slug segments."""
    url = ResumeProfileExtractor._extract_linkedin_url(
        "Profile: https://linkedin.com/in/wu-michael-dev/"
    )

    assert url == "https://linkedin.com/in/wu-michael-dev"


def test_extract_dedupes_linkedin_profile_variants_from_website_links() -> None:
    """Equivalent LinkedIn profile URLs should not remain in website links."""
    extractor = ResumeProfileExtractor(api_key=None)
    result = extractor.extract(
        "LinkedIn: linkedin.com/in/wumichaelm\n"
        "https://www.linkedin.com/in/wumichaelm?trk=foo\n"
        "Portfolio: https://michaelwu.dev"
    )

    assert result.linkedin_url == "https://linkedin.com/in/wumichaelm"
    assert all(
        "linkedin.com/in/wumichaelm" not in link.casefold()
        for link in result.website_links
    )
    assert "https://michaelwu.dev" in [link.casefold() for link in result.website_links]
