"""GitHub App and REST helpers used by deterministic agent tools."""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol, TypeAlias

import jwt
import requests

from five08.deadlines import DeadlineExceeded, clamp_timeout_seconds
from five08.tls import default_ca_bundle_path


GITHUB_API_VERSION = "2026-03-10"
_TOKEN_REFRESH_SKEW = timedelta(minutes=5)
_APP_JWT_LIFETIME = timedelta(minutes=9)
_UNSET = object()
_TokenCacheKey: TypeAlias = tuple[tuple[str, ...] | None, tuple[tuple[str, str], ...]]


class GitHubAPIError(RuntimeError):
    """Raised when a GitHub API request fails."""


class GitHubTokenProvider(Protocol):
    """Provide a short-lived token scoped to a GitHub operation."""

    def get_token(
        self,
        *,
        repositories: Sequence[str] | None = None,
        permissions: Mapping[str, str] | None = None,
        deadline_monotonic: float | None = None,
    ) -> str:
        """Return a token for the requested repository and permission scope."""

    def invalidate(
        self,
        *,
        repositories: Sequence[str] | None = None,
        permissions: Mapping[str, str] | None = None,
    ) -> None:
        """Discard a cached token after an authentication failure."""


@dataclass(frozen=True)
class _CachedInstallationToken:
    token: str
    expires_at: datetime


class StaticGitHubTokenProvider:
    """Compatibility provider for a pre-existing GitHub API token."""

    def __init__(self, token: str) -> None:
        self._token = token

    def get_token(
        self,
        *,
        repositories: Sequence[str] | None = None,
        permissions: Mapping[str, str] | None = None,
        deadline_monotonic: float | None = None,
    ) -> str:
        del repositories, permissions, deadline_monotonic
        return self._token

    def invalidate(
        self,
        *,
        repositories: Sequence[str] | None = None,
        permissions: Mapping[str, str] | None = None,
    ) -> None:
        del repositories, permissions


class GitHubAppTokenProvider:
    """Mint and cache least-privilege GitHub App installation tokens."""

    def __init__(
        self,
        *,
        client_id: str | int,
        installation_id: str | int,
        private_key: str,
        base_url: str = "https://api.github.com",
        timeout_seconds: float = 20.0,
        deadline_monotonic: float | None = None,
    ) -> None:
        self.client_id = str(client_id).strip()
        self.installation_id = str(installation_id).strip()
        self.private_key = private_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.deadline_monotonic = deadline_monotonic
        self._cache: dict[_TokenCacheKey, _CachedInstallationToken] = {}
        self._lock = threading.RLock()

        if not self.client_id:
            raise ValueError("GitHub App client ID is required")
        if not self.installation_id:
            raise ValueError("GitHub App installation id is required")
        if not self.private_key.strip():
            raise ValueError("GitHub App private key is required")

    def get_token(
        self,
        *,
        repositories: Sequence[str] | None = None,
        permissions: Mapping[str, str] | None = None,
        deadline_monotonic: float | None = None,
    ) -> str:
        """Return a cached valid token or mint one restricted to this operation."""

        cache_key = _token_cache_key(repositories=repositories, permissions=permissions)
        now = datetime.now(timezone.utc)
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached is not None and cached.expires_at > now + _TOKEN_REFRESH_SKEW:
                return cached.token

            payload: dict[str, Any] = {}
            repository_names = _installation_repository_names(repositories)
            if repository_names is not None:
                payload["repositories"] = repository_names
            if permissions:
                payload["permissions"] = dict(permissions)

            try:
                timeout_seconds = clamp_timeout_seconds(
                    self.timeout_seconds,
                    deadline_monotonic=(
                        deadline_monotonic
                        if deadline_monotonic is not None
                        else self.deadline_monotonic
                    ),
                )
            except DeadlineExceeded as exc:
                raise GitHubAPIError("GitHub token request deadline exceeded") from exc
            try:
                response = requests.request(
                    "POST",
                    f"{self.base_url}/app/installations/{self.installation_id}/access_tokens",
                    headers={
                        "Accept": "application/vnd.github+json",
                        "Authorization": f"Bearer {self._app_jwt(now)}",
                        "X-GitHub-Api-Version": GITHUB_API_VERSION,
                    },
                    json=payload or None,
                    timeout=timeout_seconds,
                    verify=default_ca_bundle_path(),
                )
            except requests.RequestException as exc:
                raise GitHubAPIError(
                    f"GitHub installation token request failed: {exc}"
                ) from exc
            if not 200 <= response.status_code < 300:
                raise GitHubAPIError(_github_error_message(response))
            try:
                response_payload = response.json()
            except ValueError as exc:
                raise GitHubAPIError(
                    "GitHub installation token response was not JSON"
                ) from exc
            if not isinstance(response_payload, dict):
                raise GitHubAPIError("GitHub installation token response was invalid")
            token = response_payload.get("token")
            expires_at = _parse_github_timestamp(response_payload.get("expires_at"))
            if not isinstance(token, str) or not token.strip() or expires_at is None:
                raise GitHubAPIError(
                    "GitHub installation token response was incomplete"
                )
            self._cache[cache_key] = _CachedInstallationToken(
                token=token,
                expires_at=expires_at,
            )
            return token

    def invalidate(
        self,
        *,
        repositories: Sequence[str] | None = None,
        permissions: Mapping[str, str] | None = None,
    ) -> None:
        """Invalidate one scoped token so the next request receives a fresh one."""

        cache_key = _token_cache_key(repositories=repositories, permissions=permissions)
        with self._lock:
            self._cache.pop(cache_key, None)

    def _app_jwt(self, now: datetime) -> str:
        issued_at = now - timedelta(seconds=60)
        return jwt.encode(
            {
                "iat": int(issued_at.timestamp()),
                "exp": int((now + _APP_JWT_LIFETIME).timestamp()),
                "iss": self.client_id,
            },
            self.private_key,
            algorithm="RS256",
        )


class GitHubClient:
    """Small GitHub REST client for repositories, issues, and Project boards."""

    def __init__(
        self,
        *,
        token: str | None = None,
        token_provider: GitHubTokenProvider | None = None,
        base_url: str = "https://api.github.com",
        timeout_seconds: float = 20.0,
        deadline_monotonic: float | None = None,
    ) -> None:
        if token_provider is None:
            if not token or not token.strip():
                raise ValueError("GitHub token or token provider is required")
            token_provider = StaticGitHubTokenProvider(token)
        self.token_provider = token_provider
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.deadline_monotonic = deadline_monotonic
        self.status_code: int | None = None

    def create_issue(
        self,
        *,
        repository: str,
        title: str,
        body: str | None = None,
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"title": title}
        if body:
            payload["body"] = body
        if labels:
            payload["labels"] = labels
        return self._object_request(
            "POST",
            f"/repos/{repository}/issues",
            json=payload,
            repositories=[repository],
            permissions={"issues": "write"},
        )

    def list_issues(
        self,
        *,
        repository: str,
        query: str = "",
        state: str = "open",
        limit: int = 10,
    ) -> dict[str, Any]:
        """List repository issues while excluding pull requests deterministically."""

        bounded_limit = min(max(limit, 1), 20)
        raw_items = self._list_request(
            "GET",
            f"/repos/{repository}/issues",
            params={
                "state": _issue_state(state),
                "per_page": min(max(bounded_limit * 5, 30), 100),
                "sort": "updated",
                "direction": "desc",
            },
            repositories=[repository],
            permissions={"issues": "read"},
        )
        normalized_query = query.strip().casefold()
        issues = [
            item
            for item in raw_items
            if isinstance(item, dict)
            and "pull_request" not in item
            and _issue_matches_query(item, normalized_query)
        ]
        return {"issues": issues[:bounded_limit], "total_count": len(issues)}

    def search_issues(
        self,
        *,
        repository: str,
        query: str,
        state: str = "open",
        limit: int = 10,
    ) -> dict[str, Any]:
        """Backward-compatible alias for repository-scoped issue lookup."""

        return self.list_issues(
            repository=repository,
            query=query,
            state=state,
            limit=limit,
        )

    def get_issue(self, *, repository: str, issue_number: int) -> dict[str, Any]:
        issue = self._object_request(
            "GET",
            f"/repos/{repository}/issues/{issue_number}",
            repositories=[repository],
            permissions={"issues": "read"},
        )
        if "pull_request" in issue:
            raise GitHubAPIError("Requested item is a pull request, not an issue")
        return issue

    def update_issue(
        self,
        *,
        repository: str,
        issue_number: int,
        title: str | None = None,
        body: str | None | object = _UNSET,
        state: str | None = None,
        state_reason: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if body is not _UNSET:
            payload["body"] = body
        if state is not None:
            payload["state"] = _issue_state(state)
        if state_reason is not None:
            payload["state_reason"] = state_reason
        if not payload:
            raise ValueError("At least one issue update is required")
        return self._object_request(
            "PATCH",
            f"/repos/{repository}/issues/{issue_number}",
            json=payload,
            repositories=[repository],
            permissions={"issues": "write"},
        )

    def add_issue_comment(
        self,
        *,
        repository: str,
        issue_number: int,
        body: str,
    ) -> dict[str, Any]:
        return self._object_request(
            "POST",
            f"/repos/{repository}/issues/{issue_number}/comments",
            json={"body": body},
            repositories=[repository],
            permissions={"issues": "write"},
        )

    def get_repository(self, *, repository: str) -> dict[str, Any]:
        return self._object_request(
            "GET",
            f"/repos/{repository}",
            repositories=[repository],
            permissions={"metadata": "read"},
        )

    def list_installation_repositories(self) -> dict[str, Any]:
        repositories: list[Any] = []
        page = 1
        total_count: int | None = None
        while page <= 100:
            payload = self._object_request(
                "GET",
                "/installation/repositories",
                params={"per_page": 100, "page": page},
                permissions={"metadata": "read"},
            )
            page_repositories = payload.get("repositories")
            if not isinstance(page_repositories, list) or not page_repositories:
                break
            repositories.extend(page_repositories)
            raw_total_count = payload.get("total_count")
            if isinstance(raw_total_count, int):
                total_count = raw_total_count
            if len(page_repositories) < 100 or (
                total_count is not None and len(repositories) >= total_count
            ):
                break
            page += 1
        return {"repositories": repositories}

    def list_organization_projects(
        self,
        *,
        organization: str,
        limit: int = 20,
    ) -> dict[str, Any]:
        payload = self._request(
            "GET",
            f"/orgs/{organization}/projectsV2",
            params={"per_page": min(max(limit, 1), 100)},
            permissions={"organization_projects": "read"},
        )
        return {"projects": _collection_from_payload(payload, key="projects")[:limit]}

    def get_organization_project(
        self,
        *,
        organization: str,
        project_number: int,
    ) -> dict[str, Any]:
        return self._object_request(
            "GET",
            f"/orgs/{organization}/projectsV2/{project_number}",
            permissions={"organization_projects": "read"},
        )

    def list_organization_project_fields(
        self,
        *,
        organization: str,
        project_number: int,
        limit: int = 100,
    ) -> dict[str, Any]:
        payload = self._request(
            "GET",
            f"/orgs/{organization}/projectsV2/{project_number}/fields",
            params={"per_page": min(max(limit, 1), 100)},
            permissions={"organization_projects": "read"},
        )
        return {"fields": _collection_from_payload(payload, key="fields")[:limit]}

    def list_organization_project_items(
        self,
        *,
        organization: str,
        project_number: int,
        limit: int = 20,
    ) -> dict[str, Any]:
        payload = self._request(
            "GET",
            f"/orgs/{organization}/projectsV2/{project_number}/items",
            params={"per_page": min(max(limit, 1), 100)},
            permissions={"organization_projects": "read"},
        )
        return {"items": _collection_from_payload(payload, key="items")[:limit]}

    def add_organization_project_item(
        self,
        *,
        organization: str,
        project_number: int,
        content_type: str,
        content_id: int,
    ) -> dict[str, Any]:
        return self._object_request(
            "POST",
            f"/orgs/{organization}/projectsV2/{project_number}/items",
            json={"type": content_type, "id": content_id},
            permissions={"organization_projects": "write"},
        )

    def update_organization_project_item(
        self,
        *,
        organization: str,
        project_number: int,
        item_id: int,
        fields: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._object_request(
            "PATCH",
            f"/orgs/{organization}/projectsV2/{project_number}/items/{item_id}",
            json={"fields": fields},
            permissions={"organization_projects": "write"},
        )

    def _object_request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        repositories: Sequence[str] | None = None,
        permissions: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        payload = self._request(
            method,
            path,
            json=json,
            params=params,
            repositories=repositories,
            permissions=permissions,
        )
        if not isinstance(payload, dict):
            raise GitHubAPIError("GitHub response was not a JSON object")
        return payload

    def _list_request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        repositories: Sequence[str] | None = None,
        permissions: Mapping[str, str] | None = None,
    ) -> list[Any]:
        payload = self._request(
            method,
            path,
            json=json,
            params=params,
            repositories=repositories,
            permissions=permissions,
        )
        if not isinstance(payload, list):
            raise GitHubAPIError("GitHub response was not a JSON list")
        return payload

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        repositories: Sequence[str] | None = None,
        permissions: Mapping[str, str] | None = None,
    ) -> Any:
        for attempt in range(2):
            token = self.token_provider.get_token(
                repositories=repositories,
                permissions=permissions,
                deadline_monotonic=self.deadline_monotonic,
            )
            try:
                timeout_seconds = clamp_timeout_seconds(
                    self.timeout_seconds,
                    deadline_monotonic=self.deadline_monotonic,
                )
            except DeadlineExceeded as exc:
                raise GitHubAPIError("GitHub request deadline exceeded") from exc
            try:
                response = requests.request(
                    method,
                    f"{self.base_url}{path}",
                    headers={
                        "Accept": "application/vnd.github+json",
                        "Authorization": f"Bearer {token}",
                        "X-GitHub-Api-Version": GITHUB_API_VERSION,
                    },
                    json=json,
                    params=params,
                    timeout=timeout_seconds,
                    verify=default_ca_bundle_path(),
                )
            except requests.RequestException as exc:
                raise GitHubAPIError(f"GitHub request failed: {exc}") from exc

            self.status_code = response.status_code
            if response.status_code == 401 and attempt == 0:
                self.token_provider.invalidate(
                    repositories=repositories,
                    permissions=permissions,
                )
                continue
            if not 200 <= response.status_code < 300:
                raise GitHubAPIError(_github_error_message(response))
            if not getattr(response, "content", b""):
                return {}
            try:
                return response.json()
            except ValueError as exc:
                raise GitHubAPIError("GitHub response was not valid JSON") from exc
        raise GitHubAPIError("GitHub request failed after refreshing its token")


def _token_cache_key(
    *,
    repositories: Sequence[str] | None,
    permissions: Mapping[str, str] | None,
) -> _TokenCacheKey:
    normalized_repositories = (
        tuple(sorted({repository.strip().casefold() for repository in repositories}))
        if repositories is not None
        else None
    )
    normalized_permissions = tuple(
        sorted(
            (str(name).strip(), str(level).strip())
            for name, level in (permissions or {}).items()
            if str(name).strip() and str(level).strip()
        )
    )
    return normalized_repositories, normalized_permissions


def _installation_repository_names(
    repositories: Sequence[str] | None,
) -> list[str] | None:
    if repositories is None:
        return None
    names: list[str] = []
    for repository in repositories:
        normalized = repository.strip().strip("/")
        if not normalized:
            continue
        names.append(normalized.rsplit("/", 1)[-1])
    return sorted(set(names))


def _parse_github_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _issue_state(value: str) -> str:
    normalized = value.strip().casefold()
    if normalized not in {"open", "closed", "all"}:
        raise ValueError("GitHub issue state must be open, closed, or all")
    return normalized


def _issue_matches_query(issue: dict[str, Any], query: str) -> bool:
    if not query:
        return True
    searchable = " ".join(
        str(value)
        for value in (
            issue.get("number", ""),
            issue.get("title", ""),
            issue.get("body", ""),
        )
        if value is not None
    ).casefold()
    return query in searchable


def _collection_from_payload(payload: Any, *, key: str) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _github_error_message(response: requests.Response) -> str:
    message = getattr(response, "text", "").strip() or "Unknown error"
    return f"GitHub request failed with status {response.status_code}: {message}"
