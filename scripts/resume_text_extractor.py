#!/usr/bin/env python3
"""Command-line helper to exercise resume text and link extraction."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any

from five08.document_text import extract_document_text
from five08.resume_extractor import ResumeProfileExtractor


@dataclass
class FileResult:
    path: str
    text_length: int
    extracted_text: str
    links: list[tuple[str, float]]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extract resume text with shared helpers and print the links discovered "
            "by the current resume URL extraction logic."
        )
    )
    parser.add_argument(
        "files",
        nargs="+",
        metavar="FILE",
        help="One or more resume files to extract.",
    )
    parser.add_argument(
        "--text-max",
        type=int,
        default=4000,
        help="Max characters of extracted text to print. Set to 0 for full text.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON output instead of pretty text.",
    )
    return parser.parse_args()


def _shorten_text(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    omitted = len(text) - limit
    return f"{text[:limit]}\n... ({omitted} more chars omitted)"


def extract_website_link_candidates(text: str) -> list[tuple[str, float]]:
    """Extract normalized website links and confidence scores from resume text."""
    return ResumeProfileExtractor._extract_website_link_candidates(text)


def _process_file(path: Path) -> FileResult:
    raw = path.read_bytes()
    text = extract_document_text(raw, filename=path.name)
    link_candidates = extract_website_link_candidates(text)
    return FileResult(
        path=str(path),
        text_length=len(text),
        extracted_text=text,
        links=link_candidates,
    )


def _print_pretty(results: list[FileResult], text_max: int) -> None:
    for index, result in enumerate(results, start=1):
        print(f"\n[{index}] {result.path}")
        print(f"  extracted_text_len={result.text_length}")
        print("  extracted_text:")
        print(_shorten_text(result.extracted_text, text_max))

        if result.links:
            print("  extracted_links:")
            for link, confidence in result.links:
                print(f"    - {link} (confidence={confidence:.3f})")
        else:
            print("  extracted_links: []")


def _print_json(results: list[FileResult], text_max: int) -> None:
    payload: list[dict[str, Any]] = []
    for result in results:
        payload.append(
            {
                "path": result.path,
                "extracted_text_length": result.text_length,
                "extracted_text": _shorten_text(result.extracted_text, text_max),
                "extracted_links": [
                    {"url": url, "confidence": confidence}
                    for url, confidence in result.links
                ],
            }
        )
    print(json.dumps(payload, indent=2))


def main() -> None:
    args = _parse_args()
    results: list[FileResult] = []
    exit_code = 0

    for file_arg in args.files:
        path = Path(file_arg)
        if not path.is_file():
            print(f"error: missing file: {path}", file=sys.stderr)
            exit_code = 1
            continue
        try:
            results.append(_process_file(path))
        except Exception as exc:  # pragma: no cover - runtime diagnostic path
            print(f"error: failed to process {path}: {exc}", file=sys.stderr)
            exit_code = 1

    if not results:
        raise SystemExit(exit_code or 1)

    if args.json:
        _print_json(results, args.text_max)
    else:
        _print_pretty(results, args.text_max)

    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
