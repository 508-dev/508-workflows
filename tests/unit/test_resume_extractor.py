"""Unit tests for resume extractor helpers."""

from unittest.mock import Mock, patch

from five08.resume_extractor import _coerce_email_list
from five08.resume_extractor import _infer_timezone_from_location
from five08.resume_extractor import _normalize_name_part
from five08.resume_extractor import _normalize_website_url
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


def test_extract_website_links_includes_middle_scheme_url() -> None:
    """Middle-of-document URLs should be retained for markdown or scheme links."""
    links = ResumeProfileExtractor._extract_website_links(
        f"{'a' * 500}\nWebsite: https://michaelwu.dev\n{'b' * 500}"
    )

    assert "https://michaelwu.dev" in links


def test_extract_website_links_excludes_middle_company_url_without_context() -> None:
    """Middle 90% company-style links should be ignored unless context marks personal use."""
    links = ResumeProfileExtractor._extract_website_links(
        f"{'a' * 500}\nBuilt integrations with https://acme.com\n{'b' * 500}"
    )

    assert "https://acme.com" not in links


def test_normalize_website_url_removes_zero_width_characters() -> None:
    """Unicode formatting characters should not block valid URL normalization."""
    assert (
        _normalize_website_url("https://michaelwu.dev\u200b").casefold()
        == "https://michaelwu.dev"
    )


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
    assert result.linkedin_url is not None
    assert result.linkedin_url.casefold().endswith("linkedin.com/in/wumichaelm")
    assert all("node.js" not in link.casefold() for link in result.website_links)


def test_extract_social_links_only_keeps_direct_profile_urls() -> None:
    """Social URLs should be retained only when they point to a user profile."""
    extractor = ResumeProfileExtractor(api_key=None)
    result = extractor.extract(
        "https://www.youtube.com/@maimtime\n"
        "https://youtube.com/watch?v=dQw4w9WgXcQ\n"
        "https://x.com/asianluxetravel\n"
        "https://x.com/home\n"
        "https://www.linkedin.com/in/wumichaelm/\n"
        "https://www.linkedin.com/company/acme\n"
        "https://bsky.app/profile/michaelmwu.bsky.social\n"
        "https://bsky.app/about\n"
        "https://www.instagram.com/michaelmwu/\n"
        "https://www.instagram.com/p/abc123/\n"
        "https://fb.me/michaelmwu\n"
        "https://telegram.me/michaelmwu\n"
        "https://www.tiktok.com/@michaelmwu\n"
        "https://www.tiktok.com/discover/travel\n"
        "https://www.threads.net/@michaelmwu\n"
        "https://www.threads.net/t/CuAbc123\n"
        "https://www.pinterest.com/michaelmwu/\n"
        "https://www.pinterest.com/pin/12345/\n"
        "https://www.twitch.tv/michaelmwu\n"
        "https://www.twitch.tv/directory\n"
        "https://mastodon.social/@michaelmwu\n"
        "https://mastodon.social/@michaelmwu/112233\n"
        "https://gitlab.com/michaelmwu\n"
        "https://gitlab.com/explore\n"
        "https://stackoverflow.com/users/12345/michael-mwu\n"
        "https://stackoverflow.com/questions/1/example\n"
        "https://www.kaggle.com/michaelmwu\n"
        "https://www.kaggle.com/competitions\n"
        "https://huggingface.co/michaelmwu\n"
        "https://huggingface.co/models\n"
        "https://medium.com/@michaelmwu\n"
        "https://medium.com/tag/python\n"
        "https://michaelmwu.substack.com\n"
        "https://substack.com/home\n"
    )

    assert result.linkedin_url is not None
    assert result.linkedin_url.casefold().endswith("linkedin.com/in/wumichaelm")
    assert result.website_links == []
    assert result.social_links == [
        "https://youtube.com/@maimtime",
        "https://x.com/asianluxetravel",
        "https://bsky.app/profile/michaelmwu.bsky.social",
        "https://instagram.com/michaelmwu",
        "https://fb.me/michaelmwu",
        "https://telegram.me/michaelmwu",
        "https://tiktok.com/@michaelmwu",
        "https://threads.net/@michaelmwu",
        "https://pinterest.com/michaelmwu",
        "https://twitch.tv/michaelmwu",
        "https://mastodon.social/@michaelmwu",
        "https://gitlab.com/michaelmwu",
        "https://stackoverflow.com/users/12345/michael-mwu",
        "https://kaggle.com/michaelmwu",
        "https://huggingface.co/michaelmwu",
        "https://medium.com/@michaelmwu",
        "https://michaelmwu.substack.com",
    ]


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


def test_split_name_heuristic_parses_last_comma_first() -> None:
    """Comma-delimited names should parse as Last, First."""
    assert ResumeProfileExtractor._split_name_heuristically("Doe, Jane") == (
        "Jane",
        "Doe",
    )


def test_split_name_heuristic_preserves_multi_part_first_name_with_last_comma_first() -> (
    None
):
    """Multi-token first names after comma still keep the final last name."""
    assert ResumeProfileExtractor._split_name_heuristically("Doe, Jane Marie") == (
        "Jane",
        "Doe",
    )


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


def test_extract_llm_location_unknown_values_normalize_to_none() -> None:
    """Location placeholders from model output should not persist as literal values."""

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
                                            '"address_city": "unknown", '
                                            '"address_state": "N/A", '
                                            '"address_country": "none", '
                                            '"timezone": "unknown", '
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

    result = extractor.extract("Jane Doe\njane@example.com\nSenior Engineer\n")

    assert result.address_city is None
    assert result.address_state is None
    assert result.address_country is None
    assert result.timezone is None


def test_extract_llm_backfills_location_and_timezone_from_resume_text() -> None:
    """LLM mode should backfill explicit location fields and infer timezone."""

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
                                            '"address_city": null, '
                                            '"address_state": null, '
                                            '"address_country": null, '
                                            '"timezone": null, '
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
        "Jane Doe\n"
        "Address City: San Francisco\n"
        "Address State: California\n"
        "Address Country: United States\n"
        "jane@example.com\n"
    )

    assert result.address_city == "San Francisco"
    assert result.address_state == "California"
    assert result.address_country == "United States"
    assert result.timezone == "UTC-08:00"


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


def test_extract_name_skips_resume_heading_lines() -> None:
    """Heading labels like 'Resume:' should not be extracted as names."""
    extractor = ResumeProfileExtractor(api_key=None)

    result = extractor.extract(
        "Resume:\nJane Doe\njane@example.com\n8 years of software experience\n"
    )

    assert result.name == "Jane Doe"


def test_extract_name_skips_resume_heading_lines_with_spacing_variant() -> None:
    """Heading labels like 'Resume :' should not be extracted as names."""
    extractor = ResumeProfileExtractor(api_key=None)

    result = extractor.extract(
        "Resume :\nJane Doe\njane@example.com\n8 years of software experience\n"
    )

    assert result.name == "Jane Doe"


def test_extract_name_skips_heading_lines_with_extra_internal_spacing() -> None:
    """Heading labels with extra spacing should not be extracted as names."""
    extractor = ResumeProfileExtractor(api_key=None)

    result = extractor.extract(
        "Curriculum  Vitae\nJane Doe\njane@example.com\n8 years of software experience\n"
    )

    assert result.name == "Jane Doe"


def test_extract_name_case_normalizes_all_caps_name() -> None:
    """Uppercase resume names should be normalized before exporting."""
    extractor = ResumeProfileExtractor(api_key=None)

    result = extractor.extract("WILL GUTIERREZ\nSoftware Engineer\nwill@example.com\n")

    assert result.name == "Will Gutierrez"
    assert result.first_name == "Will"
    assert result.last_name == "Gutierrez"


def test_infer_seniority_regex_handles_scale_keywords() -> None:
    """Scale/impact keywords alone (no cross-team scope) should yield senior, not staff."""
    level = ResumeProfileExtractor._infer_seniority_from_resume(
        "10 years of software experience. Managed teams at a Series B startup."
    )

    assert level == "senior"


def test_infer_seniority_regex_cross_team_scope_yields_staff() -> None:
    """Cross-team scope signals with 8+ years should yield staff."""
    level = ResumeProfileExtractor._infer_seniority_from_resume(
        "10 years of software experience. Led cross-functional initiatives across teams."
    )

    assert level == "staff"


def test_infer_seniority_regex_title_first_senior_overrides_low_years() -> None:
    """Explicit senior title should override low years-of-experience count."""
    level = ResumeProfileExtractor._infer_seniority_from_resume(
        "3 years of experience. Senior Engineer at Acme Corp."
    )

    assert level == "senior"


def test_infer_seniority_regex_matches_senior_software_engineer_variant() -> None:
    """Title variants like 'Senior Software Engineer' should map to senior."""
    level = ResumeProfileExtractor._infer_seniority_from_resume(
        "Senior Software Engineer at Acme"
    )

    assert level == "senior"


def test_infer_seniority_regex_matches_engineer_ii_as_midlevel() -> None:
    """Engineer II titles should map to midlevel even without years text."""
    level = ResumeProfileExtractor._infer_seniority_from_resume(
        "Software Engineer II, Platform"
    )

    assert level == "midlevel"


def test_infer_seniority_regex_matches_principal_software_engineer_variant() -> None:
    """Principal Software Engineer variants should map to staff."""
    level = ResumeProfileExtractor._infer_seniority_from_resume(
        "Principal Software Engineer"
    )

    assert level == "staff"


def test_infer_seniority_regex_defaults_generic_engineer_title_to_midlevel() -> None:
    """Generic engineer/developer titles without qualifiers should map to midlevel."""
    level = ResumeProfileExtractor._infer_seniority_from_resume(
        "Software Engineer at Acme Corp"
    )

    assert level == "midlevel"


def test_infer_seniority_regex_does_not_match_larger_numeric_prefixes() -> None:
    """Standalone 500/1000 tokens should not trigger from larger numbers like 5000."""
    level = ResumeProfileExtractor._infer_seniority_from_resume(
        "10 years of software experience. Built tooling used across 5000 projects."
    )

    assert level == "senior"


def test_extract_roles_falls_back_to_title_inference_developer() -> None:
    """Role extraction should infer developer from common engineering titles."""
    roles = ResumeProfileExtractor._extract_roles(
        "Experience\nSenior Software Engineer at Acme\nBuilt APIs"
    )

    assert "developer" in roles


def test_extract_roles_falls_back_to_title_inference_non_developer() -> None:
    """Role extraction should infer non-developer roles from title keywords."""
    roles = ResumeProfileExtractor._extract_roles(
        "Work History\nTechnical Program Manager at Example Inc"
    )

    assert "program manager" in roles


def test_normalize_name_part_preserves_non_uppercase_casing() -> None:
    """Only all-caps names should be title-cased; mixed-case names stay unchanged."""
    assert _normalize_name_part("McDonald") == "McDonald"
    assert _normalize_name_part("mcdonald") == "mcdonald"


def test_infer_timezone_known_country() -> None:
    assert _infer_timezone_from_location(country="India") == "UTC+05:30"


def test_infer_timezone_unknown_country_returns_none() -> None:
    assert _infer_timezone_from_location(country="Atlantis") is None


def test_infer_timezone_none_country_returns_none() -> None:
    assert _infer_timezone_from_location(country=None) is None


def test_infer_timezone_case_insensitive() -> None:
    assert _infer_timezone_from_location(country="INDIA") == "UTC+05:30"
    assert _infer_timezone_from_location(country="india") == "UTC+05:30"


def test_infer_timezone_ambiguous_country_returns_none() -> None:
    assert _infer_timezone_from_location(country="Brazil") is None
    assert _infer_timezone_from_location(country="Australia") is None
    assert _infer_timezone_from_location(country="Russia") is None


def test_infer_timezone_city_takes_precedence_over_country() -> None:
    # San Francisco (UTC-8) in ambiguous country USA should still resolve.
    assert (
        _infer_timezone_from_location(country="United States", city="San Francisco")
        == "UTC-08:00"
    )


def test_infer_timezone_city_resolves_ambiguous_country() -> None:
    assert (
        _infer_timezone_from_location(country="Australia", city="Sydney") == "UTC+10:00"
    )
    assert (
        _infer_timezone_from_location(country="Australia", city="Perth") == "UTC+08:00"
    )


def test_infer_timezone_unknown_city_falls_back_to_country() -> None:
    assert (
        _infer_timezone_from_location(country="Japan", city="Unknown City")
        == "UTC+09:00"
    )
