"""Unit tests for the shared Migadu API client."""

from unittest.mock import Mock, patch

import pytest

from five08.clients.migadu import MigaduAPIError, MigaduClient


def test_list_mailboxes_truncates_error_response_body() -> None:
    response = Mock()
    response.status_code = 500
    response.text = f"email=jane@508.dev {'x' * 600}"

    with patch("five08.clients.migadu.requests.get", return_value=response):
        with pytest.raises(MigaduAPIError) as exc_info:
            MigaduClient(
                username="migadu-user",
                api_key="migadu-key",
                domain="508.dev",
            ).list_mailboxes()

    message = str(exc_info.value)
    assert "jane@508.dev" not in message
    assert "[redacted-email]" in message
    assert len(message) < 600
