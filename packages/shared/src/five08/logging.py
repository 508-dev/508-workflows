"""Shared logging setup."""

import logging


def configure_logging(level: str = "INFO") -> None:
    """Configure process-wide logging in a consistent way."""
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
