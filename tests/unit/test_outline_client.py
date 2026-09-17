"""Unit tests for the shared Outline API client."""

from unittest.mock import Mock, patch

import pytest
import requests

from five08.clients.outline import (
    OutlineAPIError,
    OutlineClient,
    OutlineConflictError,
    OutlineDocument,
    normalize_outline_api_base_url,
    normalize_outline_web_base_url,
)
from five08.tls import default_ca_bundle_path


def test_normalize_outline_api_base_url_accepts_root_url() -> None:
    assert (
        normalize_outline_api_base_url("https://outline.example.com/")
        == "https://outline.example.com/api"
    )


def test_normalize_outline_api_base_url_accepts_api_url() -> None:
    assert (
        normalize_outline_api_base_url("https://outline.example.com/api/")
        == "https://outline.example.com/api"
    )


def test_normalize_outline_web_base_url_accepts_api_url() -> None:
    assert (
        normalize_outline_web_base_url("https://outline.example.com/wiki/api/")
        == "https://outline.example.com/wiki"
    )


def test_invite_user_posts_outline_rpc_payload() -> None:
    response = Mock()
    response.status_code = 200
    response.json.return_value = {
        "ok": True,
        "data": {
            "sent": [{"email": "jane@508.dev", "name": "Jane Doe"}],
            "users": [],
        },
    }

    with patch("five08.clients.outline.requests.post", return_value=response) as post:
        result = OutlineClient(
            api_key="outline-key",
            base_url="https://outline.example.com/",
            timeout_seconds=7.0,
        ).invite_user(email="jane@508.dev", name="Jane Doe")

    post.assert_called_once_with(
        "https://outline.example.com/api/users.invite",
        headers={
            "Accept": "application/json",
            "Authorization": "Bearer outline-key",
            "Content-Type": "application/json",
        },
        json={
            "invites": [{"email": "jane@508.dev", "name": "Jane Doe"}],
            "suppressEmail": False,
        },
        timeout=7.0,
        verify=default_ca_bundle_path(),
    )
    assert result["ok"] is True


def test_invite_user_raises_on_http_error() -> None:
    response = Mock()
    response.status_code = 403
    response.text = "Forbidden"

    with patch("five08.clients.outline.requests.post", return_value=response):
        with pytest.raises(OutlineAPIError, match="status=403") as error:
            OutlineClient(api_key="outline-key").invite_user(email="jane@508.dev")

    assert "Forbidden" not in str(error.value)


def test_invite_user_raises_on_request_error() -> None:
    with patch(
        "five08.clients.outline.requests.post",
        side_effect=requests.Timeout("timed out"),
    ):
        with pytest.raises(OutlineAPIError, match="request failed"):
            OutlineClient(api_key="outline-key").invite_user(email="jane@508.dev")


def test_search_documents_posts_published_search_payload() -> None:
    response = Mock()
    response.status_code = 200
    response.json.return_value = {
        "ok": True,
        "data": [
            {
                "context": "<b>Invoice</b> submissions are due on Friday.",
                "ranking": 1.5,
                "document": {
                    "id": "doc-1",
                    "title": "Invoice process",
                    "url": "/doc/invoice-process-abc123",
                    "updatedAt": "2026-07-20T12:00:00.000Z",
                },
            }
        ],
    }

    with patch("five08.clients.outline.requests.post", return_value=response) as post:
        results = OutlineClient(
            api_key="wiki-key",
            base_url="https://outline.example.com/",
        ).search_documents(query="  invoice   due  ", limit=5)

    post.assert_called_once_with(
        "https://outline.example.com/api/documents.search",
        headers={
            "Accept": "application/json",
            "Authorization": "Bearer wiki-key",
            "Content-Type": "application/json",
        },
        json={
            "query": "invoice due",
            "limit": 5,
            "offset": 0,
            "statusFilter": ["published"],
            "snippetMinWords": 12,
            "snippetMaxWords": 30,
        },
        timeout=20.0,
        verify=default_ca_bundle_path(),
    )
    assert len(results) == 1
    assert results[0].context == "<b>Invoice</b> submissions are due on Friday."
    assert results[0].ranking == 1.5
    assert results[0].document.title == "Invoice process"
    assert (
        results[0].document.url
        == "https://outline.example.com/doc/invoice-process-abc123"
    )


def test_search_documents_ignores_malformed_or_external_document_urls() -> None:
    response = Mock()
    response.status_code = 200
    response.json.return_value = {
        "ok": True,
        "data": [
            {"document": {"id": "missing-url", "title": "Missing URL"}},
            {
                "document": {
                    "id": "external-url",
                    "title": "External URL",
                    "url": "https://attacker.example/doc/secret",
                }
            },
        ],
    }

    with patch("five08.clients.outline.requests.post", return_value=response):
        results = OutlineClient(api_key="wiki-key").search_documents(query="secret")

    assert results == []


def test_search_documents_preserves_same_instance_absolute_urls() -> None:
    response = Mock()
    response.status_code = 200
    response.json.return_value = {
        "ok": True,
        "data": [
            {
                "document": {
                    "id": "doc-1",
                    "title": "Member handbook",
                    "url": (
                        "https://outline.example.com/wiki/doc/member-handbook-abc123"
                        "?source=search"
                    ),
                }
            }
        ],
    }

    with patch("five08.clients.outline.requests.post", return_value=response):
        results = OutlineClient(
            api_key="wiki-key",
            base_url="https://outline.example.com/wiki/api",
        ).search_documents(query="handbook")

    assert results[0].document.url == (
        "https://outline.example.com/wiki/doc/member-handbook-abc123"
    )


def test_list_starred_documents_keeps_outline_star_order() -> None:
    response = Mock()
    response.status_code = 200
    response.json.return_value = {
        "ok": True,
        "data": {
            "stars": [{"documentId": "doc-2"}, {"documentId": "doc-1"}],
            "documents": [
                {
                    "id": "doc-1",
                    "title": "Member handbook",
                    "url": "/doc/member-handbook-abc123",
                },
                {
                    "id": "doc-2",
                    "title": "Invoice process",
                    "url": "/doc/invoice-process-def456",
                },
            ],
        },
    }

    with patch("five08.clients.outline.requests.post", return_value=response) as post:
        documents = OutlineClient(
            api_key="wiki-key",
            base_url="https://outline.example.com/wiki/api",
        ).list_starred_documents(limit=6)

    post.assert_called_once_with(
        "https://outline.example.com/wiki/api/stars.list",
        headers={
            "Accept": "application/json",
            "Authorization": "Bearer wiki-key",
            "Content-Type": "application/json",
        },
        json={"limit": 6, "offset": 0},
        timeout=20.0,
        verify=default_ca_bundle_path(),
    )
    assert [document.id for document in documents] == ["doc-2", "doc-1"]
    assert (
        documents[0].url
        == "https://outline.example.com/wiki/doc/invoice-process-def456"
    )


def test_search_documents_rejects_empty_query_without_calling_outline() -> None:
    client = OutlineClient(api_key="wiki-key")
    with pytest.raises(ValueError, match="must not be empty"):
        client.search_documents(query="   ")


def test_get_document_returns_a_typed_full_document() -> None:
    response = Mock()
    response.status_code = 200
    response.json.return_value = {
        "ok": True,
        "data": {
            "id": "doc-1",
            "title": "Member handbook",
            "text": "# Welcome\n\nThis is the complete article.",
            "url": "/doc/member-handbook-abc123",
            "collectionId": "collection-1",
            "parentDocumentId": "parent-1",
            "revision": 4,
            "updatedAt": "2026-09-17T12:00:00.000Z",
        },
    }

    with patch("five08.clients.outline.requests.post", return_value=response) as post:
        document = OutlineClient(
            api_key="writer-key",
            base_url="https://outline.example.com/wiki/api",
        ).get_document(document_id=" doc-1 ")

    post.assert_called_once_with(
        "https://outline.example.com/wiki/api/documents.info",
        headers={
            "Accept": "application/json",
            "Authorization": "Bearer writer-key",
            "Content-Type": "application/json",
        },
        json={"id": "doc-1"},
        timeout=20.0,
        verify=default_ca_bundle_path(),
    )
    assert isinstance(document, OutlineDocument)
    assert document.id == "doc-1"
    assert document.text == "# Welcome\n\nThis is the complete article."
    assert document.collection_id == "collection-1"
    assert document.parent_document_id == "parent-1"
    assert document.revision == 4
    assert document.updated_at == "2026-09-17T12:00:00.000Z"
    assert document.url == "https://outline.example.com/wiki/doc/member-handbook-abc123"


def test_create_document_posts_explicit_publish_payload() -> None:
    response = Mock()
    response.status_code = 200
    response.json.return_value = {
        "ok": True,
        "data": {
            "id": "doc-2",
            "title": "Wiki writing workflow",
            "text": "# Workflow\n\nApproved content.",
            "url": "/doc/wiki-writing-workflow-def456",
            "collectionId": "collection-1",
            "parentDocumentId": "parent-1",
            "revision": 1,
        },
    }

    with patch("five08.clients.outline.requests.post", return_value=response) as post:
        document = OutlineClient(api_key="writer-key").create_document(
            title=" Wiki writing workflow ",
            text="# Workflow\n\nApproved content.",
            collection_id=" collection-1 ",
            parent_document_id=" parent-1 ",
            publish=True,
        )

    post.assert_called_once_with(
        "https://app.getoutline.com/api/documents.create",
        headers={
            "Accept": "application/json",
            "Authorization": "Bearer writer-key",
            "Content-Type": "application/json",
        },
        json={
            "title": "Wiki writing workflow",
            "text": "# Workflow\n\nApproved content.",
            "publish": True,
            "collectionId": "collection-1",
            "parentDocumentId": "parent-1",
        },
        timeout=20.0,
        verify=default_ca_bundle_path(),
    )
    assert document.id == "doc-2"
    assert document.revision == 1


def test_update_document_forwards_optimistic_revision_guard() -> None:
    response = Mock()
    response.status_code = 200
    response.json.return_value = {
        "ok": True,
        "data": {
            "id": "doc-1",
            "title": "Member handbook",
            "text": "# Welcome\n\nUpdated article.",
            "url": "/doc/member-handbook-abc123",
            "collectionId": "collection-1",
            "revision": 5,
        },
    }

    with patch("five08.clients.outline.requests.post", return_value=response) as post:
        document = OutlineClient(api_key="writer-key").update_document(
            document_id="doc-1",
            text="# Welcome\n\nUpdated article.",
            publish=True,
            expected_revision=4,
        )

    post.assert_called_once_with(
        "https://app.getoutline.com/api/documents.update",
        headers={
            "Accept": "application/json",
            "Authorization": "Bearer writer-key",
            "Content-Type": "application/json",
        },
        json={
            "id": "doc-1",
            "text": "# Welcome\n\nUpdated article.",
            "publish": True,
            "lastRevision": 4,
        },
        timeout=20.0,
        verify=default_ca_bundle_path(),
    )
    assert document.revision == 5


def test_update_document_requires_a_change_without_calling_outline() -> None:
    client = OutlineClient(api_key="writer-key")

    with patch("five08.clients.outline.requests.post") as post:
        with pytest.raises(ValueError, match="requires at least one change"):
            client.update_document(document_id="doc-1", expected_revision=4)

    post.assert_not_called()


def test_create_document_requires_a_destination_without_calling_outline() -> None:
    client = OutlineClient(api_key="writer-key")

    with patch("five08.clients.outline.requests.post") as post:
        with pytest.raises(ValueError, match="collection ID or parent document ID"):
            client.create_document(title="Draft", text="Draft text")

    post.assert_not_called()


def test_get_document_rejects_external_document_url() -> None:
    response = Mock()
    response.status_code = 200
    response.json.return_value = {
        "ok": True,
        "data": {
            "id": "doc-1",
            "title": "Member handbook",
            "text": "Complete article.",
            "url": "https://attacker.example/doc/member-handbook-abc123",
        },
    }

    with patch("five08.clients.outline.requests.post", return_value=response):
        with pytest.raises(OutlineAPIError, match="same-instance URL"):
            OutlineClient(api_key="writer-key").get_document(document_id="doc-1")


def test_update_document_raises_a_typed_conflict_error() -> None:
    response = Mock()
    response.status_code = 409
    response.text = "The document was changed elsewhere."

    with patch("five08.clients.outline.requests.post", return_value=response):
        with pytest.raises(OutlineConflictError) as error:
            OutlineClient(api_key="writer-key").update_document(
                document_id="doc-1",
                text="Updated article.",
                expected_revision=4,
            )

    assert "changed elsewhere" not in str(error.value)
