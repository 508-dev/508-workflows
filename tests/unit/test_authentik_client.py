"""Unit tests for the shared Authentik client."""

from unittest.mock import Mock, patch

import pytest

from five08.clients.authentik import AuthentikAPIError, AuthentikClient


def test_create_user_posts_expected_payload() -> None:
    """User creation should post a minimal non-superuser payload."""
    mock_response = Mock()
    mock_response.status_code = 201
    mock_response.content = b'{"pk": 42}'
    mock_response.json.return_value = {"pk": 42}

    with patch(
        "five08.clients.authentik.requests.request",
        return_value=mock_response,
    ) as mock_request:
        result = AuthentikClient(
            "https://authentik.example.com",
            "secret",
        ).create_user(
            username="jane",
            name="Jane Doe",
            email="jane@508.dev",
        )

    assert result == {"pk": 42}
    mock_request.assert_called_once_with(
        "POST",
        "https://authentik.example.com/api/v3/core/users/",
        headers={
            "Accept": "application/json",
            "Authorization": "Bearer secret",
            "Content-Type": "application/json",
        },
        params=None,
        json={
            "username": "jane",
            "name": "Jane Doe",
            "is_active": True,
            "type": "internal",
            "email": "jane@508.dev",
        },
        timeout=20.0,
    )


def test_send_recovery_email_posts_required_stage() -> None:
    """Recovery emails should use the Authentik stage UUID payload."""
    mock_response = Mock()
    mock_response.status_code = 204
    mock_response.content = b""
    mock_response.text = ""

    with patch(
        "five08.clients.authentik.requests.request",
        return_value=mock_response,
    ) as mock_request:
        AuthentikClient(
            "https://authentik.example.com/api/v3",
            "secret",
        ).send_recovery_email(
            user_id=42,
            email_stage="3fa85f64-5717-4562-b3fc-2c963f66afa6",
        )

    mock_request.assert_called_once_with(
        "POST",
        "https://authentik.example.com/api/v3/core/users/42/recovery_email/",
        headers={
            "Accept": "application/json",
            "Authorization": "Bearer secret",
            "Content-Type": "application/json",
        },
        params=None,
        json={"email_stage": "3fa85f64-5717-4562-b3fc-2c963f66afa6"},
        timeout=20.0,
    )


def test_resolve_email_stage_id_returns_explicit_override_without_lookup() -> None:
    """An explicit stage UUID should bypass the list call."""
    client = AuthentikClient("https://authentik.example.com", "secret")

    with patch.object(client, "list_email_stages") as mock_list:
        result = client.resolve_email_stage_id(
            stage_name="default-recovery-email",
            stage_id="3fa85f64-5717-4562-b3fc-2c963f66afa6",
        )

    assert result == "3fa85f64-5717-4562-b3fc-2c963f66afa6"
    mock_list.assert_not_called()


def test_resolve_email_stage_id_looks_up_exact_stage_name() -> None:
    """Stage name resolution should return the matched Email Stage UUID."""
    client = AuthentikClient("https://authentik.example.com", "secret")

    with patch.object(
        client,
        "list_email_stages",
        return_value={
            "results": [
                {
                    "pk": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                    "name": "default-recovery-email",
                }
            ]
        },
    ) as mock_list:
        result = client.resolve_email_stage_id(
            stage_name="default-recovery-email",
        )

    assert result == "3fa85f64-5717-4562-b3fc-2c963f66afa6"
    mock_list.assert_called_once_with(
        params={"name": "default-recovery-email", "page_size": 20}
    )


def test_resolve_email_stage_id_raises_when_stage_name_missing() -> None:
    """Missing Email Stage names should surface a shared API error."""
    client = AuthentikClient("https://authentik.example.com", "secret")

    with patch.object(client, "list_email_stages", return_value={"results": []}):
        with pytest.raises(
            AuthentikAPIError,
            match="No Authentik email stage found named 'default-recovery-email'",
        ):
            client.resolve_email_stage_id(stage_name="default-recovery-email")


def test_find_users_by_username_or_email_deduplicates_matches() -> None:
    """Username and email lookups should return unique users by id."""
    client = AuthentikClient("https://authentik.example.com", "secret")

    with patch.object(
        client,
        "list_users",
        side_effect=[
            {"results": [{"pk": 42, "username": "jane", "email": "jane@508.dev"}]},
            {"results": [{"pk": 42, "username": "jane", "email": "jane@508.dev"}]},
        ],
    ) as mock_list:
        result = client.find_users_by_username_or_email(
            username="jane",
            email="jane@508.dev",
        )

    assert result == [{"pk": 42, "username": "jane", "email": "jane@508.dev"}]
    assert mock_list.call_count == 2


def test_find_users_by_username_or_email_filters_non_exact_matches() -> None:
    """Search results should be filtered locally to exact username/email matches."""
    client = AuthentikClient("https://authentik.example.com", "secret")

    with patch.object(
        client,
        "list_users",
        side_effect=[
            {
                "results": [
                    {"pk": 1, "username": "jane-dev", "email": "jane@508.dev"},
                    {"pk": 2, "username": "jane", "email": "jane-other@508.dev"},
                ]
            },
            {
                "results": [
                    {"pk": 3, "username": "other", "email": "other@508.dev"},
                    {"pk": 4, "username": "jane", "email": "jane@508.dev"},
                ]
            },
        ],
    ):
        result = client.find_users_by_username_or_email(
            username="jane",
            email="jane@508.dev",
        )

    assert result == [
        {"pk": 2, "username": "jane", "email": "jane-other@508.dev"},
        {"pk": 4, "username": "jane", "email": "jane@508.dev"},
    ]


def test_request_raises_on_non_success_status() -> None:
    """Non-2xx Authentik responses should raise a shared API error."""
    mock_response = Mock()
    mock_response.status_code = 403
    mock_response.reason = "Forbidden"
    mock_response.text = '{"detail":"forbidden"}'
    mock_response.content = b'{"detail":"forbidden"}'
    mock_response.json.return_value = {"detail": "forbidden"}

    with patch(
        "five08.clients.authentik.requests.request",
        return_value=mock_response,
    ):
        with pytest.raises(
            AuthentikAPIError,
            match="Authentik request failed with status 403: Forbidden \\(forbidden\\)",
        ):
            AuthentikClient("https://authentik.example.com", "secret").get_user(42)
