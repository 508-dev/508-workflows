"""Bounded, provider-neutral web research adapters for the agent.

The adapters deliberately only expose search result metadata and bounded page
markdown.  They do not decide whether a request is permissible to send to an
external provider; callers must make that policy decision before invoking
them.  All provider responses are untrusted data and should be labelled as
such before being added to an LLM context.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from math import isfinite
from time import monotonic
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

import requests

from five08.tls import default_ca_bundle_path

DEFAULT_WEB_SEARCH_LIMIT = 5
MAX_WEB_SEARCH_LIMIT = 10
MAX_WEB_QUERY_CHARS = 400
MAX_WEB_QUERY_WORDS = 50
MAX_WEB_RESULT_TITLE_CHARS = 512
MAX_WEB_RESULT_SNIPPET_CHARS = 2_000
MAX_WEB_EXTRACT_CONTENT_CHARS = 20_000
MAX_WEB_EXTRACT_METADATA_CHARS = 1_000
MAX_WEB_URL_CHARS = 2_048
DEFAULT_WEB_TIMEOUT_SECONDS = 15.0
MAX_WEB_TIMEOUT_SECONDS = 60.0

SEARXNG_DEFAULT_BASE_URL = "http://searxng:8080"
BRAVE_SEARCH_DEFAULT_BASE_URL = "https://api.search.brave.com"
FIRECRAWL_DEFAULT_BASE_URL = "https://api.firecrawl.dev"

_WEB_REQUEST_DEADLINE: ContextVar[float | None] = ContextVar(
    "agent_web_request_deadline",
    default=None,
)


class WebResearchError(RuntimeError):
    """Base exception for expected web research failures."""


class WebResearchConfigurationError(WebResearchError):
    """Raised when an adapter has invalid or missing configuration."""


class WebResearchValidationError(WebResearchError):
    """Raised when a query, result limit, or target URL is unsafe or invalid."""


class WebResearchTransportError(WebResearchError):
    """Raised when a provider cannot be reached before it returns a response."""


class WebResearchUpstreamError(WebResearchError):
    """Raised when a provider returns a non-success HTTP response."""

    def __init__(self, provider: str, status_code: int) -> None:
        self.provider = provider
        self.status_code = status_code
        super().__init__(f"{provider} request failed with status {status_code}.")


class WebResearchResponseError(WebResearchError):
    """Raised when a provider returns malformed or unsuccessful JSON."""


class WebResearchUnavailableError(WebResearchError):
    """Raised when no configured provider can complete a requested search."""

    def __init__(self, provider_errors: Mapping[str, WebResearchError]) -> None:
        self.provider_errors = dict(provider_errors)
        provider_names = ", ".join(self.provider_errors)
        super().__init__(f"No web search provider was available ({provider_names}).")


@dataclass(frozen=True)
class WebSearchResult:
    """A bounded, source-attributed search result safe to pass to callers."""

    provider: str
    title: str
    url: str
    snippet: str = ""
    published_at: str | None = None
    source: str | None = None


@dataclass(frozen=True)
class WebSearchResponse:
    """The results returned by one search provider."""

    provider: str
    results: tuple[WebSearchResult, ...]


@dataclass(frozen=True)
class WebExtractResult:
    """Bounded markdown extracted from a public web page."""

    provider: str
    url: str
    content: str
    title: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


class WebSearchProvider(Protocol):
    """Provider-neutral synchronous web-search interface."""

    name: str

    def search(self, query: str, *, limit: int) -> WebSearchResponse:
        """Return bounded results for one search query."""


class WebExtractProvider(Protocol):
    """Provider-neutral synchronous web-extraction interface."""

    name: str

    def extract(self, url: str) -> WebExtractResult:
        """Extract bounded main-page content from one public HTTPS URL."""


def normalize_provider_base_url(
    base_url: str,
    *,
    provider: str,
    allow_http: bool = False,
) -> str:
    """Validate and normalize a trusted provider endpoint configuration.

    Provider URLs are administrator-controlled configuration rather than user
    input.  HTTPS is nevertheless mandatory by default so that API credentials
    cannot be sent over cleartext.  SearXNG can explicitly opt into HTTP for a
    private in-cluster deployment.
    """

    if not isinstance(base_url, str):
        raise WebResearchConfigurationError(f"{provider} base URL must be a string.")
    candidate = base_url.strip()
    if not candidate:
        raise WebResearchConfigurationError(f"{provider} base URL must not be empty.")
    if len(candidate) > MAX_WEB_URL_CHARS:
        raise WebResearchConfigurationError(f"{provider} base URL is too long.")

    try:
        parsed = urlsplit(candidate)
    except ValueError as exc:
        raise WebResearchConfigurationError(
            f"{provider} base URL must be an absolute HTTP(S) URL."
        ) from exc

    allowed_schemes = {"https"}
    if allow_http:
        allowed_schemes.add("http")
    if parsed.scheme.lower() not in allowed_schemes or not parsed.hostname:
        scheme_hint = "HTTP(S)" if allow_http else "HTTPS"
        raise WebResearchConfigurationError(
            f"{provider} base URL must be an absolute {scheme_hint} URL."
        )
    if parsed.username or parsed.password:
        raise WebResearchConfigurationError(
            f"{provider} base URL must not include credentials."
        )
    if parsed.query or parsed.fragment:
        raise WebResearchConfigurationError(
            f"{provider} base URL must not include a query or fragment."
        )
    try:
        _ = parsed.port
    except ValueError as exc:
        raise WebResearchConfigurationError(
            f"{provider} base URL has an invalid port."
        ) from exc

    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, path, "", ""))


def validate_public_https_url(url: str) -> str:
    """Validate an extraction target and reject non-public HTTPS destinations.

    Firecrawl performs the actual page fetch.  Resolving before sending it a
    target still blocks obvious SSRF attempts such as ``localhost``, private IP
    literals, and DNS names that currently resolve to any non-public address.
    The provider must retain its own SSRF protections because DNS can change
    after this validation.
    """

    normalized, host, port = _normalize_public_web_url(url, require_https=True)
    if urlsplit(normalized).query:
        raise WebResearchValidationError(
            "Web extraction URL must not include a query string."
        )
    _require_public_host(host, port)
    return normalized


class SearxngWebSearch:
    """SearXNG JSON search adapter.

    SearXNG is commonly self-hosted inside the deployment network, hence HTTP
    is allowed only for this explicit adapter.  No credentials are attached to
    SearXNG requests by this module.
    """

    name = "searxng"

    def __init__(
        self,
        *,
        base_url: str = SEARXNG_DEFAULT_BASE_URL,
        timeout_seconds: float = DEFAULT_WEB_TIMEOUT_SECONDS,
        language: str | None = None,
    ) -> None:
        self.base_url = normalize_provider_base_url(
            base_url,
            provider="SearXNG",
            allow_http=True,
        )
        self.timeout_seconds = _normalize_timeout(timeout_seconds, provider=self.name)
        self.language = _optional_bounded_text(language, max_chars=32)

    def search(
        self, query: str, *, limit: int = DEFAULT_WEB_SEARCH_LIMIT
    ) -> WebSearchResponse:
        normalized_query = _normalize_query(query)
        normalized_limit = _normalize_limit(limit)
        params: dict[str, str] = {
            "q": normalized_query,
            "format": "json",
            "categories": "general",
        }
        if self.language:
            params["language"] = self.language

        payload = _get_json(
            provider=self.name,
            url=_with_endpoint(self.base_url, "/search"),
            params=params,
            headers={"Accept": "application/json"},
            timeout_seconds=self.timeout_seconds,
        )
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raise WebResearchResponseError(
                "SearXNG response did not include a results list."
            )

        results = _parse_search_results(
            provider=self.name,
            raw_results=raw_results,
            limit=normalized_limit,
            title_keys=("title",),
            url_keys=("url",),
            snippet_keys=("content", "snippet", "description"),
            published_at_keys=("publishedDate", "published_at", "date"),
            source_keys=("engine", "engines", "category"),
        )
        return WebSearchResponse(provider=self.name, results=results)


class BraveWebSearch:
    """Brave Web Search API adapter."""

    name = "brave"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = BRAVE_SEARCH_DEFAULT_BASE_URL,
        timeout_seconds: float = DEFAULT_WEB_TIMEOUT_SECONDS,
        country: str | None = None,
        search_language: str | None = None,
    ) -> None:
        self.api_key = _required_secret(api_key, provider="Brave")
        self.base_url = normalize_provider_base_url(base_url, provider="Brave")
        self.timeout_seconds = _normalize_timeout(timeout_seconds, provider=self.name)
        self.country = _optional_bounded_text(country, max_chars=8)
        self.search_language = _optional_bounded_text(search_language, max_chars=16)

    def search(
        self, query: str, *, limit: int = DEFAULT_WEB_SEARCH_LIMIT
    ) -> WebSearchResponse:
        normalized_query = _normalize_query(query)
        normalized_limit = _normalize_limit(limit)
        params: dict[str, str | int] = {
            "q": normalized_query,
            "count": normalized_limit,
            "safesearch": "moderate",
        }
        if self.country:
            params["country"] = self.country
        if self.search_language:
            params["search_lang"] = self.search_language

        payload = _get_json(
            provider=self.name,
            url=_brave_search_endpoint(self.base_url),
            params=params,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": self.api_key,
            },
            timeout_seconds=self.timeout_seconds,
        )
        web = payload.get("web")
        if web is None:
            return WebSearchResponse(provider=self.name, results=())
        if not isinstance(web, Mapping):
            raise WebResearchResponseError(
                "Brave response web field must be an object."
            )
        raw_results = web.get("results")
        if raw_results is None:
            return WebSearchResponse(provider=self.name, results=())
        if not isinstance(raw_results, list):
            raise WebResearchResponseError(
                "Brave response results field must be a list."
            )

        results = _parse_search_results(
            provider=self.name,
            raw_results=raw_results,
            limit=normalized_limit,
            title_keys=("title",),
            url_keys=("url",),
            snippet_keys=("description", "snippet"),
            published_at_keys=("page_age", "age", "published_at"),
            source_keys=("profile",),
        )
        return WebSearchResponse(provider=self.name, results=results)


class FirecrawlWebResearch:
    """Firecrawl v2 search and public-page extraction adapter."""

    name = "firecrawl"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = FIRECRAWL_DEFAULT_BASE_URL,
        timeout_seconds: float = DEFAULT_WEB_TIMEOUT_SECONDS,
    ) -> None:
        self.api_key = _required_secret(api_key, provider="Firecrawl")
        self.base_url = normalize_provider_base_url(base_url, provider="Firecrawl")
        self.timeout_seconds = _normalize_timeout(timeout_seconds, provider=self.name)

    def search(
        self, query: str, *, limit: int = DEFAULT_WEB_SEARCH_LIMIT
    ) -> WebSearchResponse:
        normalized_query = _normalize_query(query)
        normalized_limit = _normalize_limit(limit)
        payload = _post_json(
            provider=self.name,
            url=_firecrawl_endpoint(self.base_url, "search"),
            headers=self._headers(),
            body={
                "query": normalized_query,
                "limit": normalized_limit,
                "sources": ["web"],
            },
            timeout_seconds=self.timeout_seconds,
        )
        data = _firecrawl_data(payload)
        raw_results = _firecrawl_search_results(data)
        results = _parse_search_results(
            provider=self.name,
            raw_results=raw_results,
            limit=normalized_limit,
            title_keys=("title",),
            url_keys=("url",),
            snippet_keys=("description", "snippet"),
            published_at_keys=("published_at", "publishedDate", "date"),
            source_keys=(),
            metadata_url_keys=("sourceURL", "sourceUrl"),
        )
        return WebSearchResponse(provider=self.name, results=results)

    def extract(self, url: str) -> WebExtractResult:
        normalized_url = validate_public_https_url(url)
        effective_timeout_seconds = _effective_request_timeout(self.timeout_seconds)
        provider_timeout_ms = max(1_000, int(effective_timeout_seconds * 1_000))
        payload = _post_json(
            provider=self.name,
            url=_firecrawl_endpoint(self.base_url, "scrape"),
            headers=self._headers(),
            body={
                "url": normalized_url,
                "formats": ["markdown"],
                "onlyMainContent": True,
                "removeBase64Images": True,
                "blockAds": True,
                "skipTlsVerification": False,
                "storeInCache": False,
                "timeout": provider_timeout_ms,
            },
            timeout_seconds=effective_timeout_seconds,
        )
        data = _firecrawl_data(payload)
        raw_content = data.get("markdown")
        if not isinstance(raw_content, str) or not raw_content.strip():
            raise WebResearchResponseError(
                "Firecrawl scrape response did not include markdown."
            )

        raw_metadata = data.get("metadata")
        metadata_source = raw_metadata if isinstance(raw_metadata, Mapping) else {}
        title = _first_bounded_text(
            (metadata_source.get("title"), data.get("title")),
            max_chars=MAX_WEB_RESULT_TITLE_CHARS,
        )
        metadata = _bounded_extract_metadata(metadata_source)
        final_url = _first_normalized_result_url(
            (metadata_source.get("sourceURL"), metadata_source.get("url")),
            fallback=normalized_url,
        )
        if final_url is None:
            final_url = normalized_url
        return WebExtractResult(
            provider=self.name,
            url=final_url,
            content=_bounded_markdown(raw_content),
            title=title,
            metadata=metadata,
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }


class WebResearchClient:
    """Route operations to configured providers with safe fallback semantics.

    The default search path tries providers in construction order.  It only
    falls back after a provider failure; an empty successful result is returned
    directly, avoiding surprise extra API usage.  Callers can request a named
    provider when policy, cost controls, or user choice require it.
    """

    def __init__(
        self,
        *,
        search_providers: Sequence[WebSearchProvider],
        extract_providers: Sequence[WebExtractProvider] = (),
    ) -> None:
        self._search_providers = _provider_map(search_providers, capability="search")
        self._extract_providers = _provider_map(extract_providers, capability="extract")

    @property
    def search_provider_names(self) -> tuple[str, ...]:
        """Configured search providers in fallback order."""
        return tuple(self._search_providers)

    @property
    def extract_provider_names(self) -> tuple[str, ...]:
        """Configured extraction providers."""
        return tuple(self._extract_providers)

    def search(
        self,
        query: str,
        *,
        limit: int = DEFAULT_WEB_SEARCH_LIMIT,
        provider: str | None = None,
        deadline_monotonic: float | None = None,
    ) -> WebSearchResponse:
        """Search with one selected provider or the configured fallback order."""
        _normalize_query(query)
        _normalize_limit(limit)
        with _web_request_deadline(deadline_monotonic):
            if provider is not None:
                selected = self._search_provider(provider)
                return selected.search(query, limit=limit)

            errors: dict[str, WebResearchError] = {}
            for name, selected in self._search_providers.items():
                try:
                    return selected.search(query, limit=limit)
                except WebResearchError as exc:
                    errors[name] = exc
            raise WebResearchUnavailableError(errors)

    def extract(
        self,
        url: str,
        *,
        provider: str = "firecrawl",
        deadline_monotonic: float | None = None,
    ) -> WebExtractResult:
        """Extract one public HTTPS page through a named provider."""
        with _web_request_deadline(deadline_monotonic):
            selected = self._extract_provider(provider)
            return selected.extract(url)

    def _search_provider(self, name: str) -> WebSearchProvider:
        return _provider_by_name(self._search_providers, name, capability="search")

    def _extract_provider(self, name: str) -> WebExtractProvider:
        return _provider_by_name(self._extract_providers, name, capability="extract")


def _required_secret(value: str, *, provider: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WebResearchConfigurationError(f"{provider} API key must not be empty.")
    return value.strip()


def _normalize_timeout(value: float, *, provider: str) -> float:
    if isinstance(value, bool):
        raise WebResearchConfigurationError(f"{provider} timeout must be a number.")
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise WebResearchConfigurationError(
            f"{provider} timeout must be a number."
        ) from exc
    if not isfinite(timeout) or not 1.0 <= timeout <= MAX_WEB_TIMEOUT_SECONDS:
        raise WebResearchConfigurationError(
            f"{provider} timeout must be between 1 and {MAX_WEB_TIMEOUT_SECONDS:g} seconds."
        )
    return timeout


@contextmanager
def _web_request_deadline(deadline_monotonic: float | None):
    token = _WEB_REQUEST_DEADLINE.set(deadline_monotonic)
    try:
        yield
    finally:
        _WEB_REQUEST_DEADLINE.reset(token)


def _effective_request_timeout(timeout_seconds: float) -> float:
    deadline = _WEB_REQUEST_DEADLINE.get()
    if deadline is None:
        return timeout_seconds
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise WebResearchTransportError("Public web research deadline exceeded.")
    return min(timeout_seconds, remaining)


def _normalize_query(query: str) -> str:
    if not isinstance(query, str):
        raise WebResearchValidationError("Web search query must be text.")
    normalized = " ".join(query.split())
    if not normalized:
        raise WebResearchValidationError("Web search query must not be empty.")
    if len(normalized) > MAX_WEB_QUERY_CHARS:
        raise WebResearchValidationError(
            f"Web search query must be at most {MAX_WEB_QUERY_CHARS} characters."
        )
    if len(normalized.split()) > MAX_WEB_QUERY_WORDS:
        raise WebResearchValidationError(
            f"Web search query must be at most {MAX_WEB_QUERY_WORDS} words."
        )
    return normalized


def _normalize_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise WebResearchValidationError("Web search result limit must be an integer.")
    if not 1 <= limit <= MAX_WEB_SEARCH_LIMIT:
        raise WebResearchValidationError(
            f"Web search result limit must be between 1 and {MAX_WEB_SEARCH_LIMIT}."
        )
    return limit


def _optional_bounded_text(value: str | None, *, max_chars: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise WebResearchConfigurationError("Configured text values must be strings.")
    normalized = " ".join(value.split())
    if not normalized:
        return None
    if len(normalized) > max_chars:
        raise WebResearchConfigurationError("Configured text value is too long.")
    return normalized


def _with_endpoint(base_url: str, endpoint: str) -> str:
    """Append an endpoint once while preserving a configured base path."""
    normalized_endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"
    if base_url.rstrip("/").endswith(normalized_endpoint):
        return base_url.rstrip("/")
    return f"{base_url.rstrip('/')}{normalized_endpoint}"


def _brave_search_endpoint(base_url: str) -> str:
    return _with_endpoint(base_url, "/res/v1/web/search")


def _firecrawl_endpoint(base_url: str, endpoint: str) -> str:
    normalized = base_url.rstrip("/")
    endpoint_path = f"/v2/{endpoint}"
    if normalized.endswith(endpoint_path):
        return normalized
    for known_endpoint in ("/v2/search", "/v2/scrape"):
        if normalized.endswith(known_endpoint):
            return f"{normalized[: -len(known_endpoint)]}/v2/{endpoint}"
    if normalized.endswith("/v2"):
        return f"{normalized}/{endpoint}"
    return f"{normalized}{endpoint_path}"


def _get_json(
    *,
    provider: str,
    url: str,
    params: Mapping[str, str | int],
    headers: Mapping[str, str],
    timeout_seconds: float,
) -> dict[str, Any]:
    try:
        response = requests.get(
            url,
            params=dict(params),
            headers=dict(headers),
            timeout=_effective_request_timeout(timeout_seconds),
            verify=default_ca_bundle_path(),
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise WebResearchTransportError(f"{provider} request failed: {exc}") from exc
    return _response_json(provider, response)


def _post_json(
    *,
    provider: str,
    url: str,
    headers: Mapping[str, str],
    body: Mapping[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    try:
        response = requests.post(
            url,
            headers=dict(headers),
            json=dict(body),
            timeout=_effective_request_timeout(timeout_seconds),
            verify=default_ca_bundle_path(),
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise WebResearchTransportError(f"{provider} request failed: {exc}") from exc
    return _response_json(provider, response)


def _response_json(provider: str, response: requests.Response) -> dict[str, Any]:
    status_code = getattr(response, "status_code", None)
    if not isinstance(status_code, int) or not 200 <= status_code < 300:
        if not isinstance(status_code, int):
            raise WebResearchResponseError(
                f"{provider} response did not include a status code."
            )
        raise WebResearchUpstreamError(provider, status_code)
    try:
        payload = response.json()
    except ValueError as exc:
        raise WebResearchResponseError(
            f"{provider} response payload must be valid JSON."
        ) from exc
    if not isinstance(payload, dict):
        raise WebResearchResponseError(
            f"{provider} response payload must be a JSON object."
        )
    if payload.get("success") is False:
        raise WebResearchResponseError(f"{provider} reported an unsuccessful response.")
    return payload


def _firecrawl_data(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise WebResearchResponseError(
            "Firecrawl response did not include a data object."
        )
    return data


def _firecrawl_search_results(data: Mapping[str, Any]) -> list[Any]:
    for key in ("web", "results"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def _parse_search_results(
    *,
    provider: str,
    raw_results: Sequence[Any],
    limit: int,
    title_keys: Sequence[str],
    url_keys: Sequence[str],
    snippet_keys: Sequence[str],
    published_at_keys: Sequence[str],
    source_keys: Sequence[str],
    metadata_url_keys: Sequence[str] = (),
) -> tuple[WebSearchResult, ...]:
    parsed_results: list[WebSearchResult] = []
    seen_urls: set[str] = set()

    for raw_result in raw_results:
        if len(parsed_results) >= limit:
            break
        if not isinstance(raw_result, Mapping):
            continue

        metadata = raw_result.get("metadata")
        metadata_values = metadata if isinstance(metadata, Mapping) else {}
        url = _first_normalized_result_url(
            tuple(raw_result.get(key) for key in url_keys)
            + tuple(metadata_values.get(key) for key in metadata_url_keys),
            fallback=None,
        )
        if url is None:
            continue
        url_key = url.casefold()
        if url_key in seen_urls:
            continue
        seen_urls.add(url_key)

        title = _first_bounded_text(
            tuple(raw_result.get(key) for key in title_keys)
            + (metadata_values.get("title"),),
            max_chars=MAX_WEB_RESULT_TITLE_CHARS,
        )
        if not title:
            title = _title_from_url(url)
        snippet = (
            _first_bounded_text(
                tuple(raw_result.get(key) for key in snippet_keys)
                + (metadata_values.get("description"),),
                max_chars=MAX_WEB_RESULT_SNIPPET_CHARS,
            )
            or ""
        )
        published_at = _first_bounded_text(
            tuple(raw_result.get(key) for key in published_at_keys),
            max_chars=128,
        )
        source = _first_source_text(raw_result, source_keys)
        parsed_results.append(
            WebSearchResult(
                provider=provider,
                title=title,
                url=url,
                snippet=snippet,
                published_at=published_at,
                source=source,
            )
        )
    return tuple(parsed_results)


def _first_normalized_result_url(
    candidates: Sequence[Any],
    *,
    fallback: str | None,
) -> str | None:
    for candidate in candidates:
        try:
            return _normalize_result_url(candidate)
        except WebResearchValidationError:
            continue
    return fallback


def _normalize_result_url(value: Any) -> str:
    if not isinstance(value, str):
        raise WebResearchValidationError("Search result URL must be text.")
    normalized, host, _port = _normalize_public_web_url(value, require_https=False)
    _reject_obviously_non_public_host(host)
    return normalized


def _normalize_public_web_url(
    value: Any, *, require_https: bool
) -> tuple[str, str, int]:
    if not isinstance(value, str):
        raise WebResearchValidationError("Web URL must be text.")
    candidate = value.strip()
    if not candidate:
        raise WebResearchValidationError("Web URL must not be empty.")
    if len(candidate) > MAX_WEB_URL_CHARS:
        raise WebResearchValidationError("Web URL is too long.")
    try:
        parsed = urlsplit(candidate)
    except ValueError as exc:
        raise WebResearchValidationError("Web URL is invalid.") from exc

    scheme = parsed.scheme.lower()
    allowed_schemes = {"https"} if require_https else {"http", "https"}
    if scheme not in allowed_schemes:
        if require_https:
            raise WebResearchValidationError("Web extraction URL must use HTTPS.")
        raise WebResearchValidationError("Web URL must use HTTP or HTTPS.")
    if parsed.username or parsed.password:
        raise WebResearchValidationError("Web URL must not include credentials.")
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise WebResearchValidationError("Web URL must include a hostname.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise WebResearchValidationError("Web URL has an invalid port.") from exc
    if port is None:
        port = 443 if scheme == "https" else 80
    if port not in {80, 443}:
        raise WebResearchValidationError("Web URL port must be 80 or 443.")
    if require_https and port != 443:
        raise WebResearchValidationError("Web extraction URL port must be 443.")

    # urlsplit preserves a valid bracketed IPv6 netloc, so retain it rather than
    # reconstructing it from the hostname.  Removing fragments makes citation
    # de-duplication deterministic without changing the requested query.
    normalized = urlunsplit(
        (scheme, parsed.netloc, parsed.path or "/", parsed.query, "")
    )
    return normalized, host, port


def _require_public_host(host: str, port: int) -> None:
    if _reject_obviously_non_public_host(host):
        return

    try:
        address_info = socket.getaddrinfo(
            host,
            port,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise WebResearchValidationError(
            "Web extraction URL host does not resolve to a public address."
        ) from exc

    addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for _family, _socktype, _proto, _canonical_name, socket_address in address_info:
        if not socket_address:
            continue
        parsed = _parse_ip_address(str(socket_address[0]).strip())
        if parsed is not None:
            addresses.add(parsed)
    if not addresses or any(not address.is_global for address in addresses):
        raise WebResearchValidationError(
            "Web extraction URL host resolves to a non-public address."
        )


def _reject_obviously_non_public_host(host: str) -> bool:
    """Reject local hostnames and non-public IP literals without DNS lookups."""
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        raise WebResearchValidationError(
            "Web extraction URL host resolves to a non-public address."
        )
    ip_address = _parse_ip_address(host)
    if ip_address is not None:
        if not ip_address.is_global:
            raise WebResearchValidationError(
                "Web extraction URL host resolves to a non-public address."
            )
        return True
    return False


def _parse_ip_address(
    value: str,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _first_bounded_text(candidates: Sequence[Any], *, max_chars: int) -> str | None:
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        normalized = " ".join(candidate.replace("\x00", "").split())
        if normalized:
            return _truncate(normalized, max_chars)
    return None


def _first_source_text(
    raw_result: Mapping[str, Any], source_keys: Sequence[str]
) -> str | None:
    for key in source_keys:
        value = raw_result.get(key)
        if isinstance(value, list):
            source = _first_bounded_text(value, max_chars=128)
        elif isinstance(value, Mapping):
            source = _first_bounded_text(
                (value.get("long_name"), value.get("name"), value.get("url")),
                max_chars=128,
            )
        else:
            source = _first_bounded_text((value,), max_chars=128)
        if source:
            return source
    return None


def _title_from_url(url: str) -> str:
    host = urlsplit(url).hostname or url
    return _truncate(host, MAX_WEB_RESULT_TITLE_CHARS)


def _bounded_markdown(value: str) -> str:
    # Preserve line structure for markdown while removing NUL bytes and
    # preventing unbounded untrusted context from reaching the model.
    normalized = value.replace("\x00", "").strip()
    return _truncate(normalized, MAX_WEB_EXTRACT_CONTENT_CHARS)


def _bounded_extract_metadata(metadata: Mapping[str, Any]) -> dict[str, str]:
    output: dict[str, str] = {}
    for key in ("description", "sourceURL", "url", "statusCode", "language"):
        value = metadata.get(key)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            normalized = _first_bounded_text(
                (str(value),), max_chars=MAX_WEB_EXTRACT_METADATA_CHARS
            )
            if normalized:
                output[key] = normalized
    return output


def _truncate(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    if max_chars <= 1:
        return value[:max_chars]
    return value[: max_chars - 1].rstrip() + "…"


def _provider_map(
    providers: Sequence[WebSearchProvider] | Sequence[WebExtractProvider],
    *,
    capability: str,
) -> dict[str, Any]:
    mapped: dict[str, Any] = {}
    for provider in providers:
        raw_name = getattr(provider, "name", None)
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise WebResearchConfigurationError(
                f"Configured {capability} provider must declare a name."
            )
        name = raw_name.strip().casefold()
        if name in mapped:
            raise WebResearchConfigurationError(
                f"Duplicate {capability} provider configured: {name}."
            )
        mapped[name] = provider
    return mapped


def _provider_by_name(
    providers: Mapping[str, Any],
    name: str,
    *,
    capability: str,
) -> Any:
    if not isinstance(name, str) or not name.strip():
        raise WebResearchValidationError(f"Web {capability} provider name is required.")
    normalized = name.strip().casefold()
    selected = providers.get(normalized)
    if selected is None:
        raise WebResearchConfigurationError(
            f"Web {capability} provider is not configured: {normalized}."
        )
    return selected
