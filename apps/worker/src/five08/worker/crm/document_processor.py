"""Resume document text extraction."""

import hashlib
import logging
from pathlib import Path

from five08.document_text import extract_document_text
from five08.worker.config import settings

logger = logging.getLogger(__name__)


class DocumentProcessor:
    """Extract text from supported resume file formats."""

    def __init__(self) -> None:
        self.allowed_extensions = settings.allowed_file_extensions
        self.max_file_size = settings.max_file_size_mb * 1024 * 1024
        self._content_cache: dict[str, str] = {}

    def get_content_hash(self, content: bytes, filename: str) -> str:
        """Hash bytes for extraction caching."""
        extension = Path(filename).suffix.lower().encode("utf-8")
        return hashlib.sha256(content + b"\0" + extension).hexdigest()

    def is_valid_file(self, filename: str, file_size: int) -> tuple[bool, str | None]:
        """Validate extension and size."""
        if file_size > self.max_file_size:
            return False, f"File size {file_size} exceeds maximum {self.max_file_size}"

        ext = Path(filename).suffix.lower().lstrip(".")
        if ext not in self.allowed_extensions:
            return (
                False,
                f"File extension '{ext}' not allowed. Allowed: {self.allowed_extensions}",
            )
        return True, None

    def extract_text(self, content: bytes, filename: str) -> str:
        """Extract text from supported format and cache results."""
        content_hash = self.get_content_hash(content, filename)
        if content_hash in self._content_cache:
            return self._content_cache[content_hash]

        is_valid, error = self.is_valid_file(filename, len(content))
        if not is_valid:
            raise ValueError(error or "Invalid file")

        try:
            text = extract_document_text(content, filename=filename)
        except Exception as exc:
            logger.error(
                "Error extracting document text filename=%s: %s", filename, exc
            )
            raise ValueError(
                f"Failed to extract text from {Path(filename).suffix}: {exc}"
            ) from exc

        if not text.strip():
            raise ValueError("No text could be extracted from document")

        self._content_cache[content_hash] = text
        return text
