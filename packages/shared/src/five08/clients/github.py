"""Small GitHub REST client used by deterministic agent tools."""

from __future__ import annotations

from typing import Any

import requests

from five08.tls import default_ca_bundle_path


class GitHubAPIError(RuntimeError):
    """Raised when a GitHub API request fails."""


class GitHubClient:
    """Minimal GitHub Issues API wrapper."""

    def __init__(
        self,
        *,
        token: str,
        base_url: str = "https://api.github.com",
        timeout_seconds: float = 20.0,
    ) -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
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
        return self._request("POST", f"/repos/{repository}/issues", json=payload)

    def search_issues(
        self,
        *,
        repository: str,
        query: str,
        state: str = "open",
        limit: int = 10,
    ) -> dict[str, Any]:
        search_terms = [f"repo:{repository}", "is:issue"]
        if state:
            search_terms.append(f"state:{state}")
        if query:
            search_terms.append(query)
        payload = self._request(
            "GET",
            "/search/issues",
            params={"q": " ".join(search_terms), "per_page": min(max(limit, 1), 20)},
        )
        items = payload.get("items")
        if not isinstance(items, list):
            items = []
        return {"issues": items[:limit], "total_count": payload.get("total_count", 0)}

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            response = requests.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                json=json,
                params=params,
                timeout=self.timeout_seconds,
                verify=default_ca_bundle_path(),
            )
        except requests.RequestException as exc:
            raise GitHubAPIError(f"GitHub request failed: {exc}") from exc

        self.status_code = response.status_code
        if not 200 <= response.status_code < 300:
            message = response.text.strip() or "Unknown error"
            raise GitHubAPIError(
                f"GitHub request failed with status {response.status_code}: {message}"
            )
        if not response.content:
            return {}
        try:
            payload = response.json()
        except ValueError as exc:
            raise GitHubAPIError("GitHub response was not valid JSON") from exc
        if not isinstance(payload, dict):
            raise GitHubAPIError("GitHub response was not a JSON object")
        return payload
