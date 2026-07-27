"""Outline API client helpers shared across services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests


OUTLINE_BASE_URL = "https://app.getoutline.com"
OUTLINE_SEARCH_RESULT_LIMIT = 10


@dataclass(frozen=True, slots=True)
class OutlineDocumentSummary:
    """The safe document fields needed for Discord wiki rendering."""

    id: str
    title: str
    url: str
    updated_at: str | None


@dataclass(frozen=True, slots=True)
class OutlineSearchResult:
    """One keyword-search result with a short context excerpt."""

    document: OutlineDocumentSummary
    context: str | None
    ranking: float | None


class OutlineAPIError(RuntimeError):
    """Raised when the Outline API request fails or returns invalid data."""


def normalize_outline_api_base_url(base_url: str) -> str:
    """Normalize an Outline root or API URL to the RPC API base."""
    normalized = base_url.strip().rstrip("/")
    if not normalized:
        raise OutlineAPIError("Outline base URL must not be empty.")

    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise OutlineAPIError("Outline base URL must be an absolute HTTP(S) URL.")

    path = parsed.path.rstrip("/")
    if path.endswith("/api"):
        api_path = path
    else:
        api_path = f"{path}/api" if path else "/api"

    return urlunsplit((parsed.scheme, parsed.netloc, api_path, "", ""))


def normalize_outline_web_base_url(base_url: str) -> str:
    """Normalize an Outline root or API URL to the browser-facing base URL."""
    api_url = normalize_outline_api_base_url(base_url)
    parsed = urlsplit(api_url)
    api_path = parsed.path.rstrip("/")
    if not api_path.endswith("/api"):  # pragma: no cover - defensive invariant
        raise OutlineAPIError("Outline API URL must end in /api.")

    web_path = api_path[: -len("/api")].rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, web_path, "", ""))


class OutlineClient:
    """Small Outline RPC API wrapper for invitations and read-only wiki access."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = OUTLINE_BASE_URL,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = normalize_outline_api_base_url(base_url)
        self.web_base_url = normalize_outline_web_base_url(base_url)
        self.timeout_seconds = timeout_seconds

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def request(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Call one Outline RPC method and return the JSON response."""
        try:
            response = requests.post(
                f"{self.base_url}/{method.lstrip('/')}",
                headers=self._headers(),
                json=payload,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise OutlineAPIError(f"Outline API request failed: {exc}") from exc

        if not 200 <= response.status_code < 300:
            raise OutlineAPIError(
                f"Outline API request failed: status={response.status_code}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise OutlineAPIError(
                "Outline response payload must be valid JSON."
            ) from exc

        if not isinstance(data, dict):
            raise OutlineAPIError("Outline response payload must be a JSON object.")

        if data.get("ok") is False:
            raise OutlineAPIError("Outline API returned an error.")

        return data

    def invite_user(
        self,
        *,
        email: str,
        name: str | None = None,
        role: str | None = None,
        suppress_email: bool = False,
    ) -> dict[str, Any]:
        """Invite one user to Outline and return the response payload."""
        invite: dict[str, Any] = {"email": email}
        if name:
            invite["name"] = name
        if role:
            invite["role"] = role

        return self.request(
            "users.invite",
            {
                "invites": [invite],
                "suppressEmail": suppress_email,
            },
        )

    def search_documents(
        self,
        *,
        query: str,
        limit: int = 5,
    ) -> list[OutlineSearchResult]:
        """Search published documents visible to this API key's owner."""
        normalized_query = " ".join(query.split())
        if not normalized_query:
            raise ValueError("Outline search query must not be empty.")

        normalized_limit = min(max(int(limit), 1), OUTLINE_SEARCH_RESULT_LIMIT)
        response = self.request(
            "documents.search",
            {
                "query": normalized_query,
                "limit": normalized_limit,
                "offset": 0,
                "statusFilter": ["published"],
                "snippetMinWords": 12,
                "snippetMaxWords": 30,
            },
        )
        raw_results = response.get("data")
        if not isinstance(raw_results, list):
            raise OutlineAPIError("Outline search payload must include a result list.")

        results: list[OutlineSearchResult] = []
        for raw_result in raw_results:
            if not isinstance(raw_result, dict):
                continue
            raw_document = raw_result.get("document")
            if not isinstance(raw_document, dict):
                continue
            document = self._document_summary(raw_document)
            if document is None:
                continue

            raw_context = raw_result.get("context")
            context = raw_context.strip() if isinstance(raw_context, str) else None
            raw_ranking = raw_result.get("ranking")
            ranking = (
                float(raw_ranking)
                if isinstance(raw_ranking, (int, float))
                and not isinstance(raw_ranking, bool)
                else None
            )
            results.append(
                OutlineSearchResult(
                    document=document,
                    context=context or None,
                    ranking=ranking,
                )
            )

        return results[:normalized_limit]

    def list_starred_documents(
        self,
        *,
        limit: int = 6,
    ) -> list[OutlineDocumentSummary]:
        """Return the authenticated integration account's starred documents."""
        normalized_limit = min(max(int(limit), 1), OUTLINE_SEARCH_RESULT_LIMIT)
        response = self.request(
            "stars.list",
            {"limit": normalized_limit, "offset": 0},
        )
        raw_data = response.get("data")
        if not isinstance(raw_data, dict):
            raise OutlineAPIError("Outline stars payload must include an object.")

        raw_documents = raw_data.get("documents")
        if not isinstance(raw_documents, list):
            raise OutlineAPIError("Outline stars payload must include documents.")

        documents_by_id: dict[str, OutlineDocumentSummary] = {}
        for raw_document in raw_documents:
            if not isinstance(raw_document, dict):
                continue
            document = self._document_summary(raw_document)
            if document is not None:
                documents_by_id[document.id] = document

        raw_stars = raw_data.get("stars")
        if not isinstance(raw_stars, list):
            return list(documents_by_id.values())[:normalized_limit]

        documents: list[OutlineDocumentSummary] = []
        seen_ids: set[str] = set()
        for raw_star in raw_stars:
            if not isinstance(raw_star, dict):
                continue
            document_id = str(raw_star.get("documentId") or "").strip()
            document = documents_by_id.get(document_id)
            if document is None or document.id in seen_ids:
                continue
            documents.append(document)
            seen_ids.add(document.id)

        return documents[:normalized_limit]

    def _document_summary(
        self,
        raw_document: dict[str, Any],
    ) -> OutlineDocumentSummary | None:
        document_id = str(raw_document.get("id") or "").strip()
        raw_url = raw_document.get("url")
        if not document_id or not isinstance(raw_url, str):
            return None

        url = self._document_url(raw_url)
        if url is None:
            return None

        title = str(raw_document.get("title") or "").strip() or "Untitled document"
        raw_updated_at = raw_document.get("updatedAt")
        updated_at = raw_updated_at.strip() if isinstance(raw_updated_at, str) else None
        return OutlineDocumentSummary(
            id=document_id,
            title=title,
            url=url,
            updated_at=updated_at or None,
        )

    def _document_url(self, raw_url: str) -> str | None:
        """Build an absolute same-instance URL from Outline's document path."""
        parsed_url = urlsplit(raw_url.strip())
        web_base = urlsplit(self.web_base_url)
        if parsed_url.scheme or parsed_url.netloc:
            if (
                parsed_url.scheme != web_base.scheme
                or parsed_url.netloc != web_base.netloc
            ):
                return None

        path = parsed_url.path.strip()
        if not path:
            return None

        base_path = web_base.path.rstrip("/")
        document_path = path.lstrip("/")
        joined_path = (
            f"{base_path}/{document_path}" if base_path else f"/{document_path}"
        )
        return urlunsplit((web_base.scheme, web_base.netloc, joined_path, "", ""))
