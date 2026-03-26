#!/usr/bin/env python3
"""Debug helper for external profile source fetching."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

from five08.resume_profile_processor import (
    ResumeProcessorConfig,
    ResumeProfileProcessor,
    _FetchedProfileSourceResponse,
)


@dataclass
class DebugResult:
    url: str
    source_type: str
    final_url: str | None = None
    content_type: str | None = None
    curl_text_preview: str | None = None
    browser_attempted: bool = False
    browser_used: bool = False
    browser_text_preview: str | None = None
    decision: str | None = None
    error: str | None = None


class DebugResumeProfileProcessor(ResumeProfileProcessor):
    def __init__(self) -> None:
        super().__init__(
            ResumeProcessorConfig(
                espo_base_url="https://example.invalid",
                espo_api_key="debug",
            )
        )
        self.last_response: _FetchedProfileSourceResponse | None = None
        self.last_browser_attempted = False
        self.last_browser_text: str | None = None

    def reset_debug_state(self) -> None:
        self.last_response = None
        self.last_browser_attempted = False
        self.last_browser_text = None

    def _fetch_external_profile_source_response(
        self, url: str
    ) -> _FetchedProfileSourceResponse:
        response = super()._fetch_external_profile_source_response(url)
        self.last_response = response
        return response

    def _fetch_external_profile_source_text_with_browser(self, url: str) -> str:
        self.last_browser_attempted = True
        rendered = super()._fetch_external_profile_source_text_with_browser(url)
        self.last_browser_text = rendered
        return rendered


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch profile-source URLs with the shared logic and print whether "
            "they stayed on curl or retried with the JS browser fallback."
        )
    )
    parser.add_argument("urls", nargs="+", help="One or more URLs to test.")
    parser.add_argument(
        "--source-type",
        choices=["auto", "website", "github"],
        default="auto",
        help="How to treat the URLs for fallback decisions. Default: auto.",
    )
    parser.add_argument(
        "--preview-chars",
        type=int,
        default=200,
        help="Max characters to print for extracted text previews.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of pretty text.",
    )
    return parser.parse_args()


def _shorten(value: str | None, limit: int) -> str | None:
    if value is None or limit <= 0 or len(value) <= limit:
        return value
    return f"{value[:limit]}... ({len(value) - limit} more chars)"


def _infer_source_type(url: str, configured: str) -> str:
    if configured != "auto":
        return configured
    hostname = (urlsplit(url).hostname or "").casefold()
    if hostname in {"github.com", "www.github.com"}:
        return "github"
    return "website"


def _run_url(
    processor: DebugResumeProfileProcessor,
    url: str,
    *,
    source_type: str,
    preview_chars: int,
) -> DebugResult:
    processor.reset_debug_state()
    allow_javascript_fallback = source_type == "website"

    try:
        extracted = processor._fetch_external_profile_source_text(
            url,
            allow_javascript_fallback=allow_javascript_fallback,
        )
    except Exception as exc:
        return DebugResult(
            url=url,
            source_type=source_type,
            final_url=(
                processor.last_response.final_url if processor.last_response else None
            ),
            content_type=(
                processor.last_response.content_type
                if processor.last_response
                else None
            ),
            browser_attempted=processor.last_browser_attempted,
            decision="error",
            error=str(exc),
        )

    browser_used = (
        processor.last_browser_attempted
        and processor.last_browser_text is not None
        and extracted == processor.last_browser_text
    )
    return DebugResult(
        url=url,
        source_type=source_type,
        final_url=processor.last_response.final_url if processor.last_response else url,
        content_type=(
            processor.last_response.content_type if processor.last_response else None
        ),
        curl_text_preview=_extract_curl_preview(processor, preview_chars),
        browser_attempted=processor.last_browser_attempted,
        browser_used=browser_used,
        browser_text_preview=_shorten(processor.last_browser_text, preview_chars),
        decision="browser" if browser_used else "curl",
        error=None,
    )


def _extract_curl_preview(
    processor: DebugResumeProfileProcessor, preview_chars: int
) -> str | None:
    if processor.last_response is None:
        return None
    try:
        extracted = processor._extract_profile_source_text(
            body=processor.last_response.body,
            content_type=processor.last_response.content_type,
        )
    except Exception as exc:
        return f"<curl extract error: {exc}>"
    return _shorten(extracted, preview_chars)


def _print_pretty(results: list[DebugResult]) -> None:
    for index, result in enumerate(results, start=1):
        print(f"[{index}] {result.url}")
        print(f"  source_type: {result.source_type}")
        print(f"  final_url: {result.final_url or 'n/a'}")
        print(f"  content_type: {result.content_type or 'n/a'}")
        print(f"  browser_attempted: {result.browser_attempted}")
        print(f"  browser_used: {result.browser_used}")
        print(f"  decision: {result.decision or 'n/a'}")
        if result.error:
            print(f"  error: {result.error}")
        if result.curl_text_preview:
            print(f"  curl_text_preview: {result.curl_text_preview}")
        if result.browser_text_preview:
            print(f"  browser_text_preview: {result.browser_text_preview}")


def _print_json(results: list[DebugResult]) -> None:
    print(json.dumps([asdict(result) for result in results], indent=2))


def main() -> None:
    args = _parse_args()
    processor = DebugResumeProfileProcessor()
    results = [
        _run_url(
            processor,
            url,
            source_type=_infer_source_type(url, args.source_type),
            preview_chars=args.preview_chars,
        )
        for url in args.urls
    ]
    if args.json:
        _print_json(results)
    else:
        _print_pretty(results)


if __name__ == "__main__":
    main()
