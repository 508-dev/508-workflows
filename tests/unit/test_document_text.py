"""Unit tests for shared document text extraction."""

from __future__ import annotations

from five08.document_text import document_file_extension
from five08.document_text import extract_document_text


def test_document_file_extension_normalizes_missing_and_mixed_case_names() -> None:
    assert document_file_extension(None) == ""
    assert document_file_extension("resume.PDF") == ".pdf"
    assert document_file_extension("resume") == ""


def test_extract_document_text_normalizes_doc_binary_content() -> None:
    extracted = extract_document_text(
        b"Jane\x00 Doe\x07\tEngineer\n\nRemote",
        filename="resume.doc",
    )

    assert extracted == "Jane Doe Engineer Remote"
