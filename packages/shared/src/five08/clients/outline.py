"""Outline API client helpers shared across services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests

from five08.tls import default_ca_bundle_path


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
class OutlineDocument:
    """A complete Outline document suitable for an approved write workflow."""

    id: str
    title: str
    text: str
    url: str
    collection_id: str | None
    parent_document_id: str | None
    revision: int | None
    updated_at: str | None


@dataclass(frozen=True, slots=True)
class OutlineSearchResult:
    """One keyword-search result with a short context excerpt."""

    document: OutlineDocumentSummary
    context: str | None
    ranking: float | None


class OutlineAPIError(RuntimeError):
    """Raised when the Outline API request fails or returns invalid data."""


class OutlineConflictError(OutlineAPIError):
    """Raised when Outline rejects a write against a newer document revision."""


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
    """Small Outline RPC API wrapper for invitations and wiki access."""

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
                verify=default_ca_bundle_path(),
            )
        except requests.RequestException as exc:
            raise OutlineAPIError(f"Outline API request failed: {exc}") from exc

        if not 200 <= response.status_code < 300:
            if response.status_code == 409:
                raise OutlineConflictError(
                    "Outline API request conflicted with a newer document revision."
                )
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

    def get_document(self, *, document_id: str) -> OutlineDocument:
        """Return one complete document, including its Markdown text and revision."""
        return self._response_document(
            self.request(
                "documents.info",
                {"id": self._required_identifier(document_id, "document ID")},
            )
        )

    def create_document(
        self,
        *,
        title: str,
        text: str,
        collection_id: str | None = None,
        parent_document_id: str | None = None,
        publish: bool = False,
    ) -> OutlineDocument:
        """Create one document in a collection or under a parent document.

        New documents are drafts by default. Callers that have completed their
        own approval and permission checks must explicitly pass ``publish=True``.
        """
        normalized_collection_id = self._optional_identifier(
            collection_id,
            "collection ID",
        )
        normalized_parent_document_id = self._optional_identifier(
            parent_document_id,
            "parent document ID",
        )
        if normalized_collection_id is None and normalized_parent_document_id is None:
            raise ValueError(
                "Outline document creation requires a collection ID or parent document ID."
            )

        payload: dict[str, Any] = {
            "title": self._required_identifier(title, "document title"),
            "text": self._document_text(text),
            "publish": self._publish_value(publish),
        }
        if normalized_collection_id is not None:
            payload["collectionId"] = normalized_collection_id
        if normalized_parent_document_id is not None:
            payload["parentDocumentId"] = normalized_parent_document_id

        return self._response_document(self.request("documents.create", payload))

    def update_document(
        self,
        *,
        document_id: str,
        title: str | None = None,
        text: str | None = None,
        publish: bool | None = None,
        expected_revision: int | None = None,
    ) -> OutlineDocument:
        """Update a document, optionally guarding against a stale revision.

        Supplying ``text`` replaces the document's complete Markdown body. Pass
        the ``revision`` returned by :meth:`get_document` as
        ``expected_revision`` to have Outline reject a concurrent update.
        """
        payload: dict[str, Any] = {
            "id": self._required_identifier(document_id, "document ID"),
        }
        has_change = False
        if title is not None:
            payload["title"] = self._required_identifier(title, "document title")
            has_change = True
        if text is not None:
            payload["text"] = self._document_text(text)
            has_change = True
        if publish is not None:
            payload["publish"] = self._publish_value(publish)
            has_change = True
        if expected_revision is not None:
            payload["lastRevision"] = self._expected_revision(expected_revision)

        if not has_change:
            raise ValueError("Outline document update requires at least one change.")

        return self._response_document(self.request("documents.update", payload))

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

    def _response_document(self, response: dict[str, Any]) -> OutlineDocument:
        """Validate the document object returned by an Outline document endpoint."""
        raw_document = response.get("data")
        if isinstance(raw_document, dict) and isinstance(
            raw_document.get("document"), dict
        ):
            raw_document = raw_document["document"]
        if not isinstance(raw_document, dict):
            raise OutlineAPIError("Outline document payload must include an object.")

        document_id = self._response_identifier(raw_document, "id", "ID")
        raw_url = raw_document.get("url")
        if not isinstance(raw_url, str):
            raise OutlineAPIError("Outline document payload must include a URL.")
        url = self._document_url(raw_url)
        if url is None:
            raise OutlineAPIError(
                "Outline document payload must include a same-instance URL."
            )

        raw_text = raw_document.get("text")
        if not isinstance(raw_text, str):
            raise OutlineAPIError(
                "Outline document payload must include Markdown document text."
            )

        title = str(raw_document.get("title") or "").strip() or "Untitled document"
        raw_revision = raw_document.get("revision")
        if raw_revision is None:
            revision = None
        elif isinstance(raw_revision, int) and not isinstance(raw_revision, bool):
            revision = raw_revision
        else:
            raise OutlineAPIError(
                "Outline document payload revision must be an integer when present."
            )

        return OutlineDocument(
            id=document_id,
            title=title,
            text=raw_text,
            url=url,
            collection_id=self._optional_response_identifier(
                raw_document,
                "collectionId",
                "collection ID",
            ),
            parent_document_id=self._optional_response_identifier(
                raw_document,
                "parentDocumentId",
                "parent document ID",
            ),
            revision=revision,
            updated_at=self._optional_response_text(raw_document, "updatedAt"),
        )

    @staticmethod
    def _required_identifier(value: str, label: str) -> str:
        if not isinstance(value, str) or not (normalized := value.strip()):
            raise ValueError(f"Outline {label} must not be empty.")
        return normalized

    @classmethod
    def _optional_identifier(cls, value: str | None, label: str) -> str | None:
        if value is None:
            return None
        return cls._required_identifier(value, label)

    @staticmethod
    def _document_text(value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("Outline document text must be a string.")
        return value

    @staticmethod
    def _publish_value(value: bool) -> bool:
        if not isinstance(value, bool):
            raise ValueError("Outline publish must be a boolean.")
        return value

    @staticmethod
    def _expected_revision(value: int) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(
                "Outline expected document revision must be a non-negative integer."
            )
        return value

    @staticmethod
    def _response_identifier(
        raw_document: dict[str, Any],
        key: str,
        label: str,
    ) -> str:
        raw_value = raw_document.get(key)
        if not isinstance(raw_value, str) or not (value := raw_value.strip()):
            raise OutlineAPIError(
                f"Outline document payload must include a non-empty {label}."
            )
        return value

    @classmethod
    def _optional_response_identifier(
        cls,
        raw_document: dict[str, Any],
        key: str,
        label: str,
    ) -> str | None:
        raw_value = raw_document.get(key)
        if raw_value is None:
            return None
        return cls._response_identifier(raw_document, key, label)

    @staticmethod
    def _optional_response_text(
        raw_document: dict[str, Any],
        key: str,
    ) -> str | None:
        raw_value = raw_document.get(key)
        if raw_value is None:
            return None
        if not isinstance(raw_value, str):
            raise OutlineAPIError(
                f"Outline document payload {key} must be a string when present."
            )
        return raw_value.strip() or None

    def _document_url(self, raw_url: str) -> str | None:
        """Build an absolute same-instance URL from Outline's document path."""
        parsed_url = urlsplit(raw_url.strip())
        web_base = urlsplit(self.web_base_url)
        is_absolute = bool(parsed_url.scheme or parsed_url.netloc)
        if is_absolute:
            if (
                parsed_url.scheme != web_base.scheme
                or parsed_url.netloc != web_base.netloc
            ):
                return None

        path = parsed_url.path.strip()
        if not path:
            return None

        if is_absolute:
            joined_path = path
        else:
            base_path = web_base.path.rstrip("/")
            document_path = path.lstrip("/")
            joined_path = (
                f"{base_path}/{document_path}" if base_path else f"/{document_path}"
            )
        return urlunsplit((web_base.scheme, web_base.netloc, joined_path, "", ""))
