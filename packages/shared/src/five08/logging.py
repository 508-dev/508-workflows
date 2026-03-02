"""Shared logging setup."""

from __future__ import annotations

from typing import Any
import logging

from .settings import SharedSettings


def configure_logging(level: str = "INFO") -> None:
    """Configure process-wide logging in a consistent way."""
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


def configure_observability(
    *,
    settings: SharedSettings,
    service_name: str,
    include_fastapi: bool = False,
) -> None:
    """Configure shared logging and optional Sentry initialization."""
    configure_logging(settings.log_level)

    logger = logging.getLogger(__name__)
    sentry_dsn = (settings.sentry_dsn or "").strip()
    if not sentry_dsn:
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
    except Exception:
        logger.warning(
            "Sentry SDK not available; skipping Sentry setup service=%s",
            service_name,
            exc_info=True,
        )
        return

    if hasattr(sentry_sdk, "is_initialized") and sentry_sdk.is_initialized():
        return

    integrations: list[Any] = [
        LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
    ]

    if include_fastapi:
        try:
            from sentry_sdk.integrations.fastapi import FastApiIntegration

            integrations.append(FastApiIntegration())
        except Exception:
            logger.warning(
                "Failed to load FastApiIntegration for Sentry service=%s",
                service_name,
                exc_info=True,
            )

    try:
        sentry_sdk.init(
            dsn=sentry_dsn,
            environment=settings.sentry_environment_name,
            release=(settings.sentry_release or "").strip() or None,
            sample_rate=settings.sentry_sample_rate,
            traces_sample_rate=settings.sentry_traces_sample_rate,
            profiles_sample_rate=settings.sentry_profiles_sample_rate,
            send_default_pii=settings.sentry_send_default_pii,
            debug=settings.sentry_debug,
            integrations=integrations,
        )
        sentry_sdk.set_tag("service", service_name)
        logger.info("Sentry initialized for service=%s", service_name)
    except Exception:
        logger.exception("Failed to initialize Sentry for service=%s", service_name)
