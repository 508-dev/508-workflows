"""Unit tests for job_match extraction helpers."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from five08.job_match import (
    DISCORD_ROLES_EXCLUDE_FROM_SYNC,
    DISCORD_SKILL_ROLE_NAMES,
    _build_prompt,
    _coerce_str_list,
    _parse_llm_response,
    _regex_hints,
    extract_job_requirements,
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
