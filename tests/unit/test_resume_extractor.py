"""Unit tests for resume extractor helpers."""

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
