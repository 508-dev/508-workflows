"""Shared TLS helpers for outbound HTTP clients."""

import os
import ssl
from typing import Iterable

import certifi


_COMMON_CA_BUNDLE_PATHS = (
    "/etc/ssl/cert.pem",
    "/private/etc/ssl/cert.pem",
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/ca-bundle.pem",
)


def _valid_ca_bundle_path(path: str | None) -> str | None:
    candidate = (path or "").strip()
    if candidate and os.path.isfile(candidate) and os.access(candidate, os.R_OK):
        return candidate
    return None


def _system_ca_bundle_candidates() -> Iterable[str | None]:
    default_paths = ssl.get_default_verify_paths()
    yield default_paths.cafile
    yield default_paths.openssl_cafile
    yield from _COMMON_CA_BUNDLE_PATHS


def default_ca_bundle_path() -> str:
    """Return a valid CA bundle path, ignoring stale inherited overrides."""
    for env_var in ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE"):
        candidate = _valid_ca_bundle_path(os.getenv(env_var))
        if candidate:
            return candidate

    certifi_path = certifi.where()
    candidate = _valid_ca_bundle_path(certifi_path)
    if candidate:
        return candidate

    for fallback in _system_ca_bundle_candidates():
        candidate = _valid_ca_bundle_path(fallback)
        if candidate:
            return candidate

    return certifi_path
