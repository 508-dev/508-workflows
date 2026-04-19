"""Unit tests for shared TLS helper behavior."""

from __future__ import annotations

import tempfile

import certifi

from five08.tls import default_ca_bundle_path


def test_default_ca_bundle_path_falls_back_when_env_path_is_stale(
    monkeypatch,
) -> None:
    """Stale inherited CA bundle paths should not break outbound requests."""
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/tmp/missing-ca.pem")
    default_ca_bundle_path.cache_clear()

    assert default_ca_bundle_path() == certifi.where()

    default_ca_bundle_path.cache_clear()


def test_default_ca_bundle_path_preserves_valid_env_override(monkeypatch) -> None:
    """Valid custom CA bundle paths should still be honored."""
    with tempfile.NamedTemporaryFile() as bundle:
        monkeypatch.setenv("REQUESTS_CA_BUNDLE", bundle.name)
        default_ca_bundle_path.cache_clear()

        assert default_ca_bundle_path() == bundle.name

    default_ca_bundle_path.cache_clear()
