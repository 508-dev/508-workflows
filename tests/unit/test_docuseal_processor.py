"""Unit tests for Docuseal processor logic."""

import pytest
from unittest.mock import Mock, patch

from five08.clients.espo import EspoAPIError
from five08.worker.crm.docuseal_processor import (
    DocusealAgreementNonRetryableError,
    DocusealAgreementProcessingError,
    DocusealAgreementProcessor,
)
from five08.clients.discord_bot import DiscordBotAPIError
from five08.worker.masking import mask_email


def test_docuseal_processor_marks_member_agreement_signed_timestamp() -> None:
    """Processor should update the member agreement signed-at timestamp."""
    mock_api = Mock()
    mock_api.request.side_effect = [
        {"list": [{"id": "contact-1", "name": "Jane Doe", "cDiscordUserID": "1234"}]},
        {"updated": True},
    ]
    expected_email = "member@508.dev"
    expected_masked = mask_email(expected_email)

    with (
        patch("five08.worker.crm.docuseal_processor.EspoClient", return_value=mock_api),
        patch(
            "five08.worker.crm.docuseal_processor.settings.api_shared_secret",
            "top-secret",
        ),
        patch(
            "five08.worker.crm.docuseal_processor.grant_member_role_for_signed_agreement",
            return_value={"status": "applied", "role": "Member"},
        ) as mock_grant_role,
    ):
        processor = DocusealAgreementProcessor()
        result = processor.process_agreement(
            email=expected_email,
            completed_at="2026-02-25T12:00:00Z",
            submission_id=416,
        )

    assert mock_api.request.call_count == 2
    assert mock_api.request.call_args_list[1].args[1] == "Contact/contact-1"
    assert mock_api.request.call_args_list[1].args[2] == {
        "cMemberAgreementSignedAt": "2026-02-25 12:00:00",
    }
    assert result["success"] is True
    assert result["masked_email"] == expected_masked
    assert result["contact_id"] == "contact-1"
    assert result["submission_id"] == 416
    assert result["completed_at"] == "2026-02-25 12:00:00"
    assert result["discord_user_id"] == "1234"
    assert result["member_role"]["status"] == "applied"
    mock_grant_role.assert_called_once_with(
        base_url="http://discord_bot:3000",
        api_secret="top-secret",
        discord_user_id="1234",
        contact_id="contact-1",
        contact_name="Jane Doe",
        submission_id=416,
        completed_at="2026-02-25 12:00:00",
    )
    assert "email" not in result


def test_docuseal_processor_normalizes_completed_at_to_utc_timestamp() -> None:
    """Processor should convert a UTC-offset timestamp before writing CRM."""
    mock_api = Mock()
    mock_api.request.side_effect = [
        {"list": [{"id": "contact-1"}]},
        {"updated": True},
    ]

    with (
        patch("five08.worker.crm.docuseal_processor.EspoClient", return_value=mock_api),
        patch(
            "five08.worker.crm.docuseal_processor.grant_member_role_for_signed_agreement"
        ) as mock_grant_role,
    ):
        processor = DocusealAgreementProcessor()
        result = processor.process_agreement(
            email="member@508.dev",
            completed_at="2026-03-02T10:02:30.572+02:00",
            submission_id=416,
        )

    assert mock_api.request.call_args_list[1].args[2] == {
        "cMemberAgreementSignedAt": "2026-03-02 08:02:30",
    }
    assert result["completed_at"] == "2026-03-02 08:02:30"
    assert result["member_role"]["status"] == "not_linked"
    mock_grant_role.assert_not_called()


def test_docuseal_processor_skips_role_grant_without_bot_base_url() -> None:
    """Missing bot base URL should be reported without calling the bot client."""
    mock_api = Mock()
    mock_api.request.side_effect = [
        {"list": [{"id": "contact-1", "cDiscordUserID": "1234"}]},
        {"updated": True},
    ]

    with (
        patch("five08.worker.crm.docuseal_processor.EspoClient", return_value=mock_api),
        patch("five08.worker.crm.docuseal_processor.logger.warning") as mock_warning,
        patch(
            "five08.worker.crm.docuseal_processor.settings.discord_bot_internal_base_url",
            " ",
        ),
        patch(
            "five08.worker.crm.docuseal_processor.settings.api_shared_secret",
            "top-secret",
        ),
        patch(
            "five08.worker.crm.docuseal_processor.grant_member_role_for_signed_agreement"
        ) as mock_grant_role,
    ):
        processor = DocusealAgreementProcessor()
        result = processor.process_agreement(
            email="member@508.dev",
            completed_at="2026-02-25T12:00:00Z",
            submission_id=417,
        )

    assert result["success"] is True
    assert result["member_role"]["status"] == "bot_endpoint_not_configured"
    mock_grant_role.assert_not_called()
    mock_warning.assert_called_once_with(
        "Skipping Member role grant for contact_id=%s: "
        "DISCORD_BOT_INTERNAL_BASE_URL is not configured",
        "contact-1",
    )


def test_docuseal_processor_skips_role_grant_without_api_secret() -> None:
    """Missing shared API secret should be reported without calling the bot client."""
    mock_api = Mock()
    mock_api.request.side_effect = [
        {"list": [{"id": "contact-1", "cDiscordUserID": "1234"}]},
        {"updated": True},
    ]

    with (
        patch("five08.worker.crm.docuseal_processor.EspoClient", return_value=mock_api),
        patch("five08.worker.crm.docuseal_processor.logger.warning") as mock_warning,
        patch(
            "five08.worker.crm.docuseal_processor.settings.api_shared_secret",
            " ",
        ),
        patch(
            "five08.worker.crm.docuseal_processor.grant_member_role_for_signed_agreement"
        ) as mock_grant_role,
    ):
        processor = DocusealAgreementProcessor()
        result = processor.process_agreement(
            email="member@508.dev",
            completed_at="2026-02-25T12:00:00Z",
            submission_id=418,
        )

    assert result["success"] is True
    assert result["member_role"]["status"] == "api_secret_not_configured"
    mock_grant_role.assert_not_called()
    mock_warning.assert_called_once_with(
        "Skipping Member role grant for contact_id=%s: "
        "API_SHARED_SECRET is not configured",
        "contact-1",
    )


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("cDiscordUserId", "111"),
        ("discordUserId", "222"),
        ("cDiscordID", "333"),
        ("cDiscordId", "444"),
        ("cDiscordUserID", "555"),
    ],
)
def test_docuseal_processor_reads_supported_discord_id_aliases(
    field_name: str,
    field_value: str,
) -> None:
    """All supported Discord ID aliases should trigger the role-grant path."""
    mock_api = Mock()
    mock_api.request.side_effect = [
        {"list": [{"id": "contact-1", field_name: field_value}]},
        {"updated": True},
    ]

    with (
        patch("five08.worker.crm.docuseal_processor.EspoClient", return_value=mock_api),
        patch(
            "five08.worker.crm.docuseal_processor.settings.api_shared_secret",
            "top-secret",
        ),
        patch(
            "five08.worker.crm.docuseal_processor.grant_member_role_for_signed_agreement",
            return_value={"status": "applied"},
        ) as mock_grant_role,
    ):
        processor = DocusealAgreementProcessor()
        result = processor.process_agreement(
            email="member@508.dev",
            completed_at="2026-02-25T12:00:00Z",
            submission_id=419,
        )

    assert result["success"] is True
    assert result["discord_user_id"] == field_value
    assert result["member_role"]["status"] == "applied"
    assert mock_grant_role.call_args.kwargs["discord_user_id"] == field_value


def test_docuseal_processor_reads_discord_id_from_username_fallback() -> None:
    """Mention-style cDiscordUsername values should still resolve to an ID."""
    mock_api = Mock()
    mock_api.request.side_effect = [
        {
            "list": [
                {
                    "id": "contact-1",
                    "cDiscordUsername": "janedoe (ID: 987654321)",
                }
            ]
        },
        {"updated": True},
    ]

    with (
        patch("five08.worker.crm.docuseal_processor.EspoClient", return_value=mock_api),
        patch(
            "five08.worker.crm.docuseal_processor.settings.api_shared_secret",
            "top-secret",
        ),
        patch(
            "five08.worker.crm.docuseal_processor.grant_member_role_for_signed_agreement",
            return_value={"status": "applied"},
        ) as mock_grant_role,
    ):
        processor = DocusealAgreementProcessor()
        result = processor.process_agreement(
            email="member@508.dev",
            completed_at="2026-02-25T12:00:00Z",
            submission_id=420,
        )

    assert result["success"] is True
    assert result["discord_user_id"] == "987654321"
    assert result["member_role"]["status"] == "applied"
    assert mock_grant_role.call_args.kwargs["discord_user_id"] == "987654321"


def test_docuseal_processor_raises_on_invalid_completed_at() -> None:
    """Processor should raise so the job runner can mark the job non-retryable/dead."""
    mock_api = Mock()
    mock_api.request.side_effect = [
        {"list": [{"id": "contact-1"}]},
    ]

    with patch(
        "five08.worker.crm.docuseal_processor.EspoClient", return_value=mock_api
    ):
        processor = DocusealAgreementProcessor()
        with pytest.raises(DocusealAgreementNonRetryableError) as exc_info:
            processor.process_agreement(
                email="member@508.dev",
                completed_at="not-a-date",
                submission_id=416,
            )

    assert "invalid_completed_at for contact_id=contact-1" in str(exc_info.value)
    assert mock_api.request.call_count == 1


def test_docuseal_processor_returns_contact_not_found_when_missing_contact() -> None:
    """Processor should return a contact-not-found error without raw email."""
    mock_api = Mock()
    mock_api.request.return_value = {"list": []}

    with patch(
        "five08.worker.crm.docuseal_processor.EspoClient", return_value=mock_api
    ):
        processor = DocusealAgreementProcessor()
        result = processor.process_agreement(
            email="missing@508.dev",
            completed_at="2026-02-25T12:00:00Z",
            submission_id=123,
        )

    assert result["success"] is False
    assert result["error"] == "contact_not_found"
    assert result["masked_email"] == mask_email("missing@508.dev")
    assert result["masked_email"] != "missing@508.dev"
    assert mock_api.request.call_count == 1


def test_docuseal_processor_raises_on_search_failure() -> None:
    """Processor should raise when CRM search fails to trigger job retries."""
    mock_api = Mock()
    mock_api.request.side_effect = EspoAPIError("CRM unavailable")

    with patch(
        "five08.worker.crm.docuseal_processor.EspoClient", return_value=mock_api
    ):
        processor = DocusealAgreementProcessor()
        with pytest.raises(DocusealAgreementProcessingError) as exc_info:
            processor.process_agreement(
                email="broken@508.dev",
                completed_at="2026-02-25T12:00:00Z",
                submission_id=55,
            )

    assert (
        str(exc_info.value)
        == f"CRM search failed for masked_email={mask_email('broken@508.dev')}: "
        "CRM unavailable"
    )


def test_docuseal_processor_raises_on_update_failure() -> None:
    """Processor should raise when CRM update fails to trigger job retries."""
    mock_api = Mock()
    mock_api.request.side_effect = [
        {"list": [{"id": "contact-1"}]},
        EspoAPIError("write failed"),
    ]

    with patch(
        "five08.worker.crm.docuseal_processor.EspoClient", return_value=mock_api
    ):
        processor = DocusealAgreementProcessor()
        with pytest.raises(DocusealAgreementProcessingError) as exc_info:
            processor.process_agreement(
                email="member@508.dev",
                completed_at="2026-02-25T12:00:00Z",
                submission_id=9001,
            )

    assert (
        str(exc_info.value)
        == "CRM update failed for contact_id=contact-1: write failed"
    )


def test_docuseal_processor_role_assignment_error_is_best_effort() -> None:
    """CRM success should survive bot role assignment failures."""
    mock_api = Mock()
    mock_api.request.side_effect = [
        {"list": [{"id": "contact-1", "cDiscordUserID": "1234"}]},
        {"updated": True},
    ]

    with (
        patch("five08.worker.crm.docuseal_processor.EspoClient", return_value=mock_api),
        patch("five08.worker.crm.docuseal_processor.logger.warning") as mock_warning,
        patch(
            "five08.worker.crm.docuseal_processor.settings.api_shared_secret",
            "top-secret",
        ),
        patch(
            "five08.worker.crm.docuseal_processor.grant_member_role_for_signed_agreement",
            side_effect=DiscordBotAPIError("status code is 403"),
        ),
    ):
        processor = DocusealAgreementProcessor()
        result = processor.process_agreement(
            email="member@508.dev",
            completed_at="2026-02-25T12:00:00Z",
            submission_id=9001,
        )

    assert result["success"] is True
    assert result["member_role"]["status"] == "error"
    assert result["member_role"]["discord_user_id"] == "1234"
    assert "status code is 403" in result["member_role"]["error"]
    mock_warning.assert_called_once()
    warning_args = mock_warning.call_args.args
    assert (
        warning_args[0] == "Best-effort Member role assignment failed contact_id=%s: %s"
    )
    assert warning_args[1] == "contact-1"
    assert "1234" not in str(warning_args[2])
    assert "status code is 403" in str(warning_args[2])
