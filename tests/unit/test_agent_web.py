"""Unit tests for bounded multi-provider agent web research adapters."""

from __future__ import annotations

import json
import socket
from unittest.mock import Mock, patch

import pytest
import requests

import five08.agent.web as agent_web
from five08.agent.web import (
    MAX_WEB_EXTRACT_CONTENT_CHARS,
    MAX_WEB_RESPONSE_BYTES,
    BraveWebSearch,
    FirecrawlWebResearch,
    SearxngWebSearch,
    WebExtractResult,
    WebResearchClient,
    WebResearchConfigurationError,
    WebResearchResponseError,
    WebResearchTransportError,
    WebResearchUnavailableError,
    WebResearchValidationError,
    WebSearchResponse,
    normalize_provider_base_url,
    validate_public_https_url,
)
from five08.agent.tools import ToolRegistry
from five08.tls import default_ca_bundle_path


def _response(payload: object, *, status_code: int = 200) -> Mock:
    response = Mock()
    response.status_code = status_code
    encoded = json.dumps(payload).encode("utf-8")
    response.headers = {"Content-Length": str(len(encoded))}
    response.iter_content.return_value = [encoded]
    return response


def test_searxng_search_parses_bounded_deduplicated_results() -> None:
    response = _response(
        {
            "results": [
                {
                    "title": "  First result ",
                    "url": "https://example.com/first#fragment",
                    "content": "Useful\n summary",
                    "publishedDate": "2026-07-01",
                    "engine": "brave",
                },
                {
                    "title": "Duplicate",
                    "url": "https://example.com/first",
                    "content": "Should be removed",
                },
                {
                    "title": "Unsafe scheme",
                    "url": "file:///etc/passwd",
                    "content": "Must be removed",
                },
                {
                    "title": "Private target",
                    "url": "http://127.0.0.1/internal",
                    "content": "Must be removed",
                },
                {
                    "url": "https://example.com/fallback-title",
                    "content": "Second result",
                },
            ]
        }
    )

    with patch("five08.agent.web.requests.get", return_value=response) as get:
        result = SearxngWebSearch(
            base_url="http://searxng.internal:8080/base/",
            language="en-US",
        ).search("  current  grants  ", limit=2)

    assert result.provider == "searxng"
    assert [item.url for item in result.results] == [
        "https://example.com/first",
        "https://example.com/fallback-title",
    ]
    assert result.results[0].title == "First result"
    assert result.results[0].snippet == "Useful summary"
    assert result.results[0].published_at == "2026-07-01"
    assert result.results[0].source == "brave"
    assert result.results[1].title == "example.com"
    get.assert_called_once_with(
        "http://searxng.internal:8080/base/search",
        params={
            "q": "current grants",
            "format": "json",
            "categories": "general",
            "language": "en-US",
        },
        headers={"Accept": "application/json"},
        timeout=15.0,
        verify=default_ca_bundle_path(),
        allow_redirects=False,
        stream=True,
    )


def test_brave_search_uses_subscription_header_and_parses_profile() -> None:
    response = _response(
        {
            "web": {
                "results": [
                    {
                        "title": "Brave result",
                        "url": "https://example.com/article",
                        "description": "Useful description",
                        "page_age": "2026-07-01",
                        "profile": {"long_name": "Example Publications"},
                    }
                ]
            }
        }
    )

    with patch("five08.agent.web.requests.get", return_value=response) as get:
        result = BraveWebSearch(
            api_key="brave-token",
            country="US",
            search_language="en",
        ).search("grants", limit=3)

    assert result.results[0].source == "Example Publications"
    assert result.results[0].published_at == "2026-07-01"
    get.assert_called_once_with(
        "https://api.search.brave.com/res/v1/web/search",
        params={
            "q": "grants",
            "count": 3,
            "safesearch": "moderate",
            "country": "US",
            "search_lang": "en",
        },
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": "brave-token",
        },
        timeout=15.0,
        verify=default_ca_bundle_path(),
        allow_redirects=False,
        stream=True,
    )


def test_brave_empty_web_response_is_a_valid_empty_result() -> None:
    with patch("five08.agent.web.requests.get", return_value=_response({"web": None})):
        result = BraveWebSearch(api_key="brave-token").search("nothing")

    assert result.results == ()


def test_firecrawl_search_parses_metadata_source_url() -> None:
    response = _response(
        {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "Firecrawl result",
                        "description": "Sourced result",
                        "metadata": {
                            "sourceURL": "https://example.com/firecrawl-result"
                        },
                    }
                ]
            },
        }
    )

    with patch("five08.agent.web.requests.post", return_value=response) as post:
        result = FirecrawlWebResearch(api_key="firecrawl-token").search(
            "fresh policy", limit=2
        )

    assert result.results[0].url == "https://example.com/firecrawl-result"
    assert result.results[0].snippet == "Sourced result"
    post.assert_called_once_with(
        "https://api.firecrawl.dev/v2/search",
        headers={
            "Accept": "application/json",
            "Authorization": "Bearer firecrawl-token",
            "Content-Type": "application/json",
        },
        json={"query": "fresh policy", "limit": 2, "sources": ["web"]},
        timeout=15.0,
        verify=default_ca_bundle_path(),
        allow_redirects=False,
        stream=True,
    )


def test_firecrawl_extract_validates_target_and_bounds_markdown(monkeypatch) -> None:
    response = _response(
        {
            "success": True,
            "data": {
                "markdown": "x" * (MAX_WEB_EXTRACT_CONTENT_CHARS + 100),
                "metadata": {
                    "title": "Example page",
                    "description": "A public page",
                    "sourceURL": "https://example.com/final",
                    "statusCode": 200,
                },
            },
        }
    )
    monkeypatch.setattr(
        "five08.agent.web.socket.getaddrinfo",
        lambda *_args, **_kwargs: [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", 443),
            )
        ],
    )

    with patch("five08.agent.web.requests.post", return_value=response) as post:
        result = FirecrawlWebResearch(api_key="firecrawl-token").extract(
            "https://example.com/article#ignored"
        )

    assert result.provider == "firecrawl"
    assert result.url == "https://example.com/final"
    assert result.title == "Example page"
    assert len(result.content) == MAX_WEB_EXTRACT_CONTENT_CHARS
    assert result.content.endswith("…")
    assert result.metadata == {
        "description": "A public page",
        "sourceURL": "https://example.com/final",
        "statusCode": "200",
    }
    body = post.call_args.kwargs["json"]
    assert body == {
        "url": "https://example.com/article",
        "formats": ["markdown"],
        "onlyMainContent": True,
        "removeBase64Images": True,
        "blockAds": True,
        "skipTlsVerification": False,
        "storeInCache": False,
        "timeout": 15000,
    }


def test_firecrawl_full_search_endpoint_base_is_reused_for_scrape() -> None:
    response = _response({"success": True, "data": {"markdown": "content"}})

    with (
        patch(
            "five08.agent.web.validate_public_https_url",
            return_value="https://example.com/article",
        ),
        patch("five08.agent.web.requests.post", return_value=response) as post,
    ):
        FirecrawlWebResearch(
            api_key="firecrawl-token",
            base_url="https://api.firecrawl.dev/v2/search",
        ).extract("https://example.com/article")

    assert post.call_args.args[0] == "https://api.firecrawl.dev/v2/scrape"


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://localhost/internal",
        "https://127.0.0.1/internal",
        "https://169.254.169.254/latest/meta-data",
        "https://example.com:8443",
        "https://example.com/article?access_token=secret",
        "https://user:password@example.com/",
    ],
)
def test_validate_public_https_url_rejects_unsafe_targets(url: str) -> None:
    with pytest.raises(WebResearchValidationError):
        validate_public_https_url(url)


def test_validate_public_https_url_rejects_mixed_private_dns_answer(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "five08.agent.web.socket.getaddrinfo",
        lambda *_args, **_kwargs: [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", 443),
            ),
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("10.0.0.1", 443),
            ),
        ],
    )

    with pytest.raises(WebResearchValidationError, match="non-public"):
        validate_public_https_url("https://mixed.example/path")


def test_validate_public_https_url_accepts_public_dns_answer(monkeypatch) -> None:
    monkeypatch.setattr(
        "five08.agent.web.socket.getaddrinfo",
        lambda *_args, **_kwargs: [
            (
                socket.AF_INET6,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("2606:2800:220:1:248:1893:25c8:1946", 443, 0, 0),
            )
        ],
    )

    assert validate_public_https_url("https://example.com/path#fragment") == (
        "https://example.com/path"
    )


def test_planner_url_validation_does_not_resolve_before_policy(monkeypatch) -> None:
    """An unauthorized model action must not trigger a DNS lookup in validation."""

    def fail_dns(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("planner validation must not resolve hostnames")

    monkeypatch.setattr("five08.agent.web.socket.getaddrinfo", fail_dns)

    ToolRegistry().validate_planner_action(
        "web_read.extract",
        {"url": "https://example.com/article"},
    )


def test_provider_base_urls_require_https_except_explicit_searxng_style_http() -> None:
    assert (
        normalize_provider_base_url(
            "http://searxng:8080/", provider="SearXNG", allow_http=True
        )
        == "http://searxng:8080"
    )

    with pytest.raises(WebResearchConfigurationError, match="HTTPS"):
        normalize_provider_base_url("http://api.example.com", provider="Brave")
    with pytest.raises(WebResearchConfigurationError, match="credentials"):
        normalize_provider_base_url("https://token@example.com", provider="Firecrawl")


def test_web_research_client_uses_next_provider_only_after_failure() -> None:
    class FailingProvider:
        name = "first"

        def search(self, query: str, *, limit: int) -> WebSearchResponse:
            raise WebResearchTransportError("first unavailable")

    class WorkingProvider:
        name = "second"

        def __init__(self) -> None:
            self.calls = 0

        def search(self, query: str, *, limit: int) -> WebSearchResponse:
            self.calls += 1
            return WebSearchResponse(provider=self.name, results=())

    second = WorkingProvider()
    client = WebResearchClient(search_providers=(FailingProvider(), second))

    assert client.search("research") == WebSearchResponse(provider="second", results=())
    assert second.calls == 1


def test_web_research_client_reports_all_provider_errors() -> None:
    class FailingProvider:
        def __init__(self, name: str) -> None:
            self.name = name

        def search(self, query: str, *, limit: int) -> WebSearchResponse:
            raise WebResearchTransportError(f"{self.name} unavailable")

    client = WebResearchClient(
        search_providers=(FailingProvider("searxng"), FailingProvider("brave"))
    )

    with pytest.raises(WebResearchUnavailableError) as exc_info:
        client.search("research")

    assert set(exc_info.value.provider_errors) == {"searxng", "brave"}


def test_web_research_client_routes_named_extract_provider() -> None:
    class Extractor:
        name = "test-extract"

        def extract(self, url: str) -> WebExtractResult:
            return WebExtractResult(
                provider=self.name,
                url=url,
                content="content",
            )

    client = WebResearchClient(
        search_providers=(),
        extract_providers=(Extractor(),),
    )

    assert (
        client.extract("https://example.com", provider="test-extract").content
        == "content"
    )


def test_provider_transport_and_response_errors_are_typed() -> None:
    with patch(
        "five08.agent.web.requests.get",
        side_effect=requests.Timeout("timed out"),
    ):
        with pytest.raises(WebResearchTransportError, match="brave request failed"):
            BraveWebSearch(api_key="token").search("query")

    invalid_json = _response({})
    invalid_json.headers = {}
    invalid_json.iter_content.return_value = [b"not json"]
    with patch("five08.agent.web.requests.get", return_value=invalid_json):
        with pytest.raises(WebResearchResponseError, match="valid JSON"):
            BraveWebSearch(api_key="token").search("query")


def test_provider_response_body_is_capped_before_json_parsing() -> None:
    response = _response({"web": {"results": []}})
    response.headers = {"Content-Length": str(MAX_WEB_RESPONSE_BYTES + 1)}

    with patch("five08.agent.web.requests.get", return_value=response):
        with pytest.raises(WebResearchResponseError, match="too large"):
            BraveWebSearch(api_key="token").search("query")

    response.iter_content.assert_not_called()


def test_streamed_response_stops_when_the_global_web_deadline_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A long-lived chunked response cannot keep a scheduled run alive."""

    response = _response({"web": {"results": []}})
    response.iter_content.return_value = [b'{"web":', b'{"results": []}}']
    timestamps = iter([9.0, 10.0])
    monkeypatch.setattr(agent_web, "monotonic", lambda: next(timestamps))

    with (
        agent_web._web_request_deadline(10.0),
        pytest.raises(WebResearchTransportError, match="deadline exceeded"),
    ):
        agent_web._response_json("test", response)

    response.close.assert_called_once()


def test_web_search_query_and_limit_are_bounded() -> None:
    provider = BraveWebSearch(api_key="token")
    with pytest.raises(WebResearchValidationError, match="400 characters"):
        provider.search("x" * 401)
    with pytest.raises(WebResearchValidationError, match="between 1 and 10"):
        provider.search("valid", limit=11)
    with pytest.raises(WebResearchValidationError, match="integer"):
        provider.search("valid", limit=True)  # type: ignore[arg-type]
