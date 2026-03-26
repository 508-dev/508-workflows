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
) -> DebugResult:
    allow_javascript_fallback = source_type == "website"
    try:
        diagnostics = processor.inspect_profile_source_fetch(
            url,
            allow_javascript_fallback=allow_javascript_fallback,
        )
    except Exception as exc:
        return DebugResult(
            url=url,
            source_type=source_type,
            decision="error",
            error=str(exc),
        )
    return DebugResult(
        url=url,
        source_type=source_type,
        final_url=diagnostics.final_url or url,
        content_type=diagnostics.content_type,
        curl_text_preview=diagnostics.curl_text,
        browser_attempted=diagnostics.browser_attempted,
        browser_used=diagnostics.browser_used,
        browser_text_preview=diagnostics.browser_text,
        decision=(
            "error"
            if diagnostics.error and not diagnostics.selected_text
            else "browser"
            if diagnostics.browser_used
            else "curl"
        ),
        error=diagnostics.error,
    )


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
        )
        for url in args.urls
    ]
    for result in results:
        result.curl_text_preview = _shorten(
            result.curl_text_preview, args.preview_chars
        )
        result.browser_text_preview = _shorten(
            result.browser_text_preview, args.preview_chars
        )
    if args.json:
        _print_json(results)
    else:
        _print_pretty(results)


if __name__ == "__main__":
    main()
