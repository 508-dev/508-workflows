"""Unit tests for the shared GitHub client."""

from __future__ import annotations

from unittest.mock import patch

from five08.clients.github import GitHubClient
from five08.tls import default_ca_bundle_path


class _FakeResponse:
    status_code = 200
    content = b'{"number": 1}'
    text = '{"number": 1}'

    def json(self) -> dict[str, int]:
        return {"number": 1}


def test_github_client_uses_default_ca_bundle() -> None:
    client = GitHubClient(token="token")

    with patch("five08.clients.github.requests.request") as mock_request:
        mock_request.return_value = _FakeResponse()

        client.create_issue(repository="508-dev/508-workflows", title="Fix thing")

    assert mock_request.call_args.kwargs["verify"] == default_ca_bundle_path()
