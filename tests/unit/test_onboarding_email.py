"""Tests for deterministic onboarding email generation."""

from __future__ import annotations

import pytest

from five08.onboarding_email import OnboardingEmailRequest, build_onboarding_email


def test_prospective_member_email_includes_contribution_but_not_member_steps() -> None:
    draft = build_onboarding_email(
        OnboardingEmailRequest(
            candidate_name=" Jane   Example ",
            sender_name="Michael Wu",
            has_contributed=False,
            discord_joined="no",
            membership_agreement_signed="unknown",
        )
    )

    assert draft.subject == "508.dev onboarding"
    assert draft.text_body.startswith("Great talking Jane,\n\n")
    assert draft.markdown_body.startswith("Great talking Jane,\n\n")
    assert "contribution requirement" in draft.text_body
    assert "https://wiki.508.dev/s/contributing-to-508" in draft.text_body
    assert (
        "[contributing to 508](https://wiki.508.dev/s/contributing-to-508)"
        in draft.markdown_body
    )
    assert (
        "[#prospective-members](https://discord.com/channels/1336096360772141148/1336628706160017469)"
        in draft.markdown_body
    )
    assert "DM @caleb or @michaelmwu" not in draft.text_body
    assert '<a href="https://discord.gg/9zAKxmUZJf">508 Discord server</a>' in (
        draft.html_body
    )
    assert "<p>Great talking Jane,</p>" in draft.html_body
    assert "<p>Cheers,<br>Michael Wu</p>" in draft.html_body


def test_new_member_email_omits_invite_and_agreement_when_already_done() -> None:
    draft = build_onboarding_email(
        OnboardingEmailRequest(
            candidate_name="Sam Member",
            sender_name="Caleb",
            has_contributed=True,
            discord_joined="yes",
            membership_agreement_signed="yes",
        )
    )

    assert "Since you have already joined Discord" in draft.text_body
    assert "https://discord.gg/9zAKxmUZJf" not in draft.text_body
    assert "membership agreement to fully onboard" not in draft.text_body
    assert "DM @caleb or @michaelmwu" in draft.text_body
    assert "[onboarding instructions](https://wiki.508.dev/s/onboarding)" in (
        draft.markdown_body
    )
    assert '<a href="https://wiki.508.dev/s/onboarding">' in draft.html_body


def test_invalid_state_is_rejected() -> None:
    with pytest.raises(ValueError, match="discord_joined"):
        build_onboarding_email(
            OnboardingEmailRequest(
                candidate_name="Sam",
                sender_name="Michael",
                has_contributed=True,
                discord_joined="maybe",  # type: ignore[arg-type]
            )
        )
