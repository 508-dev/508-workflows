"""Unit tests for job_match extraction helpers."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from five08.job_match import (
    DISCORD_LOCALITY_ROLE_NAMES,
    DISCORD_ROLES_EXCLUDE_FROM_SYNC,
    DISCORD_ROLES_NEVER_SUGGEST,
    DISCORD_SKILL_ROLE_NAMES,
    _build_prompt,
    _coerce_str_list,
    _parse_llm_response,
    _regex_hints,
    extract_job_requirements,
    suggest_locality_discord_roles,
    suggest_technical_discord_roles,
)


# ---------------------------------------------------------------------------
# _regex_hints
# ---------------------------------------------------------------------------


def test_regex_hints_detects_us_only_plain() -> None:
    hints = _regex_hints("This role is US only and full-time.")
    assert hints.get("us_only_detected") is True


def test_regex_hints_detects_authorized_to_work() -> None:
    hints = _regex_hints("Candidates must be authorized to work in the US.")
    assert hints.get("us_only_detected") is True


def test_regex_hints_no_us_only() -> None:
    hints = _regex_hints("Remote role, open to all locations.")
    assert "us_only_detected" not in hints


def test_regex_hints_detects_senior_seniority() -> None:
    hints = _regex_hints("We are looking for a senior engineer.")
    assert hints.get("seniority_hint") == "senior"


def test_regex_hints_detects_entry_level_seniority() -> None:
    hints = _regex_hints("Great opportunity for entry-level developers.")
    assert hints.get("seniority_hint") == "junior"


def test_regex_hints_detects_mid_level_seniority() -> None:
    hints = _regex_hints("Ideal candidate is mid-level.")
    assert hints.get("seniority_hint") == "midlevel"


def test_regex_hints_no_seniority() -> None:
    hints = _regex_hints("Software engineer role, all levels welcome.")
    assert hints.get("seniority_hint") is None


# ---------------------------------------------------------------------------
# _parse_llm_response
# ---------------------------------------------------------------------------


def test_parse_llm_response_plain_json() -> None:
    raw = '{"required_skills": ["python"]}'
    data = _parse_llm_response(raw)
    assert data["required_skills"] == ["python"]


def test_parse_llm_response_fenced_json() -> None:
    raw = '```json\n{"required_skills": ["go"]}\n```'
    data = _parse_llm_response(raw)
    assert data["required_skills"] == ["go"]


def test_parse_llm_response_fenced_no_lang() -> None:
    raw = '```\n{"title": "SWE"}\n```'
    data = _parse_llm_response(raw)
    assert data["title"] == "SWE"


def test_parse_llm_response_invalid_json_raises() -> None:
    with pytest.raises((json.JSONDecodeError, ValueError)):
        _parse_llm_response("not json at all")


# ---------------------------------------------------------------------------
# _coerce_str_list
# ---------------------------------------------------------------------------


def test_coerce_str_list_filters_non_strings() -> None:
    assert _coerce_str_list(["python", None, 123, "react"]) == ["python", "react"]


def test_coerce_str_list_non_list_returns_empty() -> None:
    assert _coerce_str_list(None) == []
    assert _coerce_str_list("python") == []


# ---------------------------------------------------------------------------
# extract_job_requirements
# ---------------------------------------------------------------------------


def _make_openai_response(payload: dict) -> MagicMock:
    """Build a minimal mock that looks like a chat.completions response."""
    choice = MagicMock()
    choice.message.content = json.dumps(payload)
    choice.finish_reason = "stop"
    response = MagicMock()
    response.choices = [choice]
    return response


def test_extract_raises_when_api_key_missing() -> None:
    with pytest.raises(RuntimeError, match="OpenAI API key"):
        extract_job_requirements("some job", api_key=None)


def test_extract_raises_and_logs_webhook_when_api_key_missing() -> None:
    sent: list[str] = []

    with patch("five08.job_match.DiscordWebhookLogger") as mock_logger_cls:
        mock_logger = MagicMock()
        mock_logger_cls.return_value = mock_logger
        mock_logger.send.side_effect = lambda **kwargs: sent.append(
            kwargs.get("content", "")
        )

        with pytest.raises(RuntimeError):
            extract_job_requirements("job", api_key=None, webhook_url="http://hook")

    mock_logger.send.assert_called_once()


def test_extract_normalizes_required_skills() -> None:
    payload = {
        "required_skills": ["Python", "REACT", "  django  "],
        "preferred_skills": [],
        "seniority": "senior",
        "location_type": "remote_any",
        "preferred_timezones": [],
        "raw_location_text": None,
        "title": "Backend Engineer",
    }
    with patch("openai.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_openai_response(
            payload
        )

        result = extract_job_requirements(
            "We need Python, React, Django. Senior role.",
            api_key="test-key",
        )

    # Skills should be normalized/lowercased
    assert "python" in result.required_skills
    assert result.seniority == "senior"
    assert result.title == "Backend Engineer"


def test_extract_us_only_regex_overrides_remote_any() -> None:
    """Regex US-only detection must override the LLM returning remote_any."""
    payload = {
        "required_skills": ["python"],
        "preferred_skills": [],
        "seniority": None,
        "location_type": "remote_any",  # LLM got it wrong
        "preferred_timezones": [],
        "raw_location_text": "US only",
        "title": None,
    }
    with patch("openai.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_openai_response(
            payload
        )

        result = extract_job_requirements(
            "Looking for engineers, US only.",
            api_key="test-key",
        )

    assert result.location_type == "us_only"


def test_extract_normalizes_location_type_case() -> None:
    """LLM returning 'US_ONLY' (uppercase) should still be accepted."""
    payload = {
        "required_skills": ["python"],
        "preferred_skills": [],
        "seniority": None,
        "location_type": "US_ONLY",
        "preferred_timezones": [],
        "raw_location_text": None,
        "title": None,
    }
    with patch("openai.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_openai_response(
            payload
        )

        result = extract_job_requirements("python role", api_key="test-key")

    assert result.location_type == "us_only"


def test_extract_raises_on_empty_choices() -> None:
    response = MagicMock()
    response.choices = []

    with patch("openai.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = response

        with pytest.raises(RuntimeError, match="empty or missing"):
            extract_job_requirements("python role", api_key="test-key")


def test_extract_raises_on_invalid_json_response() -> None:
    choice = MagicMock()
    choice.message.content = "this is not json"
    choice.finish_reason = "stop"
    response = MagicMock()
    response.choices = [choice]

    with patch("openai.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = response

        with pytest.raises(RuntimeError, match="unparseable"):
            extract_job_requirements("python role", api_key="test-key")


def test_build_prompt_includes_us_only_hint() -> None:
    prompt = _build_prompt("text", {"us_only_detected": True})
    assert "US-only location restriction" in prompt


def test_build_prompt_includes_seniority_hint() -> None:
    prompt = _build_prompt("text", {"seniority_hint": "senior"})
    assert "senior" in prompt


def test_build_prompt_no_hints_has_no_note_lines() -> None:
    prompt = _build_prompt("text", {})
    assert "Note:" not in prompt


def test_build_prompt_includes_all_discord_role_names() -> None:
    prompt = _build_prompt("text", {})
    for role in DISCORD_SKILL_ROLE_NAMES:
        assert role in prompt, f"Expected Discord role '{role}' in prompt"


def test_build_prompt_excludes_soft_skill_instruction() -> None:
    prompt = _build_prompt("text", {})
    assert "EXCLUDE soft skills" in prompt


# ---------------------------------------------------------------------------
# extract_job_requirements — discord_role_types
# ---------------------------------------------------------------------------


def _make_base_payload(**overrides: object) -> dict:
    base = {
        "required_skills": ["python"],
        "preferred_skills": [],
        "discord_role_types": [],
        "seniority": None,
        "location_type": None,
        "preferred_timezones": [],
        "raw_location_text": None,
        "title": None,
    }
    base.update(overrides)
    return base


def test_extract_parses_discord_role_types() -> None:
    payload = _make_base_payload(
        discord_role_types=["Full Stack", "Backend"],
    )
    with patch("openai.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_openai_response(
            payload
        )
        result = extract_job_requirements("job text", api_key="test-key")

    assert result.discord_role_types == ["Full Stack", "Backend"]


def test_extract_filters_unknown_discord_role_types() -> None:
    payload = _make_base_payload(
        discord_role_types=["Full Stack", "NotARole", "Wizard"],
    )
    with patch("openai.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_openai_response(
            payload
        )
        result = extract_job_requirements("job text", api_key="test-key")

    assert result.discord_role_types == ["Full Stack"]


def test_extract_discord_role_types_normalizes_case_and_whitespace() -> None:
    payload = _make_base_payload(
        discord_role_types=["full stack", "  BACKEND  ", "AI engineer"],
    )
    with patch("openai.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_openai_response(
            payload
        )
        result = extract_job_requirements("job text", api_key="test-key")

    assert result.discord_role_types == ["Full Stack", "Backend", "AI Engineer"]


def test_extract_discord_role_types_deduplicates() -> None:
    payload = _make_base_payload(
        discord_role_types=["Full Stack", "full stack", "FULL STACK"],
    )
    with patch("openai.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_openai_response(
            payload
        )
        result = extract_job_requirements("job text", api_key="test-key")

    assert result.discord_role_types == ["Full Stack"]


def test_extract_discord_role_types_defaults_empty_when_missing() -> None:
    payload = _make_base_payload()
    del payload["discord_role_types"]
    with patch("openai.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_openai_response(
            payload
        )
        result = extract_job_requirements("job text", api_key="test-key")

    assert result.discord_role_types == []


# ---------------------------------------------------------------------------
# DISCORD_ROLES_EXCLUDE_FROM_SYNC
# ---------------------------------------------------------------------------


def test_discord_roles_exclude_contains_bots_and_fixtweet() -> None:
    assert "Bots" in DISCORD_ROLES_EXCLUDE_FROM_SYNC
    assert "FixTweet" in DISCORD_ROLES_EXCLUDE_FROM_SYNC
    assert "@everyone" in DISCORD_ROLES_EXCLUDE_FROM_SYNC


def test_discord_roles_exclude_does_not_contain_member() -> None:
    assert "Member" not in DISCORD_ROLES_EXCLUDE_FROM_SYNC


# ---------------------------------------------------------------------------
# DISCORD_ROLES_NEVER_SUGGEST
# ---------------------------------------------------------------------------


def test_discord_roles_never_suggest_contains_required_exclusions() -> None:
    for role in ("Member", "FixTweet", "Bots", "Admin", "508 Bot"):
        assert role in DISCORD_ROLES_NEVER_SUGGEST


# ---------------------------------------------------------------------------
# DISCORD_LOCALITY_ROLE_NAMES
# ---------------------------------------------------------------------------


def test_discord_locality_role_names_contains_all_regions() -> None:
    for region in ("Asia", "Americas", "Europe", "USA", "Taiwan", "Japan", "Africa"):
        assert region in DISCORD_LOCALITY_ROLE_NAMES


# ---------------------------------------------------------------------------
# suggest_locality_discord_roles
# ---------------------------------------------------------------------------


def test_locality_usa_returns_usa_and_americas() -> None:
    result = suggest_locality_discord_roles("United States")
    assert "USA" in result
    assert "Americas" in result


def test_locality_usa_case_insensitive() -> None:
    assert suggest_locality_discord_roles(
        "united states"
    ) == suggest_locality_discord_roles("United States")


def test_locality_japan_returns_japan_and_asia() -> None:
    result = suggest_locality_discord_roles("Japan")
    assert "Japan" in result
    assert "Asia" in result


def test_locality_taiwan_returns_taiwan_and_asia() -> None:
    result = suggest_locality_discord_roles("Taiwan")
    assert "Taiwan" in result
    assert "Asia" in result


def test_locality_canada_returns_americas_only() -> None:
    result = suggest_locality_discord_roles("Canada")
    assert "Americas" in result
    assert "USA" not in result


def test_locality_germany_returns_europe() -> None:
    result = suggest_locality_discord_roles("Germany")
    assert "Europe" in result


def test_locality_nigeria_returns_africa() -> None:
    result = suggest_locality_discord_roles("Nigeria")
    assert "Africa" in result


def test_locality_unknown_country_returns_empty() -> None:
    assert suggest_locality_discord_roles("Narnia") == []


def test_locality_none_returns_empty() -> None:
    assert suggest_locality_discord_roles(None) == []


def test_locality_empty_string_returns_empty() -> None:
    assert suggest_locality_discord_roles("") == []


# ---------------------------------------------------------------------------
# suggest_technical_discord_roles
# ---------------------------------------------------------------------------


def test_technical_react_suggests_frontend() -> None:
    result = suggest_technical_discord_roles(["React"], [])
    assert "Frontend" in result


def test_technical_solidity_suggests_blockchain() -> None:
    result = suggest_technical_discord_roles(["Solidity"], [])
    assert "Blockchain" in result


def test_technical_kubernetes_suggests_infra() -> None:
    result = suggest_technical_discord_roles(["Kubernetes"], [])
    assert "Infra / Devops" in result


def test_technical_pytorch_suggests_ai_engineer() -> None:
    result = suggest_technical_discord_roles(["PyTorch"], [])
    assert "AI Engineer" in result


def test_technical_flutter_suggests_mobile() -> None:
    result = suggest_technical_discord_roles(["Flutter"], [])
    assert "Mobile" in result


def test_technical_swift_suggests_ios() -> None:
    result = suggest_technical_discord_roles(["Swift"], [])
    assert "iOS" in result


def test_technical_kotlin_suggests_android() -> None:
    result = suggest_technical_discord_roles(["Kotlin"], [])
    assert "Android" in result


def test_technical_crm_designer_role_suggests_designer() -> None:
    result = suggest_technical_discord_roles([], ["designer"])
    assert "Designer" in result


def test_technical_crm_product_manager_suggests_product_manager() -> None:
    result = suggest_technical_discord_roles([], ["product manager"])
    assert "Product Manager" in result


def test_technical_crm_data_scientist_suggests_data_scientist() -> None:
    result = suggest_technical_discord_roles([], ["data scientist"])
    assert "Data Scientist" in result


def test_technical_deduplicates_results() -> None:
    # Multiple skills mapping to the same role should only appear once
    result = suggest_technical_discord_roles(["React", "Vue", "Angular"], [])
    assert result.count("Frontend") == 1


def test_technical_empty_inputs_returns_empty() -> None:
    assert suggest_technical_discord_roles([], []) == []


def test_technical_unknown_skills_returns_empty() -> None:
    assert suggest_technical_discord_roles(["Underwater Basket Weaving"], []) == []


def test_technical_returns_only_canonical_role_names() -> None:
    result = suggest_technical_discord_roles(
        ["React", "Solidity", "Kubernetes", "Swift"], []
    )
    for role in result:
        assert role in DISCORD_SKILL_ROLE_NAMES


def test_technical_react_native_suggests_mobile_not_frontend() -> None:
    # Regression: "React Native" has an exact match → Mobile.
    # The substring "react" must NOT additionally suggest Frontend.
    result = suggest_technical_discord_roles(["React Native"], [])
    assert "Mobile" in result
    assert "Frontend" not in result


def test_technical_exact_match_already_seen_skips_substring_scan() -> None:
    # "React" is already suggested via an earlier skill; a second "react"-containing
    # skill with an exact match should not trigger a spurious substring Frontend hit.
    result = suggest_technical_discord_roles(["React", "React Native"], [])
    assert "Frontend" in result
    assert "Mobile" in result
    assert result.count("Frontend") == 1
    assert result.count("Mobile") == 1
