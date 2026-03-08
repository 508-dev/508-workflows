"""PDF extraction helpers."""

from __future__ import annotations

import io
from collections import OrderedDict
from typing import Iterable


def extract_pdf_text_with_links(content: bytes) -> str:
    """Extract PDF text and append visible link text with its target URL."""
    try:
        import fitz
    except Exception as exc:  # pragma: no cover - import failure is env-dependent
        raise ValueError(f"PDF processing dependency missing: {exc}") from exc

    try:
        document = fitz.open(stream=content, filetype="pdf")
    except TypeError:
        document = fitz.open(stream=io.BytesIO(content), filetype="pdf")

    try:
        page_chunks: list[str] = []
        for page in document:
            page_text = page.get_text().strip()
            link_lines = _extract_page_link_lines(page, fitz)
            page_parts = [part for part in (page_text, "\n".join(link_lines)) if part]
            if page_parts:
                page_chunks.append("\n".join(page_parts))

        return "\n\n".join(page_chunks).strip()
    except Exception as exc:
        raise ValueError(f"Failed to extract text from PDF: {exc}") from exc
    finally:
        document.close()


def _extract_page_link_lines(page, fitz_module) -> list[str]:
    words = page.get_text("words")
    if not words:
        return []

    link_lines: OrderedDict[tuple[float, float, float, float], str] = OrderedDict()
    for link in page.get_links():
        uri = str(link.get("uri") or "").strip()
        rect_data = link.get("from")
        if not uri or rect_data is None:
            continue

        rect = fitz_module.Rect(rect_data)
        linked_words = [
            word
            for word in words
            if fitz_module.Rect(word[:4]).intersects(rect) and str(word[4]).strip()
        ]
        if linked_words:
            linked_words.sort(
                key=lambda item: (round(float(item[1]), 3), float(item[0]))
            )
            anchor_text = _normalize_anchor_text(word[4] for word in linked_words)
        else:
            anchor_text = uri

        link_lines.setdefault(
            (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)),
            f"{anchor_text}: {uri}",
        )

    return list(link_lines.values())


def _normalize_anchor_text(words: Iterable[str]) -> str:
    joined = " ".join(str(word).strip() for word in words if str(word).strip())
    return " ".join(joined.split())
