"""Unit tests for shared document text extraction."""

from __future__ import annotations

import pytest

from five08.document_text import document_file_extension
from five08.document_text import extract_document_text


def test_document_file_extension_normalizes_missing_and_mixed_case_names() -> None:
    assert document_file_extension(None) == ""
    assert document_file_extension("resume.PDF") == ".pdf"
    assert document_file_extension("resume") == ""


def test_extract_document_text_rejects_legacy_doc_files() -> None:
    with pytest.raises(ValueError, match=r"Legacy \.doc files are not supported\."):
        extract_document_text(b"binary-doc-content", filename="resume.doc")
