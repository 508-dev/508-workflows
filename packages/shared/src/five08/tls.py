"""Shared TLS helpers for outbound HTTP clients."""

import os
from functools import lru_cache

import certifi


@lru_cache(maxsize=1)
def default_ca_bundle_path() -> str:
    """Return a valid CA bundle path, ignoring stale inherited overrides."""
    for env_var in ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE"):
        candidate = os.getenv(env_var, "").strip()
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.R_OK):
            return candidate
    return certifi.where()
