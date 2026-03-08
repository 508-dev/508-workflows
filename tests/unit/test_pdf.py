"""Unit tests for shared PDF extraction helpers."""

from __future__ import annotations

import sys
from types import SimpleNamespace

from five08.pdf import extract_pdf_text_with_links


class _FakeRect:
    def __init__(self, coords: tuple[float, float, float, float]) -> None:
        self.x0, self.y0, self.x1, self.y1 = coords

    def intersects(self, other: "_FakeRect") -> bool:
        return not (
            self.x1 < other.x0
            or self.x0 > other.x1
            or self.y1 < other.y0
            or self.y0 > other.y1
        )


class _FakePage:
    def get_text(self, mode: str | None = None):
        if mode == "words":
            return [
                (10.0, 10.0, 20.0, 20.0, "LinkedIn", 0, 0, 0),
                (21.0, 10.0, 30.0, 20.0, "Profile", 0, 0, 1),
            ]
        return "Plain Text"

    def get_links(self) -> list[dict[str, object]]:
        return [
            {
                "uri": "https://linkedin.com/in/example",
                "from": (9.0, 9.0, 31.0, 21.0),
            }
        ]


class _FakeDocument:
    def __iter__(self):
        return iter([_FakePage()])

    def close(self) -> None:
        return None


def test_extract_pdf_text_with_links_appends_anchor_context(
    monkeypatch,
) -> None:
    monkeypatch.setitem(
        sys.modules,
        "fitz",
        SimpleNamespace(
            open=lambda stream, filetype: _FakeDocument(),
            Rect=lambda coords: _FakeRect(coords),
        ),
    )

    extracted = extract_pdf_text_with_links(b"%PDF-1.7")

    assert extracted == "Plain Text\nLinkedIn Profile: https://linkedin.com/in/example"
