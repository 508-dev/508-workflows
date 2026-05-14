"""Unit tests for shared TLS helper behavior."""

from __future__ import annotations

from types import SimpleNamespace

import certifi

from five08 import tls
from five08.tls import default_ca_bundle_path


def test_default_ca_bundle_path_falls_back_when_env_path_is_stale(
    monkeypatch,
    tmp_path,
) -> None:
    """Stale inherited CA bundle paths should not break outbound requests."""
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(tmp_path / "missing-ca.pem"))

    assert default_ca_bundle_path() == certifi.where()


def test_default_ca_bundle_path_falls_back_when_env_path_is_directory(
    monkeypatch,
    tmp_path,
) -> None:
    """Directory overrides should be ignored because requests expects a file."""
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(tmp_path))

    assert default_ca_bundle_path() == certifi.where()


def test_default_ca_bundle_path_preserves_valid_env_override(
    monkeypatch,
    tmp_path,
) -> None:
    """Valid custom CA bundle paths should still be honored."""
    bundle = tmp_path / "custom-ca.pem"
    bundle.write_text("", encoding="utf-8")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(bundle))

    assert default_ca_bundle_path() == str(bundle)


def test_default_ca_bundle_path_falls_back_when_certifi_path_is_stale(
    monkeypatch,
    tmp_path,
) -> None:
    """Deleted workspace venvs should not leave requests pinned to stale certifi."""
    fallback_bundle = tmp_path / "system-ca.pem"
    fallback_bundle.write_text("", encoding="utf-8")
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("CURL_CA_BUNDLE", raising=False)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.setattr(certifi, "where", lambda: str(tmp_path / "missing-ca.pem"))
    monkeypatch.setattr(
        tls.ssl,
        "get_default_verify_paths",
        lambda: SimpleNamespace(
            cafile=str(fallback_bundle),
            openssl_cafile=str(tmp_path / "openssl-missing.pem"),
        ),
    )
    monkeypatch.setattr(tls, "_COMMON_CA_BUNDLE_PATHS", ())

    assert default_ca_bundle_path() == str(fallback_bundle)


def test_default_ca_bundle_path_revalidates_previous_result(
    monkeypatch,
    tmp_path,
) -> None:
    """A CA bundle deleted after startup should not remain cached."""
    certifi_bundle = tmp_path / "certifi-ca.pem"
    fallback_bundle = tmp_path / "system-ca.pem"
    certifi_bundle.write_text("", encoding="utf-8")
    fallback_bundle.write_text("", encoding="utf-8")
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("CURL_CA_BUNDLE", raising=False)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.setattr(certifi, "where", lambda: str(certifi_bundle))
    monkeypatch.setattr(
        tls.ssl,
        "get_default_verify_paths",
        lambda: SimpleNamespace(
            cafile=str(fallback_bundle),
            openssl_cafile=str(tmp_path / "openssl-missing.pem"),
        ),
    )
    monkeypatch.setattr(tls, "_COMMON_CA_BUNDLE_PATHS", ())

    assert default_ca_bundle_path() == str(certifi_bundle)

    certifi_bundle.unlink()

    assert default_ca_bundle_path() == str(fallback_bundle)
