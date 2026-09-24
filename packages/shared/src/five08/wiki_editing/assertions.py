"""Short-lived, payload-bound bot assertions for privileged wiki actions.

The general API shared secret authenticates a calling service, but it must not
be enough to manufacture a Discord identity or role set for an approval-gated
write.  The Discord bot therefore signs the exact JSON body of each wiki
request with a separately scoped secret.  The API verifies that assertion
before it accepts the embedded actor context.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from typing import Any


WIKI_ASSERTION_HEADER = "X-Wiki-Assertion"
WIKI_ASSERTION_TTL_SECONDS = 60
_ASSERTION_VERSION = 1


class WikiAssertionError(ValueError):
    """A wiki action assertion is missing, malformed, expired, or mismatched."""


def create_wiki_action_assertion(
    secret: str,
    *,
    method: str,
    path: str,
    payload: Mapping[str, Any],
    now: int | None = None,
    ttl_seconds: int = WIKI_ASSERTION_TTL_SECONDS,
) -> str:
    """Sign one exact request body for the stated method/path and short TTL."""
    normalized_secret = _required_secret(secret)
    if ttl_seconds <= 0 or ttl_seconds > WIKI_ASSERTION_TTL_SECONDS:
        raise ValueError("wiki assertion TTL is outside the allowed boundary")
    issued_at = int(time.time() if now is None else now)
    claims = {
        "v": _ASSERTION_VERSION,
        "iat": issued_at,
        "exp": issued_at + ttl_seconds,
        "method": _normalized_method(method),
        "path": _normalized_path(path),
        "body_sha256": _payload_sha256(payload),
    }
    encoded_claims = _urlsafe_encode(_canonical_json(claims))
    signature = hmac.new(
        normalized_secret.encode("utf-8"),
        encoded_claims.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{encoded_claims}.{_urlsafe_encode(signature)}"


def verify_wiki_action_assertion(
    assertion: str | None,
    secret: str,
    *,
    method: str,
    path: str,
    payload: Mapping[str, Any],
    now: int | None = None,
) -> None:
    """Verify a short-lived assertion without returning sensitive diagnostics."""
    normalized_secret = _required_secret(secret)
    if not isinstance(assertion, str) or len(assertion) > 4_096:
        raise WikiAssertionError("missing assertion")
    encoded_claims, separator, encoded_signature = assertion.partition(".")
    if not separator or not encoded_claims or not encoded_signature:
        raise WikiAssertionError("malformed assertion")
    try:
        raw_claims = _urlsafe_decode(encoded_claims)
        claims = json.loads(raw_claims)
        received_signature = _urlsafe_decode(encoded_signature)
    except (
        UnicodeDecodeError,
        UnicodeEncodeError,
        ValueError,
        binascii.Error,
        json.JSONDecodeError,
    ) as exc:
        raise WikiAssertionError("malformed assertion") from exc
    if not isinstance(claims, dict) or set(claims) != {
        "v",
        "iat",
        "exp",
        "method",
        "path",
        "body_sha256",
    }:
        raise WikiAssertionError("malformed assertion")
    if _canonical_json(claims) != raw_claims:
        # Reject alternate JSON spellings so the signed compact form is unique.
        raise WikiAssertionError("noncanonical assertion")
    expected_signature = hmac.new(
        normalized_secret.encode("utf-8"),
        encoded_claims.encode("ascii"),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(received_signature, expected_signature):
        raise WikiAssertionError("invalid assertion signature")

    issued_at = claims.get("iat")
    expires_at = claims.get("exp")
    if (
        claims.get("v") != _ASSERTION_VERSION
        or isinstance(issued_at, bool)
        or isinstance(expires_at, bool)
        or not isinstance(issued_at, int)
        or not isinstance(expires_at, int)
        or expires_at - issued_at <= 0
        or expires_at - issued_at > WIKI_ASSERTION_TTL_SECONDS
    ):
        raise WikiAssertionError("invalid assertion claims")
    current_time = int(time.time() if now is None else now)
    # A small clock-skew allowance only applies before issuance; expiry remains
    # strict to keep captured approval assertions short lived.
    if issued_at > current_time + 5 or current_time > expires_at:
        raise WikiAssertionError("expired assertion")
    if (
        claims.get("method") != _normalized_method(method)
        or claims.get("path") != _normalized_path(path)
        or not isinstance(claims.get("body_sha256"), str)
        or not hmac.compare_digest(claims["body_sha256"], _payload_sha256(payload))
    ):
        raise WikiAssertionError("assertion does not match request")


def _payload_sha256(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise WikiAssertionError("assertion payload is not JSON-safe") from exc


def _urlsafe_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _urlsafe_decode(value: str) -> bytes:
    if not value or any(character.isspace() for character in value):
        raise ValueError("invalid base64url data")
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)


def _required_secret(secret: str) -> str:
    normalized = secret.strip()
    if not normalized:
        raise WikiAssertionError("wiki assertion secret is not configured")
    return normalized


def _normalized_method(value: str) -> str:
    normalized = value.strip().upper()
    if not normalized or len(normalized) > 16:
        raise WikiAssertionError("invalid assertion method")
    return normalized


def _normalized_path(value: str) -> str:
    normalized = value.strip()
    if not normalized.startswith("/") or len(normalized) > 2_000:
        raise WikiAssertionError("invalid assertion path")
    return normalized
