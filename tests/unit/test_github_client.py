"""Unit tests for the shared GitHub client."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from five08 import deadlines
from five08.clients.github import GitHubAppTokenProvider, GitHubClient
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


def test_github_client_clamps_timeout_to_execution_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deadlines, "monotonic", lambda: 100.0)
    client = GitHubClient(token="token", deadline_monotonic=105.0)

    with patch("five08.clients.github.requests.request") as mock_request:
        mock_request.return_value = _FakeResponse()
        client.get_issue(repository="508-dev/508-workflows", issue_number=1)

    assert mock_request.call_args.kwargs["timeout"] == 5.0


def test_github_client_passes_its_deadline_to_the_token_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Provider:
        def __init__(self) -> None:
            self.deadlines: list[float | None] = []

        def get_token(
            self,
            *,
            repositories: object = None,
            permissions: object = None,
            deadline_monotonic: float | None = None,
        ) -> str:
            del repositories, permissions
            self.deadlines.append(deadline_monotonic)
            return "token"

        def invalidate(self, **_kwargs: object) -> None:
            return None

    provider = Provider()
    monkeypatch.setattr(deadlines, "monotonic", lambda: 100.0)
    client = GitHubClient(token_provider=provider, deadline_monotonic=105.0)

    with patch("five08.clients.github.requests.request") as mock_request:
        mock_request.return_value = _FakeResponse()
        client.get_issue(repository="508-dev/508-workflows", issue_number=1)

    assert provider.deadlines == [105.0]


class _Response:
    def __init__(self, *, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload
        self.content = b"{}"
        self.text = str(payload)

    def json(self) -> object:
        return self._payload


def test_github_app_provider_mints_and_caches_narrow_installation_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, object]] = []

    def fake_request(*_args: object, **kwargs: object) -> _Response:
        requests.append(dict(kwargs))
        return _Response(
            status_code=201,
            payload={
                "token": "installation-token",
                "expires_at": "2030-01-01T00:00:00Z",
            },
        )

    jwt_payloads: list[dict[str, object]] = []

    def fake_encode(
        payload: dict[str, object], *_args: object, **_kwargs: object
    ) -> str:
        jwt_payloads.append(payload)
        return "app-jwt"

    monkeypatch.setattr("five08.clients.github.jwt.encode", fake_encode)
    monkeypatch.setattr("five08.clients.github.requests.request", fake_request)
    provider = GitHubAppTokenProvider(
        client_id="Iv1.client-id",
        installation_id="456",
        private_key="private-key",
    )

    first = provider.get_token(
        repositories=["508-dev/todos"],
        permissions={"issues": "write"},
    )
    second = provider.get_token(
        repositories=["508-dev/todos"],
        permissions={"issues": "write"},
    )

    assert first == "installation-token"
    assert second == "installation-token"
    assert len(requests) == 1
    assert requests[0]["json"] == {
        "repositories": ["todos"],
        "permissions": {"issues": "write"},
    }
    headers = requests[0]["headers"]
    assert isinstance(headers, dict)
    assert headers["Authorization"] == "Bearer app-jwt"
    assert jwt_payloads[0]["iss"] == "Iv1.client-id"


def test_github_client_refreshes_provider_once_after_unauthorized_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Provider:
        def __init__(self) -> None:
            self.tokens = ["expired", "fresh"]
            self.requests: list[tuple[object, object]] = []
            self.invalidations: list[tuple[object, object]] = []

        def get_token(
            self,
            *,
            repositories: object = None,
            permissions: object = None,
            deadline_monotonic: float | None = None,
        ) -> str:
            del deadline_monotonic
            self.requests.append((repositories, permissions))
            return self.tokens.pop(0)

        def invalidate(
            self,
            *,
            repositories: object = None,
            permissions: object = None,
        ) -> None:
            self.invalidations.append((repositories, permissions))

    provider = Provider()
    responses = [
        _Response(status_code=401, payload={"message": "Bad credentials"}),
        _Response(status_code=201, payload={"number": 7}),
    ]
    headers: list[dict[str, str]] = []

    def fake_request(*_args: object, **kwargs: object) -> _Response:
        request_headers = kwargs.get("headers")
        assert isinstance(request_headers, dict)
        headers.append(request_headers)
        return responses.pop(0)

    monkeypatch.setattr("five08.clients.github.requests.request", fake_request)
    result = GitHubClient(token_provider=provider).create_issue(
        repository="508-dev/todos",
        title="Follow up",
    )

    assert result == {"number": 7}
    assert [header["Authorization"] for header in headers] == [
        "Bearer expired",
        "Bearer fresh",
    ]
    assert provider.invalidations == [(["508-dev/todos"], {"issues": "write"})]


def test_github_projects_use_organization_projects_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Provider:
        def __init__(self) -> None:
            self.requests: list[tuple[object, object]] = []

        def get_token(
            self,
            *,
            repositories: object = None,
            permissions: object = None,
            deadline_monotonic: float | None = None,
        ) -> str:
            del deadline_monotonic
            self.requests.append((repositories, permissions))
            return "token"

        def invalidate(self, **_kwargs: object) -> None:
            return None

    provider = Provider()
    monkeypatch.setattr(
        "five08.clients.github.requests.request",
        lambda *_args, **_kwargs: _Response(status_code=200, payload={"id": 99}),
    )

    GitHubClient(token_provider=provider).update_organization_project_item(
        organization="508-dev",
        project_number=3,
        item_id=99,
        fields=[{"id": 4, "value": "Done"}],
    )

    assert provider.requests == [(None, {"organization_projects": "write"})]
