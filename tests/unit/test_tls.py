"""Unit tests for shared TLS helper behavior."""

from __future__ import annotations

import certifi

from five08.tls import default_ca_bundle_path


def test_default_ca_bundle_path_falls_back_when_env_path_is_stale(
    monkeypatch,
    tmp_path,
) -> None:
    """Stale inherited CA bundle paths should not break outbound requests."""
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(tmp_path / "missing-ca.pem"))
    default_ca_bundle_path.cache_clear()

    try:
        assert default_ca_bundle_path() == certifi.where()
    finally:
        default_ca_bundle_path.cache_clear()


def test_default_ca_bundle_path_falls_back_when_env_path_is_directory(
    monkeypatch,
    tmp_path,
) -> None:
    """Directory overrides should be ignored because requests expects a file."""
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(tmp_path))
    default_ca_bundle_path.cache_clear()

    try:
        assert default_ca_bundle_path() == certifi.where()
    finally:
        default_ca_bundle_path.cache_clear()


def test_default_ca_bundle_path_preserves_valid_env_override(
    monkeypatch,
    tmp_path,
) -> None:
    """Valid custom CA bundle paths should still be honored."""
    bundle = tmp_path / "custom-ca.pem"
    bundle.write_text("", encoding="utf-8")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(bundle))
    default_ca_bundle_path.cache_clear()

    try:
        assert default_ca_bundle_path() == str(bundle)
    finally:
        default_ca_bundle_path.cache_clear()
