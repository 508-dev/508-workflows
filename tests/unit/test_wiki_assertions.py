"""Tests for the separate bot assertion required by privileged wiki routes."""

from __future__ import annotations

import pytest

from five08.wiki_editing.assertions import (
    WikiAssertionError,
    create_wiki_action_assertion,
    verify_wiki_action_assertion,
)


def _payload() -> dict[str, object]:
    return {
        "context": {
            "discord_user_id": "writer-1",
            "roles": ["Steering Committee"],
        },
        "proposal_id": "proposal-1",
    }


def test_wiki_assertion_binds_method_path_and_complete_body() -> None:
    payload = _payload()
    assertion = create_wiki_action_assertion(
        "separate-wiki-secret",
        method="POST",
        path="/wiki/updates/proposal-1/publish",
        payload=payload,
        now=1_000,
    )

    verify_wiki_action_assertion(
        assertion,
        "separate-wiki-secret",
        method="POST",
        path="/wiki/updates/proposal-1/publish",
        payload=payload,
        now=1_030,
    )

    forged = _payload()
    forged["context"] = {
        "discord_user_id": "other-user",
        "roles": ["Admin"],
    }
    with pytest.raises(WikiAssertionError):
        verify_wiki_action_assertion(
            assertion,
            "separate-wiki-secret",
            method="POST",
            path="/wiki/updates/proposal-1/publish",
            payload=forged,
            now=1_030,
        )

    with pytest.raises(WikiAssertionError):
        verify_wiki_action_assertion(
            assertion,
            "separate-wiki-secret",
            method="POST",
            path="/wiki/updates/proposal-1/cancel",
            payload=payload,
            now=1_030,
        )


def test_wiki_assertion_rejects_expired_and_wrong_secret_tokens() -> None:
    assertion = create_wiki_action_assertion(
        "separate-wiki-secret",
        method="POST",
        path="/wiki/updates",
        payload=_payload(),
        now=1_000,
    )

    with pytest.raises(WikiAssertionError):
        verify_wiki_action_assertion(
            assertion,
            "separate-wiki-secret",
            method="POST",
            path="/wiki/updates",
            payload=_payload(),
            now=1_061,
        )
    with pytest.raises(WikiAssertionError):
        verify_wiki_action_assertion(
            assertion,
            "wrong-secret",
            method="POST",
            path="/wiki/updates",
            payload=_payload(),
            now=1_001,
        )
