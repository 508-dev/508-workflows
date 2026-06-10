"""Tests for the Discord onboarding email command."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest

from five08.clients.espo import EspoAPIError
from five08.discord_bot.cogs.onboarding_email import (
    OnboardingEmailCog,
    OnboardingEmailCommandState,
    OnboardingEmailContactSelectView,
    OnboardingEmailDraftEditView,
    OnboardingEmailSendPayload,
)


@pytest.fixture
def mock_bot() -> Mock:
    bot = Mock()
    return bot


@pytest.fixture
def onboarding_cog(mock_bot: Mock) -> OnboardingEmailCog:
    patcher = patch("five08.discord_bot.cogs.onboarding_email.settings")
    mock_settings = patcher.start()
    mock_settings.espo_api_key = "token"
    mock_settings.espo_base_url = "https://crm.example.com"
    mock_settings.audit_api_base_url = "https://audit.example.com"
    mock_settings.api_shared_secret = "secret"
    mock_settings.audit_api_timeout_seconds = 5.0
    mock_settings.discord_logs_webhook_url = None
    mock_settings.discord_logs_webhook_wait = False
    mock_settings.onboarding_email_sender_email = "onboarding@508.dev"
    mock_settings.onboarding_email_smtp_server = "smtp.migadu.com"
    mock_settings.onboarding_email_smtp_port = 465
    mock_settings.onboarding_email_smtp_use_ssl = True
    mock_settings.onboarding_email_smtp_starttls = False
    mock_settings.onboarding_email_smtp_username = "onboarding@508.dev"
    mock_settings.onboarding_email_smtp_password = "secret"
    mock_settings.onboarding_email_smtp_timeout_seconds = 20.0
    cog = OnboardingEmailCog(mock_bot)
    cog.crm = Mock()
    try:
        yield cog
    finally:
        patcher.stop()


@pytest.fixture
def mock_interaction() -> AsyncMock:
    interaction = AsyncMock()
    interaction.response = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.user = Mock()
    interaction.user.id = 123
    interaction.user.name = "michaelmwu"
    interaction.user.display_name = "Michael Wu"
    admin_role = Mock()
    admin_role.name = "Admin"
    interaction.user.roles = [admin_role]
    interaction.message = None
    return interaction


def _contact_search_response(params: dict[str, object]) -> dict[str, object]:
    select = str(params.get("select") or "")
    if "cDiscordUserID" in select:
        return {
            "list": [
                {
                    "id": "contact-user",
                    "name": "Michael Wu",
                    "c508Email": "michael@508.dev",
                    "emailAddress": "michael@example.com",
                    "cDiscordUserID": "123",
                    "cDiscordUsername": "michaelmwu",
                }
            ]
        }
    if "cOnboarder" in select:
        return {
            "list": [
                {
                    "id": "contact-candidate",
                    "name": "Jane Example",
                    "emailAddress": "jane@example.com",
                    "c508Email": "",
                    "cOnboarder": "michael",
                    "cOnboardingState": "selected",
                }
            ]
        }
    return {"list": []}


def test_candidate_lookup_keeps_name_filter_when_recipient_is_overridden(
    onboarding_cog: OnboardingEmailCog,
) -> None:
    onboarding_cog.crm.list_contacts.return_value = {
        "list": [
            {
                "id": "contact-candidate",
                "name": "Jane Example",
                "emailAddress": "jane@example.com",
                "c508Email": "",
                "cOnboarder": "michael",
                "cOnboardingState": "selected",
            }
        ]
    }

    contacts = onboarding_cog._search_candidate_contacts(
        candidate_name="Jane Example",
        recipient_email="jane.personal@example.com",
    )

    assert contacts[0]["id"] == "contact-candidate"
    params = onboarding_cog.crm.list_contacts.call_args.args[0]
    filters = params["where"][0]["value"]  # type: ignore[index]
    assert {
        "type": "contains",
        "attribute": "name",
        "value": "Jane Example",
    } in filters
    assert {
        "type": "equals",
        "attribute": "emailAddress",
        "value": "jane.personal@example.com",
    } in filters


@pytest.mark.asyncio
async def test_onboarding_email_command_generates_draft(
    onboarding_cog: OnboardingEmailCog,
    mock_interaction: AsyncMock,
) -> None:
    onboarding_cog._audit_command_safe = Mock()
    onboarding_cog.crm.list_contacts.side_effect = _contact_search_response
    mock_interaction.user.display_name = "michaelmwu"

    await onboarding_cog.onboarding_email.callback(
        onboarding_cog,
        mock_interaction,
        "Jane Example",
        False,
        discord_joined="no",
        agreement_signed="unknown",
    )

    args, kwargs = mock_interaction.followup.send.call_args
    assert "Onboarding email draft generated" in args[0]
    assert "Reply-To: `michael@508.dev`" in args[0]
    assert "From: `Michael Wu <onboarding@508.dev>`" in args[0]
    assert "CRM contact: `Jane Example`" in args[0]
    assert "**Copy/paste draft:**" in args[0]
    assert "Cheers,\nMichael" in args[0]
    assert "[508 Discord server](https://discord.gg/9zAKxmUZJf)" in args[0]
    assert kwargs["ephemeral"] is True
    assert isinstance(kwargs["view"], OnboardingEmailDraftEditView)
    assert "files" not in kwargs
    assert kwargs["view"].send_payload is not None
    audit_kwargs = onboarding_cog._audit_command_safe.call_args.kwargs
    assert audit_kwargs["result"] == "success"
    assert audit_kwargs["metadata"]["email_action"] == "drafted_for_send"
    assert audit_kwargs["metadata"]["send_available"] is True
    assert audit_kwargs["metadata"]["sender_display_name"] == "Michael Wu"
    assert audit_kwargs["metadata"]["signature_name"] == "Michael"


@pytest.mark.asyncio
async def test_onboarding_email_command_prepares_send_button_when_possible(
    onboarding_cog: OnboardingEmailCog,
    mock_interaction: AsyncMock,
) -> None:
    onboarding_cog._audit_command_safe = Mock()
    onboarding_cog.crm.list_contacts.side_effect = _contact_search_response
    onboarding_cog.crm.get_contact.return_value = {
        "id": "contact-candidate",
        "name": "Sam Member",
        "emailAddress": "sam@example.com",
        "c508Email": "",
        "cOnboarder": "michael",
        "cOnboardingState": "selected",
    }
    onboarding_cog._send_message = Mock()

    await onboarding_cog.onboarding_email.callback(
        onboarding_cog,
        mock_interaction,
        "Sam Member",
        True,
        recipient_email="sam@example.com",
        discord_joined="yes",
        agreement_signed="no",
        sender_name="Michael Wu",
        reply_to_email="michael@508.dev",
    )

    onboarding_cog._send_message.assert_not_called()
    send_args, send_kwargs = mock_interaction.followup.send.call_args
    assert "Review, edit, then press Send Email" in send_args[0]
    view = send_kwargs["view"]
    assert isinstance(view, OnboardingEmailDraftEditView)
    assert view.send_payload is not None
    first_audit_kwargs = onboarding_cog._audit_command_safe.call_args.kwargs
    assert first_audit_kwargs["result"] == "success"
    assert first_audit_kwargs["metadata"]["email_action"] == "drafted_for_send"

    await view.send_draft(mock_interaction)

    onboarding_cog._send_message.assert_called_once()
    message = onboarding_cog._send_message.call_args.args[0]
    assert message["From"] == "Michael Wu <onboarding@508.dev>"
    assert message["Reply-To"] == "Michael Wu <michael@508.dev>"
    assert message["To"] == "sam@example.com"
    assert message["Subject"] == "508.dev onboarding"
    assert "Onboarding email sent" in mock_interaction.followup.send.call_args.args[0]
    audit_kwargs = onboarding_cog._audit_command_safe.call_args.kwargs
    assert audit_kwargs["result"] == "success"
    assert audit_kwargs["metadata"]["email_action"] == "sent"


@pytest.mark.asyncio
async def test_review_send_uses_edited_markdown_body(
    onboarding_cog: OnboardingEmailCog,
    mock_interaction: AsyncMock,
) -> None:
    onboarding_cog._audit_command_safe = Mock()
    onboarding_cog._send_message = Mock()
    payload = OnboardingEmailSendPayload(
        recipient_email="sam@example.com",
        reply_to_email="michael@508.dev",
        sender_display_name="Michael Wu",
        subject="508.dev onboarding",
        recipient_from_crm=False,
        original_markdown_body="Original draft\n",
        original_text_body="Original draft\n",
        original_html_body="<p>Original draft</p>",
        candidate_name="Sam Member",
        contact_id=None,
        has_contributed=True,
        discord_joined="yes",
        agreement_signed="no",
        authorization_source="steering_committee",
        onboarding_status="selected",
    )
    view = OnboardingEmailDraftEditView(
        cog=onboarding_cog,
        requester_id=mock_interaction.user.id,
        summary="📝 Onboarding email draft generated.",
        markdown_body="Original draft\n",
        send_payload=payload,
    )

    await view.update_draft(
        mock_interaction,
        "Edited draft with a [wiki](https://wiki.508.dev/) link.\n",
    )
    await view.send_draft(mock_interaction)

    message = onboarding_cog._send_message.call_args.args[0]
    assert (
        "Edited draft with a wiki (https://wiki.508.dev/) link."
        in message.get_body(preferencelist=("plain",)).get_content()
    )
    assert (
        '<a href="https://wiki.508.dev/">wiki</a>'
        in message.get_body(preferencelist=("html",)).get_content()
    )
    audit_kwargs = onboarding_cog._audit_command_safe.call_args.kwargs
    assert audit_kwargs["metadata"]["email_action"] == "sent"
    assert audit_kwargs["metadata"]["edited"] is True


@pytest.mark.asyncio
async def test_review_send_refreshes_crm_derived_recipient_before_smtp(
    onboarding_cog: OnboardingEmailCog,
    mock_interaction: AsyncMock,
) -> None:
    onboarding_cog._audit_command_safe = Mock()
    onboarding_cog._send_message = Mock()
    onboarding_cog.crm.get_contact.return_value = {
        "id": "contact-sam",
        "name": "Sam Member",
        "emailAddress": "fresh@example.com",
        "c508Email": "",
        "cOnboarder": "michael",
        "cOnboardingState": "selected",
    }
    payload = OnboardingEmailSendPayload(
        recipient_email="stale@example.com",
        reply_to_email="michael@508.dev",
        sender_display_name="Michael Wu",
        subject="508.dev onboarding",
        recipient_from_crm=True,
        original_markdown_body="Original draft\n",
        original_text_body="Original draft\n",
        original_html_body="<p>Original draft</p>",
        candidate_name="Sam Member",
        contact_id="contact-sam",
        has_contributed=True,
        discord_joined="yes",
        agreement_signed="no",
        authorization_source="steering_committee",
        onboarding_status="selected",
    )
    view = OnboardingEmailDraftEditView(
        cog=onboarding_cog,
        requester_id=mock_interaction.user.id,
        summary="📝 Onboarding email draft generated.",
        markdown_body="Original draft\n",
        send_payload=payload,
    )

    await view.send_draft(mock_interaction)

    onboarding_cog.crm.get_contact.assert_called_once_with("contact-sam")
    message = onboarding_cog._send_message.call_args.args[0]
    assert message["To"] == "fresh@example.com"
    audit_kwargs = onboarding_cog._audit_command_safe.call_args.kwargs
    assert audit_kwargs["metadata"]["recipient_email"] == "fresh@example.com"


@pytest.mark.asyncio
async def test_review_send_revalidates_designated_onboarder_assignment(
    onboarding_cog: OnboardingEmailCog,
    mock_interaction: AsyncMock,
) -> None:
    onboarding_cog._audit_command_safe = Mock()
    onboarding_cog._send_message = Mock()
    member_role = Mock()
    member_role.name = "Member"
    mock_interaction.user.roles = [member_role]
    onboarding_cog.crm.get_contact.return_value = {
        "id": "contact-sam",
        "name": "Sam Member",
        "emailAddress": "fresh@example.com",
        "c508Email": "",
        "cOnboarder": "caleb",
        "cOnboardingState": "selected",
    }
    onboarding_cog.crm.list_contacts.return_value = {
        "list": [
            {
                "id": "contact-user",
                "name": "Michael Wu",
                "c508Email": "michael@508.dev",
                "cDiscordUserID": "123",
            }
        ]
    }
    payload = OnboardingEmailSendPayload(
        recipient_email="stale@example.com",
        reply_to_email="michael@508.dev",
        sender_display_name="Michael Wu",
        subject="508.dev onboarding",
        recipient_from_crm=True,
        original_markdown_body="Original draft\n",
        original_text_body="Original draft\n",
        original_html_body="<p>Original draft</p>",
        candidate_name="Sam Member",
        contact_id="contact-sam",
        has_contributed=True,
        discord_joined="yes",
        agreement_signed="no",
        authorization_source="designated_onboarder",
        onboarding_status="selected",
    )
    view = OnboardingEmailDraftEditView(
        cog=onboarding_cog,
        requester_id=mock_interaction.user.id,
        summary="📝 Onboarding email draft generated.",
        markdown_body="Original draft\n",
        send_payload=payload,
    )

    await view.send_draft(mock_interaction)

    onboarding_cog._send_message.assert_not_called()
    message = mock_interaction.followup.send.call_args.args[0]
    assert "Only Steering Committee+ or the candidate's designated onboarder" in message
    audit_kwargs = onboarding_cog._audit_command_safe.call_args.kwargs
    assert audit_kwargs["result"] == "denied"
    assert audit_kwargs["metadata"]["email_action"] == "send_failed"


@pytest.mark.asyncio
async def test_review_send_blocks_terminal_onboarding_state(
    onboarding_cog: OnboardingEmailCog,
    mock_interaction: AsyncMock,
) -> None:
    onboarding_cog._audit_command_safe = Mock()
    onboarding_cog._send_message = Mock()
    onboarding_cog.crm.get_contact.return_value = {
        "id": "contact-sam",
        "name": "Sam Member",
        "emailAddress": "fresh@example.com",
        "c508Email": "",
        "cOnboarder": "michael",
        "cOnboardingState": "rejected",
    }
    payload = OnboardingEmailSendPayload(
        recipient_email="stale@example.com",
        reply_to_email="michael@508.dev",
        sender_display_name="Michael Wu",
        subject="508.dev onboarding",
        recipient_from_crm=True,
        original_markdown_body="Original draft\n",
        original_text_body="Original draft\n",
        original_html_body="<p>Original draft</p>",
        candidate_name="Sam Member",
        contact_id="contact-sam",
        has_contributed=True,
        discord_joined="yes",
        agreement_signed="no",
        authorization_source="steering_committee",
        onboarding_status="selected",
    )
    view = OnboardingEmailDraftEditView(
        cog=onboarding_cog,
        requester_id=mock_interaction.user.id,
        summary="📝 Onboarding email draft generated.",
        markdown_body="Original draft\n",
        send_payload=payload,
    )

    await view.send_draft(mock_interaction)

    onboarding_cog._send_message.assert_not_called()
    message = mock_interaction.followup.send.call_args.args[0]
    assert "terminal onboarding state rejected" in message
    audit_kwargs = onboarding_cog._audit_command_safe.call_args.kwargs
    assert audit_kwargs["result"] == "denied"


@pytest.mark.asyncio
async def test_onboarding_email_draft_does_not_require_crm_reply_to_lookup(
    onboarding_cog: OnboardingEmailCog,
    mock_interaction: AsyncMock,
) -> None:
    onboarding_cog._audit_command_safe = Mock()
    onboarding_cog.crm.list_contacts.side_effect = EspoAPIError("CRM unavailable")

    await onboarding_cog.onboarding_email.callback(
        onboarding_cog,
        mock_interaction,
        "Jane Example",
        False,
    )

    assert (
        "Onboarding email draft generated"
        in mock_interaction.followup.send.call_args.args[0]
    )
    assert (
        "Reply-To: `not resolved`" in mock_interaction.followup.send.call_args.args[0]
    )


@pytest.mark.asyncio
async def test_onboarding_email_summary_escapes_inline_code_values(
    onboarding_cog: OnboardingEmailCog,
    mock_interaction: AsyncMock,
) -> None:
    onboarding_cog._audit_command_safe = Mock()
    state = OnboardingEmailCommandState(
        candidate_name="Jane `Tick`",
        has_contributed=False,
        recipient_email="jane@example.com",
        discord_joined="unknown",
        agreement_signed="unknown",
        sender_display_name="Michael `Wu`",
        signature_name="Michael",
        reply_to_email="michael@508.dev",
    )
    selected_contact = {
        "id": "contact-`jane`",
        "name": "Jane `Tick`",
        "emailAddress": "jane@example.com",
        "cOnboarder": "michael",
        "cOnboardingState": "selected`now",
    }

    await onboarding_cog._complete_onboarding_email(
        mock_interaction,
        state=state,
        selected_contact=selected_contact,
    )

    message = mock_interaction.followup.send.call_args.args[0]
    assert "From: `Michael 'Wu' <onboarding@508.dev>`" in message
    assert "CRM contact: `Jane 'Tick'` (`contact-'jane'`)" in message
    assert "status: `selected'now`" in message


@pytest.mark.asyncio
async def test_multiple_candidate_matches_show_selector(
    onboarding_cog: OnboardingEmailCog,
    mock_interaction: AsyncMock,
) -> None:
    onboarding_cog._audit_command_safe = Mock()
    onboarding_cog.crm.list_contacts.return_value = {
        "list": [
            {
                "id": "contact-1",
                "name": "Jane Example",
                "emailAddress": "jane@example.com",
                "c508Email": "",
                "cOnboarder": "michael",
                "cOnboardingState": "selected",
            },
            {
                "id": "contact-2",
                "name": "Jane Onboarded",
                "emailAddress": "jane2@example.com",
                "c508Email": "jane@508.dev",
                "cOnboarder": "caleb",
                "cOnboardingState": "onboarded",
            },
        ]
    }

    await onboarding_cog.onboarding_email.callback(
        onboarding_cog,
        mock_interaction,
        "Jane",
        False,
    )

    args, kwargs = mock_interaction.followup.send.call_args
    assert "Multiple CRM contacts match" in args[0]
    assert "Already-onboarded contacts" in args[0]
    assert isinstance(kwargs["view"], OnboardingEmailContactSelectView)
    assert kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_multiple_candidate_matches_are_filtered_for_designated_onboarder(
    onboarding_cog: OnboardingEmailCog,
    mock_interaction: AsyncMock,
) -> None:
    onboarding_cog._audit_command_safe = Mock()
    member_role = Mock()
    member_role.name = "Member"
    mock_interaction.user.roles = [member_role]

    def list_contacts(params: dict[str, object]) -> dict[str, object]:
        select = str(params.get("select") or "")
        if "cDiscordUserID" in select:
            return {
                "list": [
                    {
                        "id": "contact-user",
                        "name": "Michael Wu",
                        "c508Email": "michael@508.dev",
                        "cDiscordUserID": "123",
                    }
                ]
            }
        if "cOnboarder" in select:
            return {
                "list": [
                    {
                        "id": "contact-1",
                        "name": "Jane Assigned",
                        "emailAddress": "jane@example.com",
                        "cOnboarder": "michael",
                        "cOnboardingState": "selected",
                    },
                    {
                        "id": "contact-2",
                        "name": "Jane Also Assigned",
                        "emailAddress": "jane2@example.com",
                        "cOnboarder": "michael@508.dev",
                        "cOnboardingState": "selected",
                    },
                    {
                        "id": "contact-3",
                        "name": "Jane Other",
                        "emailAddress": "jane3@example.com",
                        "cOnboarder": "caleb",
                        "cOnboardingState": "selected",
                    },
                ]
            }
        return {"list": []}

    onboarding_cog.crm.list_contacts.side_effect = list_contacts

    await onboarding_cog.onboarding_email.callback(
        onboarding_cog,
        mock_interaction,
        "Jane",
        False,
    )

    args, kwargs = mock_interaction.followup.send.call_args
    assert "Multiple CRM contacts match" in args[0]
    view = kwargs["view"]
    assert isinstance(view, OnboardingEmailContactSelectView)
    select = view.children[0]
    option_labels = [option.label for option in select.options]
    assert option_labels == ["Jane Assigned", "Jane Also Assigned"]


@pytest.mark.parametrize("status", ["onboarded", "waitlist", "rejected"])
@pytest.mark.asyncio
async def test_terminal_onboarding_state_is_reported_without_draft(
    status: str,
    onboarding_cog: OnboardingEmailCog,
    mock_interaction: AsyncMock,
) -> None:
    onboarding_cog._audit_command_safe = Mock()
    onboarding_cog.crm.list_contacts.return_value = {
        "list": [
            {
                "id": "contact-onboarded",
                "name": "Jane Onboarded",
                "emailAddress": "jane@example.com",
                "c508Email": "jane@508.dev",
                "cOnboarder": "michael",
                "cOnboardingState": status,
            }
        ]
    }

    await onboarding_cog.onboarding_email.callback(
        onboarding_cog,
        mock_interaction,
        "Jane",
        False,
    )

    args, kwargs = mock_interaction.followup.send.call_args
    assert "terminal onboarding state" in args[0]
    assert f"`{status}`" in args[0]
    assert "files" not in kwargs
    metadata = onboarding_cog._audit_command_safe.call_args.kwargs["metadata"]
    assert metadata["error"] == "candidate_terminal_onboarding_state"


@pytest.mark.asyncio
async def test_designated_onboarder_can_generate_candidate_email(
    onboarding_cog: OnboardingEmailCog,
    mock_interaction: AsyncMock,
) -> None:
    onboarding_cog._audit_command_safe = Mock()
    member_role = Mock()
    member_role.name = "Member"
    mock_interaction.user.roles = [member_role]
    onboarding_cog.crm.list_contacts.side_effect = _contact_search_response

    await onboarding_cog.onboarding_email.callback(
        onboarding_cog,
        mock_interaction,
        "Jane Example",
        False,
        recipient_email="jane@example.com",
    )

    assert (
        "Onboarding email draft generated"
        in mock_interaction.followup.send.call_args.args[0]
    )
    metadata = onboarding_cog._audit_command_safe.call_args.kwargs["metadata"]
    assert metadata["authorization_source"] == "designated_onboarder"


@pytest.mark.asyncio
async def test_non_onboarder_member_is_denied(
    onboarding_cog: OnboardingEmailCog,
    mock_interaction: AsyncMock,
) -> None:
    onboarding_cog._audit_command_safe = Mock()
    member_role = Mock()
    member_role.name = "Member"
    mock_interaction.user.roles = [member_role]

    def list_contacts(params: dict[str, object]) -> dict[str, object]:
        response = _contact_search_response(params)
        select = str(params.get("select") or "")
        if "cOnboarder" in select:
            contact = response["list"][0]  # type: ignore[index]
            contact["cOnboarder"] = "caleb"  # type: ignore[index]
        return response

    onboarding_cog.crm.list_contacts.side_effect = list_contacts

    await onboarding_cog.onboarding_email.callback(
        onboarding_cog,
        mock_interaction,
        "Jane Example",
        False,
        recipient_email="jane@example.com",
    )

    message = mock_interaction.followup.send.call_args.args[0]
    assert "Only Steering Committee+ or the candidate's designated onboarder" in message
    audit_kwargs = onboarding_cog._audit_command_safe.call_args.kwargs
    assert audit_kwargs["result"] == "denied"
    assert audit_kwargs["metadata"]["error"] == "permission_denied"


@pytest.mark.asyncio
async def test_designated_onboarder_authorization_requires_discord_user_id_link(
    onboarding_cog: OnboardingEmailCog,
    mock_interaction: AsyncMock,
) -> None:
    onboarding_cog._audit_command_safe = Mock()
    member_role = Mock()
    member_role.name = "Member"
    mock_interaction.user.roles = [member_role]
    mock_interaction.user.id = 999
    mock_interaction.user.name = "michaelmwu"
    mock_interaction.user.display_name = "Michael Wu"

    def list_contacts(params: dict[str, object]) -> dict[str, object]:
        select = str(params.get("select") or "")
        filters = str(params.get("where") or "")
        if "cDiscordUserID" in select and "cDiscordUserID" in filters:
            return {"list": []}
        if "cDiscordUsername" in select and "cDiscordUsername" in filters:
            return {
                "list": [
                    {
                        "id": "contact-user",
                        "name": "Michael Wu",
                        "c508Email": "michael@508.dev",
                        "cDiscordUsername": "michaelmwu",
                    }
                ]
            }
        if "cOnboarder" in select:
            return {
                "list": [
                    {
                        "id": "contact-candidate",
                        "name": "Jane Example",
                        "emailAddress": "jane@example.com",
                        "cOnboarder": "michael",
                        "cOnboardingState": "selected",
                    }
                ]
            }
        return {"list": []}

    onboarding_cog.crm.list_contacts.side_effect = list_contacts

    await onboarding_cog.onboarding_email.callback(
        onboarding_cog,
        mock_interaction,
        "Jane Example",
        False,
    )

    message = mock_interaction.followup.send.call_args.args[0]
    assert "Only Steering Committee+ or the candidate's designated onboarder" in message
    audit_kwargs = onboarding_cog._audit_command_safe.call_args.kwargs
    assert audit_kwargs["result"] == "denied"
    assert audit_kwargs["metadata"]["error"] == "permission_denied"


@pytest.mark.asyncio
async def test_unauthorized_selection_authorizes_before_draft_and_reply_to_lookup(
    onboarding_cog: OnboardingEmailCog,
    mock_interaction: AsyncMock,
) -> None:
    onboarding_cog._audit_command_safe = Mock()
    member_role = Mock()
    member_role.name = "Member"
    mock_interaction.user.roles = [member_role]
    state = OnboardingEmailCommandState(
        candidate_name="Jane Example",
        has_contributed=False,
        recipient_email="jane@example.com",
        discord_joined="unknown",
        agreement_signed="unknown",
        sender_display_name="Michael Wu",
        signature_name="Michael",
        reply_to_email=None,
    )
    selected_contact = {
        "id": "contact-candidate",
        "name": "Jane Example",
        "emailAddress": "jane@example.com",
        "cOnboarder": "caleb",
        "cOnboardingState": "selected",
    }
    onboarding_cog.crm.list_contacts.return_value = {
        "list": [
            {
                "id": "contact-user",
                "name": "Michael Wu",
                "c508Email": "michael@508.dev",
                "cDiscordUserID": "123",
            }
        ]
    }

    with (
        patch(
            "five08.discord_bot.cogs.onboarding_email.build_onboarding_email"
        ) as build_mock,
        patch.object(onboarding_cog, "_reply_to_email_for_user") as reply_to_mock,
    ):
        await onboarding_cog._run_onboarding_email_flow(
            mock_interaction,
            state=state,
            selected_contact=selected_contact,
        )

    build_mock.assert_not_called()
    reply_to_mock.assert_not_called()
    audit_kwargs = onboarding_cog._audit_command_safe.call_args.kwargs
    assert audit_kwargs["result"] == "denied"
    assert audit_kwargs["metadata"]["error"] == "permission_denied"


@pytest.mark.asyncio
async def test_onboarding_email_without_reply_to_generates_copy_only_draft(
    onboarding_cog: OnboardingEmailCog,
    mock_interaction: AsyncMock,
) -> None:
    onboarding_cog._audit_command_safe = Mock()

    def list_contacts(params: dict[str, object]) -> dict[str, object]:
        select = str(params.get("select") or "")
        if "cOnboarder" in select:
            return {
                "list": [
                    {
                        "id": "contact-candidate",
                        "name": "Sam Member",
                        "emailAddress": "sam@example.com",
                        "c508Email": "",
                        "cOnboarder": "michael",
                        "cOnboardingState": "selected",
                    }
                ]
            }
        return {"list": []}

    onboarding_cog.crm.list_contacts.side_effect = list_contacts
    onboarding_cog._send_message = Mock()

    await onboarding_cog.onboarding_email.callback(
        onboarding_cog,
        mock_interaction,
        "Sam Member",
        True,
        recipient_email="sam@example.com",
    )

    onboarding_cog._send_message.assert_not_called()
    args, kwargs = mock_interaction.followup.send.call_args
    assert "Onboarding email draft generated" in args[0]
    assert "Review, edit, then press Send Email" not in args[0]
    assert isinstance(kwargs["view"], OnboardingEmailDraftEditView)
    assert kwargs["view"].send_payload is None
    audit_kwargs = onboarding_cog._audit_command_safe.call_args.kwargs
    assert audit_kwargs["result"] == "success"
    assert audit_kwargs["metadata"]["email_action"] == "drafted"
    assert audit_kwargs["metadata"]["send_available"] is False


@pytest.mark.asyncio
async def test_sender_identity_falls_back_to_crm_discord_username(
    onboarding_cog: OnboardingEmailCog,
    mock_interaction: AsyncMock,
) -> None:
    onboarding_cog._audit_command_safe = Mock()

    def list_contacts(params: dict[str, object]) -> dict[str, object]:
        select = str(params.get("select") or "")
        filters = str(params.get("where") or "")
        if "cDiscordUserID" in select and "cDiscordUserID" in filters:
            return {"list": []}
        if "cDiscordUsername" in select and "cDiscordUsername" in filters:
            return {
                "list": [
                    {
                        "id": "contact-user",
                        "name": "Michael Wu",
                        "c508Email": "michael@508.dev",
                        "cDiscordUsername": "michaelmwu",
                    }
                ]
            }
        if "cOnboarder" in select:
            return {
                "list": [
                    {
                        "id": "contact-candidate",
                        "name": "Jane Example",
                        "emailAddress": "jane@example.com",
                        "cOnboarder": "michael",
                        "cOnboardingState": "selected",
                    }
                ]
            }
        return {"list": []}

    onboarding_cog.crm.list_contacts.side_effect = list_contacts

    await onboarding_cog.onboarding_email.callback(
        onboarding_cog,
        mock_interaction,
        "Jane Example",
        False,
    )

    message = mock_interaction.followup.send.call_args.args[0]
    assert "From: `Michael Wu <onboarding@508.dev>`" in message
    assert "Cheers,\nMichael" in message


@pytest.mark.asyncio
async def test_onboarding_email_flow_sanitizes_crm_errors(
    onboarding_cog: OnboardingEmailCog,
    mock_interaction: AsyncMock,
) -> None:
    onboarding_cog._audit_command_safe = Mock()
    state = OnboardingEmailCommandState(
        candidate_name="Jane Example",
        has_contributed=False,
        recipient_email="jane@example.com",
        discord_joined="unknown",
        agreement_signed="unknown",
        sender_display_name="Michael Wu",
        signature_name="Michael",
        reply_to_email=None,
    )
    onboarding_cog._complete_onboarding_email = AsyncMock(
        side_effect=EspoAPIError("token leaked raw CRM detail")
    )

    await onboarding_cog._run_onboarding_email_flow(
        mock_interaction,
        state=state,
        selected_contact=None,
    )

    message = mock_interaction.followup.send.call_args.args[0]
    assert "CRM lookup failed" in message
    assert "token leaked" not in message
    audit_kwargs = onboarding_cog._audit_command_safe.call_args.kwargs
    assert audit_kwargs["result"] == "error"
    assert audit_kwargs["metadata"]["error"] == "crm_lookup_failed"


@pytest.mark.asyncio
async def test_onboarding_email_flow_handles_unexpected_errors(
    onboarding_cog: OnboardingEmailCog,
    mock_interaction: AsyncMock,
) -> None:
    onboarding_cog._audit_command_safe = Mock()
    state = OnboardingEmailCommandState(
        candidate_name="Jane Example",
        has_contributed=False,
        recipient_email="jane@example.com",
        discord_joined="unknown",
        agreement_signed="unknown",
        sender_display_name="Michael Wu",
        signature_name="Michael",
        reply_to_email=None,
    )
    onboarding_cog._complete_onboarding_email = AsyncMock(
        side_effect=RuntimeError("unexpected internal detail")
    )

    await onboarding_cog._run_onboarding_email_flow(
        mock_interaction,
        state=state,
        selected_contact=None,
    )

    message = mock_interaction.followup.send.call_args.args[0]
    assert "Could not prepare the onboarding email" in message
    assert "unexpected internal detail" not in message
    audit_kwargs = onboarding_cog._audit_command_safe.call_args.kwargs
    assert audit_kwargs["result"] == "error"
    assert audit_kwargs["metadata"]["error"] == "unexpected_error"
    assert audit_kwargs["metadata"]["error_type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_selected_contact_flow_refetches_contact_snapshot(
    onboarding_cog: OnboardingEmailCog,
    mock_interaction: AsyncMock,
) -> None:
    onboarding_cog._audit_command_safe = Mock()
    state = OnboardingEmailCommandState(
        candidate_name="Jane Example",
        has_contributed=False,
        recipient_email=None,
        discord_joined="unknown",
        agreement_signed="unknown",
        sender_display_name="Michael Wu",
        signature_name="Michael",
        reply_to_email=None,
    )
    stale_contact = {
        "id": "contact-candidate",
        "name": "Jane Example",
        "emailAddress": "old@example.com",
        "cOnboarder": "michael",
        "cOnboardingState": "selected",
    }
    fresh_contact = {
        "id": "contact-candidate",
        "name": "Jane Updated",
        "emailAddress": "fresh@example.com",
        "cOnboarder": "caleb",
        "cOnboardingState": "onboarded",
    }
    onboarding_cog.crm.get_contact.return_value = fresh_contact
    onboarding_cog._complete_onboarding_email = AsyncMock()

    await onboarding_cog._run_onboarding_email_selected_contact_flow(
        mock_interaction,
        state=state,
        selected_contact_snapshot=stale_contact,
    )

    onboarding_cog.crm.get_contact.assert_called_once_with("contact-candidate")
    onboarding_cog._complete_onboarding_email.assert_awaited_once_with(
        mock_interaction,
        state=state,
        selected_contact=fresh_contact,
    )


@pytest.mark.asyncio
async def test_selected_contact_flow_handles_refetch_errors(
    onboarding_cog: OnboardingEmailCog,
    mock_interaction: AsyncMock,
) -> None:
    onboarding_cog._audit_command_safe = Mock()
    state = OnboardingEmailCommandState(
        candidate_name="Jane Example",
        has_contributed=False,
        recipient_email=None,
        discord_joined="unknown",
        agreement_signed="unknown",
        sender_display_name="Michael Wu",
        signature_name="Michael",
        reply_to_email=None,
    )
    stale_contact = {
        "id": "contact-candidate",
        "name": "Jane Example",
        "emailAddress": "old@example.com",
        "cOnboarder": "michael",
        "cOnboardingState": "selected",
    }
    onboarding_cog.crm.get_contact.side_effect = EspoAPIError("CRM detail")
    onboarding_cog._complete_onboarding_email = AsyncMock()

    await onboarding_cog._run_onboarding_email_selected_contact_flow(
        mock_interaction,
        state=state,
        selected_contact_snapshot=stale_contact,
    )

    onboarding_cog._complete_onboarding_email.assert_not_called()
    message = mock_interaction.followup.send.call_args.args[0]
    assert "CRM lookup failed" in message
    audit_kwargs = onboarding_cog._audit_command_safe.call_args.kwargs
    assert audit_kwargs["result"] == "error"
    assert audit_kwargs["metadata"]["error"] == "crm_lookup_failed"
    assert audit_kwargs["metadata"]["contact_id"] == "contact-candidate"


@pytest.mark.asyncio
async def test_send_draft_response_chunks_only_markdown_body(
    onboarding_cog: OnboardingEmailCog,
    mock_interaction: AsyncMock,
) -> None:
    await onboarding_cog._send_draft_response(
        mock_interaction,
        summary="S" * 1800,
        markdown_body="M" * 4000,
    )

    calls = mock_interaction.followup.send.call_args_list
    assert calls[0].args[0] == "S" * 1800
    assert calls[1].args[0].startswith("**Copy/paste draft (1/3):**\nM")
    assert "**Copy/paste draft:**\n" not in calls[1].args[0]


@pytest.mark.asyncio
async def test_send_draft_response_chunks_oversized_summary(
    onboarding_cog: OnboardingEmailCog,
    mock_interaction: AsyncMock,
) -> None:
    await onboarding_cog._send_draft_response(
        mock_interaction,
        summary="S" * 4100,
        markdown_body="Short draft",
    )

    calls = mock_interaction.followup.send.call_args_list
    assert calls[0].args[0].startswith("**Summary (1/3):**\nS")
    assert calls[1].args[0].startswith("**Summary (2/3):**\nS")
    assert calls[2].args[0].startswith("**Summary (3/3):**\nS")
    assert calls[3].args[0] == "**Copy/paste draft:**\nShort draft"


@pytest.mark.asyncio
async def test_draft_edit_view_rerenders_edited_markdown(
    onboarding_cog: OnboardingEmailCog,
    mock_interaction: AsyncMock,
) -> None:
    view = OnboardingEmailDraftEditView(
        cog=onboarding_cog,
        requester_id=mock_interaction.user.id,
        summary="📝 Onboarding email draft generated.\nSubject: `508.dev onboarding`",
        markdown_body="Original draft",
    )

    await view.update_draft(mock_interaction, "Edited draft")

    args, kwargs = mock_interaction.followup.send.call_args
    assert "📝 Onboarding email draft updated." in args[0]
    assert "**Copy/paste draft:**\nEdited draft" in args[0]
    assert kwargs["view"] is view
    assert view.markdown_body == "Edited draft"


def test_send_message_requires_tls(
    onboarding_cog: OnboardingEmailCog,
) -> None:
    message = Mock()

    with patch("five08.discord_bot.cogs.onboarding_email.settings") as mock_settings:
        mock_settings.onboarding_email_smtp_server = "smtp.migadu.com"
        mock_settings.onboarding_email_smtp_username = "onboarding@508.dev"
        mock_settings.onboarding_email_smtp_password = "secret"
        mock_settings.onboarding_email_smtp_use_ssl = False
        mock_settings.onboarding_email_smtp_starttls = False

        with pytest.raises(ValueError, match="SMTP requires TLS"):
            onboarding_cog._send_message(message)


def test_send_message_uses_ssl_context(
    onboarding_cog: OnboardingEmailCog,
) -> None:
    message = Mock()
    tls_context = Mock()

    with (
        patch(
            "five08.onboarding_email.ssl.create_default_context",
            return_value=tls_context,
        ),
        patch("five08.onboarding_email.smtplib.SMTP_SSL") as smtp_ssl,
    ):
        smtp = smtp_ssl.return_value.__enter__.return_value

        onboarding_cog._send_message(message)

    smtp_ssl.assert_called_once_with(
        "smtp.migadu.com",
        465,
        timeout=20.0,
        context=tls_context,
    )
    smtp.login.assert_called_once_with("onboarding@508.dev", "secret")
    smtp.send_message.assert_called_once_with(message)
