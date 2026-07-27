"""Unit tests for the shared Outline API client."""

from unittest.mock import Mock, patch

import pytest
import requests

from five08.clients.outline import (
    OutlineAPIError,
    OutlineClient,
    normalize_outline_api_base_url,
    normalize_outline_web_base_url,
)


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
