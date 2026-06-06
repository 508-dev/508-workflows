"""Discord command for generating and sending onboarding email drafts."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from html import escape
import logging
import re
import smtplib
import ssl
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from five08.clients.espo import EspoAPIError, EspoClient
from five08.discord_bot.config import settings
from five08.discord_bot.utils.audit import DiscordAuditCogMixin
from five08.discord_bot.utils.role_decorators import check_user_roles_with_hierarchy
from five08.onboarding_email import OnboardingEmailRequest, build_onboarding_email

logger = logging.getLogger(__name__)
NO_MENTIONS = discord.AllowedMentions.none()
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
ONBOARDER_FIELD = "cOnboarder"
ONBOARDING_STATUS_FIELD = "cOnboardingState"
ONBOARDING_EMAIL_CONTACT_SELECT_FIELDS = (
    f"id,name,emailAddress,c508Email,{ONBOARDER_FIELD},{ONBOARDING_STATUS_FIELD}"
)
TERMINAL_ONBOARDING_STATES = frozenset({"onboarded", "waitlist", "rejected"})
DISCORD_TEXT_INPUT_MAX_LENGTH = 4000


@dataclass(frozen=True, slots=True)
class OnboardingEmailCommandState:
    """Normalized slash-command inputs carried into selection callbacks."""

    candidate_name: str
    has_contributed: bool
    recipient_email: str | None
    discord_joined: str
    agreement_signed: str
    sender_display_name: str
    signature_name: str
    reply_to_email: str | None


@dataclass(frozen=True, slots=True)
class OnboardingEmailSendPayload:
    """Validated send inputs carried by the draft review controls."""

    recipient_email: str
    reply_to_email: str
    sender_display_name: str
    subject: str
    recipient_from_crm: bool
    original_markdown_body: str
    original_text_body: str
    original_html_body: str
    candidate_name: str
    contact_id: str | None
    has_contributed: bool
    discord_joined: str
    agreement_signed: str
    authorization_source: str
    onboarding_status: str | None


def _truncate_component_text(value: str, *, limit: int) -> str:
    if len(value) <= limit:
        return value
    if limit <= 3:
        return value[:limit]
    return value[: limit - 3].rstrip() + "..."


def _discord_inline_code(value: object) -> str:
    text = " ".join(str(value or "").split())
    return text.replace("`", "'") or "unknown"


def _markdown_body_to_text(markdown_body: str) -> str:
    text = MARKDOWN_LINK_RE.sub(
        lambda match: f"{match.group(1)} ({match.group(2)})",
        markdown_body,
    )
    return text.strip() + "\n"


def _markdown_body_to_html(markdown_body: str) -> str:
    paragraphs = [
        paragraph
        for paragraph in re.split(r"\n{2,}", markdown_body.strip())
        if paragraph.strip()
    ]
    body = "\n".join(
        f"  <p>{_markdown_fragment_to_html(paragraph)}</p>" for paragraph in paragraphs
    )
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '  <meta charset="utf-8">\n'
        "  <title>508.dev onboarding</title>\n"
        "</head>\n"
        "<body>\n"
        f"{body}\n"
        "</body>\n"
        "</html>\n"
    )


def _markdown_fragment_to_html(markdown_fragment: str) -> str:
    output: list[str] = []
    cursor = 0
    for match in MARKDOWN_LINK_RE.finditer(markdown_fragment):
        output.append(escape(markdown_fragment[cursor : match.start()]))
        label = match.group(1)
        url = match.group(2)
        output.append(f'<a href="{escape(url, quote=True)}">{escape(label)}</a>')
        cursor = match.end()
    output.append(escape(markdown_fragment[cursor:]))
    return "".join(output).replace("\n", "<br>")


class OnboardingEmailContactSelect(
    discord.ui.Select["OnboardingEmailContactSelectView"]
):
    """Select menu for resolving multiple CRM candidate matches."""

    def __init__(self, contacts: list[dict[str, Any]]) -> None:
        self._contact_lookup = {
            str(contact["id"]): contact
            for contact in contacts
            if str(contact.get("id") or "").strip()
        }
        options: list[discord.SelectOption] = []
        for contact in contacts[:25]:
            contact_id = str(contact.get("id") or "").strip()
            if not contact_id:
                continue
            name = str(contact.get("name") or "Unknown")
            status = OnboardingEmailCog._normalize_onboarding_status(
                contact.get(ONBOARDING_STATUS_FIELD)
            )
            status_label = status or "unknown"
            email = OnboardingEmailCog._preferred_contact_email(contact) or "no email"
            onboarder = str(contact.get(ONBOARDER_FIELD) or "unassigned").strip()
            description = _truncate_component_text(
                f"{email} | status: {status_label} | onboarder: {onboarder}",
                limit=100,
            )
            options.append(
                discord.SelectOption(
                    label=_truncate_component_text(name, limit=100),
                    value=contact_id,
                    description=description,
                )
            )

        super().__init__(
            placeholder="Select the candidate to draft/send onboarding email...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="onboarding_email_contact_select",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not isinstance(self.view, OnboardingEmailContactSelectView):
            await interaction.response.send_message(
                "❌ Candidate selection is no longer available.",
                allowed_mentions=NO_MENTIONS,
                ephemeral=True,
            )
            return

        contact = self._contact_lookup.get(self.values[0])
        if contact is None:
            await interaction.response.send_message(
                "❌ Selected candidate could not be resolved.",
                allowed_mentions=NO_MENTIONS,
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        await self.view.cog._run_onboarding_email_selected_contact_flow(
            interaction,
            state=self.view.state,
            selected_contact_snapshot=contact,
        )

        for item in self.view.children:
            if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                item.disabled = True

        if interaction.message:
            try:
                await interaction.message.edit(view=self.view)
            except discord.NotFound:
                pass
            except discord.HTTPException as exc:
                logger.warning(
                    "Failed to disable onboarding email candidate selector: %s",
                    exc,
                )


class OnboardingEmailContactSelectView(discord.ui.View):
    """Self-only candidate selector for onboarding email generation."""

    def __init__(
        self,
        *,
        cog: "OnboardingEmailCog",
        requester_id: int,
        state: OnboardingEmailCommandState,
        contacts: list[dict[str, Any]],
    ) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.requester_id = requester_id
        self.state = state
        self.add_item(OnboardingEmailContactSelect(contacts))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "❌ Only the command requester can select the candidate.",
                allowed_mentions=NO_MENTIONS,
                ephemeral=True,
            )
            return False
        return True


class OnboardingEmailDraftEditModal(discord.ui.Modal):
    """Modal for editing the Markdown onboarding email draft."""

    def __init__(self, view: "OnboardingEmailDraftEditView") -> None:
        super().__init__(title="Edit onboarding email draft")
        self.draft_view = view
        self.email_body: discord.ui.TextInput[OnboardingEmailDraftEditModal] = (
            discord.ui.TextInput(
                label="Email body",
                style=discord.TextStyle.paragraph,
                default=view.markdown_body[:DISCORD_TEXT_INPUT_MAX_LENGTH],
                max_length=DISCORD_TEXT_INPUT_MAX_LENGTH,
                required=True,
            )
        )
        self.add_item(self.email_body)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        edited_body = str(self.email_body.value or "").strip()
        await self.draft_view.update_draft(interaction, edited_body)


class OnboardingEmailDraftEditView(discord.ui.View):
    """Self-only edit controls for an onboarding email draft."""

    def __init__(
        self,
        *,
        cog: "OnboardingEmailCog",
        requester_id: int,
        summary: str,
        markdown_body: str,
        send_payload: OnboardingEmailSendPayload | None = None,
    ) -> None:
        super().__init__(timeout=900)
        self.cog = cog
        self.requester_id = requester_id
        self.summary = summary
        self.markdown_body = markdown_body
        self.send_payload = send_payload
        self._send_lock = asyncio.Lock()
        self._sent = False
        self._edit_button.disabled = len(markdown_body) > DISCORD_TEXT_INPUT_MAX_LENGTH
        if send_payload is None:
            self.remove_item(self._send_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "❌ Only the command requester can use this draft.",
                allowed_mentions=NO_MENTIONS,
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.primary)
    async def _edit_button(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button["OnboardingEmailDraftEditView"],
    ) -> None:
        await interaction.response.send_modal(OnboardingEmailDraftEditModal(self))

    @discord.ui.button(label="Send Email", style=discord.ButtonStyle.success)
    async def _send_button(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button["OnboardingEmailDraftEditView"],
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        await self.send_draft(interaction)

    async def update_draft(
        self,
        interaction: discord.Interaction,
        markdown_body: str,
    ) -> None:
        self.markdown_body = markdown_body
        self._edit_button.disabled = len(markdown_body) > DISCORD_TEXT_INPUT_MAX_LENGTH
        await self.cog._send_draft_response(
            interaction,
            summary=self._updated_summary(),
            markdown_body=markdown_body,
            view=self,
        )

    def _updated_summary(self) -> str:
        lines = self.summary.splitlines()
        if not lines:
            return "📝 Onboarding email draft updated."
        lines[0] = "📝 Onboarding email draft updated."
        return "\n".join(lines)

    async def send_draft(self, interaction: discord.Interaction) -> None:
        payload = self.send_payload
        if payload is None:
            await interaction.followup.send(
                "⚠️ This draft was generated in copy-only mode and cannot be sent.",
                allowed_mentions=NO_MENTIONS,
                ephemeral=True,
            )
            return
        async with self._send_lock:
            if self._sent:
                await interaction.followup.send(
                    "⚠️ This onboarding email has already been sent.",
                    allowed_mentions=NO_MENTIONS,
                    ephemeral=True,
                )
                return
            try:
                await self.cog._send_reviewed_onboarding_email(
                    interaction,
                    payload=payload,
                    markdown_body=self.markdown_body,
                )
            except Exception as exc:
                await self.cog._handle_reviewed_onboarding_email_send_error(
                    interaction,
                    exc,
                    payload=payload,
                )
                return

            self._sent = True
            self._send_button.disabled = True
            if interaction.message:
                try:
                    await interaction.message.edit(view=self)
                except discord.NotFound:
                    pass
                except discord.HTTPException as exc:
                    logger.warning(
                        "Failed to disable onboarding email send button: %s",
                        exc,
                    )
            await interaction.followup.send(
                "✅ Onboarding email sent.\n"
                f"To: `{_discord_inline_code(payload.recipient_email)}`",
                allowed_mentions=NO_MENTIONS,
                ephemeral=True,
            )


class OnboardingEmailCog(DiscordAuditCogMixin, commands.Cog):
    """Generate and optionally send 508 onboarding emails from Discord."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.crm = EspoClient(settings.espo_base_url, settings.espo_api_key)
        self._init_audit_logger()

    @staticmethod
    def _display_name(interaction: discord.Interaction) -> str:
        display_name = getattr(interaction.user, "display_name", None)
        if display_name:
            return OnboardingEmailCog._normalized_person_name(str(display_name))
        name = getattr(interaction.user, "name", None)
        if name:
            return OnboardingEmailCog._normalized_person_name(str(name))
        return "508.dev"

    @staticmethod
    def _normalized_person_name(value: str) -> str:
        normalized = " ".join(value.strip().split())
        return normalized or "508.dev"

    @staticmethod
    def _first_name(value: str) -> str:
        return OnboardingEmailCog._normalized_person_name(value).split(" ", 1)[0]

    @staticmethod
    def _validate_email(value: str, field_name: str) -> str:
        normalized = value.strip()
        parsed_name, parsed_email = parseaddr(normalized)
        if parsed_name or parsed_email != normalized:
            raise ValueError(f"{field_name} must be a plain email address.")
        if not EMAIL_RE.fullmatch(normalized):
            raise ValueError(f"{field_name} must be a valid email address.")
        return normalized

    def _reply_to_email_for_user(
        self,
        *,
        interaction: discord.Interaction,
        override: str | None,
    ) -> str | None:
        if override:
            return self._validate_email(override, "reply_to_email")

        contacts = self._contacts_for_discord_user_id(
            interaction,
            select="id,name,emailAddress,c508Email,cDiscordUserID",
        )
        if not contacts:
            return None
        contact = contacts[0]

        for field_name in ("c508Email", "emailAddress"):
            candidate = str(contact.get(field_name) or "").strip()
            if candidate and EMAIL_RE.fullmatch(candidate):
                return candidate
        return None

    def _contacts_for_discord_user_id(
        self,
        interaction: discord.Interaction,
        *,
        select: str,
    ) -> list[dict[str, Any]]:
        discord_user_id = str(interaction.user.id)
        response = self.crm.list_contacts(
            {
                "where": [
                    {
                        "type": "equals",
                        "attribute": "cDiscordUserID",
                        "value": discord_user_id,
                    }
                ],
                "maxSize": 1,
                "select": select,
            }
        )
        contacts = response.get("list", [])
        if isinstance(contacts, list) and contacts:
            contact = contacts[0]
            if (
                isinstance(contact, dict)
                and str(contact.get("cDiscordUserID") or "").strip() == discord_user_id
            ):
                return [contact]
        return []

    def _contacts_for_discord_username(
        self,
        interaction: discord.Interaction,
        *,
        select: str,
    ) -> list[dict[str, Any]]:
        username_candidates = {
            str(getattr(interaction.user, "name", "") or "").strip(),
            str(getattr(interaction.user, "display_name", "") or "").strip(),
        }
        for username in sorted(value for value in username_candidates if value):
            response = self.crm.list_contacts(
                {
                    "where": [
                        {
                            "type": "equals",
                            "attribute": "cDiscordUsername",
                            "value": username,
                        }
                    ],
                    "maxSize": 2,
                    "select": select,
                }
            )
            contacts = response.get("list", [])
            if not isinstance(contacts, list) or len(contacts) != 1:
                continue
            contact = contacts[0]
            if isinstance(contact, dict):
                return [contact]
        return []

    def _sender_identity_for_user(
        self, interaction: discord.Interaction
    ) -> tuple[str, str] | None:
        contacts = self._contacts_for_discord_user_id(
            interaction,
            select="id,name,c508Email,cDiscordUserID,cDiscordUsername",
        )
        if not contacts:
            contacts = self._contacts_for_discord_username(
                interaction,
                select="id,name,c508Email,cDiscordUserID,cDiscordUsername",
            )
        if not contacts:
            return None
        contact = contacts[0]
        crm_name = self._normalized_person_name(str(contact.get("name") or ""))
        if crm_name != "508.dev":
            return crm_name, self._first_name(crm_name)
        crm_username = self._normalize_508_username(contact.get("c508Email"))
        if crm_username:
            return crm_username, self._first_name(crm_username)
        return None

    @staticmethod
    def _normalize_508_username(value: object) -> str | None:
        raw_value = str(value or "").strip().lower()
        if not raw_value:
            return None
        if "@" in raw_value:
            local_part, _, domain = raw_value.partition("@")
            if domain != "508.dev":
                return None
            raw_value = local_part
        normalized = raw_value.strip()
        if not normalized or any(char.isspace() for char in normalized):
            return None
        return normalized

    @staticmethod
    def _has_steering_committee_access(interaction: discord.Interaction) -> bool:
        roles = getattr(interaction.user, "roles", None)
        if not roles:
            return False
        return check_user_roles_with_hierarchy(roles, ["Steering Committee"])

    def _requester_508_username(self, interaction: discord.Interaction) -> str | None:
        contacts = self._contacts_for_discord_user_id(
            interaction,
            select="id,name,c508Email,cDiscordUserID",
        )
        if not contacts:
            return None
        contact = contacts[0]
        return self._normalize_508_username(contact.get("c508Email"))

    @staticmethod
    def _normalize_onboarding_status(value: object) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _preferred_contact_email(contact: dict[str, Any]) -> str | None:
        for field_name in ("emailAddress", "c508Email"):
            candidate = str(contact.get(field_name) or "").strip()
            if candidate and EMAIL_RE.fullmatch(candidate):
                return candidate
        return None

    @staticmethod
    def _contact_display_name(contact: dict[str, Any]) -> str:
        return str(contact.get("name") or "Unknown").strip() or "Unknown"

    def _search_candidate_contacts(
        self,
        *,
        candidate_name: str,
        recipient_email: str | None,
    ) -> list[dict[str, Any]]:
        filters: list[dict[str, Any]] = []
        if recipient_email:
            filters.extend(
                [
                    {
                        "type": "equals",
                        "attribute": "emailAddress",
                        "value": recipient_email,
                    },
                    {
                        "type": "equals",
                        "attribute": "c508Email",
                        "value": recipient_email,
                    },
                ]
            )
        else:
            search_term = candidate_name.strip()
            if search_term:
                filters.append(
                    {
                        "type": "contains",
                        "attribute": "name",
                        "value": search_term,
                    }
                )
                if EMAIL_RE.fullmatch(search_term):
                    filters.extend(
                        [
                            {
                                "type": "equals",
                                "attribute": "emailAddress",
                                "value": search_term,
                            },
                            {
                                "type": "equals",
                                "attribute": "c508Email",
                                "value": search_term,
                            },
                        ]
                    )

        if not filters:
            return []

        response = self.crm.list_contacts(
            {
                "where": [{"type": "or", "value": filters}],
                "maxSize": 25,
                "select": ONBOARDING_EMAIL_CONTACT_SELECT_FIELDS,
            }
        )
        contacts = response.get("list", [])
        if not isinstance(contacts, list):
            return []

        deduplicated: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for contact in contacts:
            if not isinstance(contact, dict):
                continue
            contact_id = str(contact.get("id") or "").strip()
            if not contact_id or contact_id in seen_ids:
                continue
            seen_ids.add(contact_id)
            deduplicated.append(contact)
        return deduplicated

    def _fetch_candidate_contact_by_id(
        self,
        selected_contact_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        contact_id = str(selected_contact_snapshot.get("id") or "").strip()
        if not contact_id:
            raise ValueError("Selected CRM contact is missing an id.")

        contact = self.crm.get_contact(contact_id)
        if not isinstance(contact, dict):
            raise ValueError("Selected CRM contact could not be loaded.")
        fetched_contact_id = str(contact.get("id") or "").strip()
        if fetched_contact_id != contact_id:
            raise ValueError("Selected CRM contact loaded with an unexpected id.")
        return contact

    def _authorize_onboarding_email(
        self,
        *,
        interaction: discord.Interaction,
        selected_contact: dict[str, Any] | None,
    ) -> str:
        """Return the authorization source or raise when the actor may not proceed."""
        if self._has_steering_committee_access(interaction):
            return "steering_committee"

        if selected_contact is None:
            raise PermissionError(
                "Only Steering Committee+ can use this command without "
                "a unique CRM candidate match. Designated onboarders must provide "
                "or select a candidate so the CRM assignment can be verified."
            )

        requester_username = self._requester_508_username(interaction)
        if requester_username is None:
            raise PermissionError(
                "Only Steering Committee+ or the candidate's designated onboarder "
                "can use this command. Your Discord account is not linked to a "
                "CRM contact with a 508 email."
            )

        assigned_onboarder = self._normalize_508_username(
            selected_contact.get(ONBOARDER_FIELD)
        )
        if assigned_onboarder != requester_username:
            raise PermissionError(
                "Only Steering Committee+ or the candidate's designated onboarder "
                "can use this command."
            )
        return "designated_onboarder"

    def _authorized_candidate_contacts_for_actor(
        self,
        *,
        interaction: discord.Interaction,
        contacts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if self._has_steering_committee_access(interaction):
            return contacts

        requester_username = self._requester_508_username(interaction)
        if requester_username is None:
            return []

        return [
            contact
            for contact in contacts
            if self._normalize_508_username(contact.get(ONBOARDER_FIELD))
            == requester_username
        ]

    @staticmethod
    def _public_error_text(exc: Exception) -> str:
        text = " ".join(str(exc).split())
        return text.replace("`", "'") or "Could not prepare the onboarding email."

    def _classify_onboarding_email_error(self, exc: Exception) -> tuple[str, str, str]:
        if isinstance(exc, PermissionError):
            return "denied", "permission_denied", f"⚠️ {self._public_error_text(exc)}"
        if isinstance(exc, EspoAPIError):
            return (
                "error",
                "crm_lookup_failed",
                "❌ CRM lookup failed while preparing the onboarding email. "
                "Try again or ask an admin to check CRM.",
            )
        if isinstance(exc, (OSError, smtplib.SMTPException)):
            return (
                "error",
                "smtp_send_failed",
                "❌ Failed to send the onboarding email.",
            )
        if isinstance(exc, ValueError):
            return "error", "validation_error", f"⚠️ {self._public_error_text(exc)}"
        return (
            "error",
            "unexpected_error",
            "❌ Could not prepare the onboarding email.",
        )

    async def _handle_onboarding_email_error(
        self,
        interaction: discord.Interaction,
        exc: Exception,
        *,
        state: OnboardingEmailCommandState | None,
        candidate_name: str,
        recipient_email: str | None,
        has_contributed: bool,
        discord_joined: str,
        agreement_signed: str,
        selected_contact: dict[str, Any] | None = None,
    ) -> None:
        result, error_code, public_message = self._classify_onboarding_email_error(exc)
        if result == "error":
            logger.warning("Onboarding email command failed: %s", exc, exc_info=True)

        contact_id = (
            str(selected_contact.get("id") or "").strip()
            if selected_contact is not None
            else None
        )
        self._audit_command_safe(
            interaction=interaction,
            action="onboarding.email",
            result=result,
            metadata={
                "candidate_name": candidate_name,
                "contact_id": contact_id,
                "recipient_email": recipient_email,
                "has_contributed": has_contributed,
                "discord_joined": discord_joined,
                "agreement_signed": agreement_signed,
                "error": error_code,
                "error_type": type(exc).__name__,
                "sender_display_name": (
                    state.sender_display_name if state is not None else None
                ),
                "signature_name": state.signature_name if state is not None else None,
            },
            resource_type="crm_contact" if contact_id else "onboarding_email",
            resource_id=contact_id or recipient_email,
        )
        await interaction.followup.send(
            public_message,
            allowed_mentions=NO_MENTIONS,
            ephemeral=True,
        )

    def _build_message(
        self,
        *,
        recipient_email: str,
        reply_to_email: str,
        sender_name: str,
        subject: str,
        text_body: str,
        html_body: str,
    ) -> EmailMessage:
        sender_email = self._validate_email(
            settings.onboarding_email_sender_email,
            "ONBOARDING_EMAIL_SENDER_EMAIL",
        )
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = formataddr((sender_name, sender_email))
        message["To"] = recipient_email
        message["Reply-To"] = formataddr((sender_name, reply_to_email))
        message.set_content(text_body)
        message.add_alternative(html_body, subtype="html")
        return message

    def _send_message(self, message: EmailMessage) -> None:
        smtp_server = (settings.onboarding_email_smtp_server or "").strip()
        smtp_username = (settings.onboarding_email_smtp_username or "").strip()
        smtp_password = (settings.onboarding_email_smtp_password or "").strip()
        if not smtp_server:
            raise ValueError("ONBOARDING_EMAIL_SMTP_SERVER is required to send.")
        if not smtp_username or not smtp_password:
            raise ValueError(
                "ONBOARDING_EMAIL_SMTP_USERNAME and "
                "ONBOARDING_EMAIL_SMTP_PASSWORD are required to send."
            )
        if (
            not settings.onboarding_email_smtp_use_ssl
            and not settings.onboarding_email_smtp_starttls
        ):
            raise ValueError(
                "Onboarding email SMTP requires TLS. Enable "
                "ONBOARDING_EMAIL_SMTP_USE_SSL or ONBOARDING_EMAIL_SMTP_STARTTLS."
            )

        port = settings.onboarding_email_smtp_port
        timeout = settings.onboarding_email_smtp_timeout_seconds
        tls_context = ssl.create_default_context()
        if settings.onboarding_email_smtp_use_ssl:
            with smtplib.SMTP_SSL(
                smtp_server,
                port,
                timeout=timeout,
                context=tls_context,
            ) as smtp:
                smtp.login(smtp_username, smtp_password)
                smtp.send_message(message)
            return

        with smtplib.SMTP(smtp_server, port, timeout=timeout) as smtp:
            if settings.onboarding_email_smtp_starttls:
                smtp.starttls(context=tls_context)
            smtp.login(smtp_username, smtp_password)
            smtp.send_message(message)

    async def _send_reviewed_onboarding_email(
        self,
        interaction: discord.Interaction,
        *,
        payload: OnboardingEmailSendPayload,
        markdown_body: str,
    ) -> None:
        if not markdown_body.strip():
            raise ValueError("Cannot send an empty onboarding email draft.")

        recipient_email = payload.recipient_email
        authorization_source = payload.authorization_source
        onboarding_status = payload.onboarding_status
        if payload.contact_id:
            current_contact = await asyncio.to_thread(
                self._fetch_candidate_contact_by_id,
                {"id": payload.contact_id},
            )
            current_status = self._normalize_onboarding_status(
                current_contact.get(ONBOARDING_STATUS_FIELD)
            )
            current_name = self._contact_display_name(current_contact)
            if current_status in TERMINAL_ONBOARDING_STATES:
                raise PermissionError(
                    f"{current_name} is in terminal onboarding state "
                    f"{current_status}. "
                    "No onboarding email was sent."
                )

            authorization_source = await asyncio.to_thread(
                self._authorize_onboarding_email,
                interaction=interaction,
                selected_contact=current_contact,
            )
            onboarding_status = current_status or None
            if payload.recipient_from_crm:
                current_recipient = self._preferred_contact_email(current_contact)
                if current_recipient is None:
                    raise ValueError(
                        "Selected CRM contact no longer has an email address."
                    )
                recipient_email = current_recipient

        if markdown_body == payload.original_markdown_body:
            text_body = payload.original_text_body
            html_body = payload.original_html_body
        else:
            text_body = _markdown_body_to_text(markdown_body)
            html_body = _markdown_body_to_html(markdown_body)

        message = self._build_message(
            recipient_email=recipient_email,
            reply_to_email=payload.reply_to_email,
            sender_name=payload.sender_display_name,
            subject=payload.subject,
            text_body=text_body,
            html_body=html_body,
        )
        await asyncio.to_thread(self._send_message, message)
        self._audit_command_safe(
            interaction=interaction,
            action="onboarding.email",
            result="success",
            metadata={
                "email_action": "sent",
                "candidate_name": payload.candidate_name,
                "contact_id": payload.contact_id,
                "recipient_email": recipient_email,
                "has_contributed": payload.has_contributed,
                "discord_joined": payload.discord_joined,
                "agreement_signed": payload.agreement_signed,
                "sender_display_name": payload.sender_display_name,
                "reply_to_email": payload.reply_to_email,
                "authorization_source": authorization_source,
                "onboarding_status": onboarding_status,
                "edited": markdown_body != payload.original_markdown_body,
            },
            resource_type="crm_contact" if payload.contact_id else "onboarding_email",
            resource_id=payload.contact_id or recipient_email,
        )

    async def _handle_reviewed_onboarding_email_send_error(
        self,
        interaction: discord.Interaction,
        exc: Exception,
        *,
        payload: OnboardingEmailSendPayload,
    ) -> None:
        result, error_code, public_message = self._classify_onboarding_email_error(exc)
        if result == "error":
            logger.warning(
                "Reviewed onboarding email send failed: %s",
                exc,
                exc_info=True,
            )

        self._audit_command_safe(
            interaction=interaction,
            action="onboarding.email",
            result=result,
            metadata={
                "email_action": "send_failed",
                "candidate_name": payload.candidate_name,
                "contact_id": payload.contact_id,
                "recipient_email": payload.recipient_email,
                "has_contributed": payload.has_contributed,
                "discord_joined": payload.discord_joined,
                "agreement_signed": payload.agreement_signed,
                "sender_display_name": payload.sender_display_name,
                "reply_to_email": payload.reply_to_email,
                "authorization_source": payload.authorization_source,
                "onboarding_status": payload.onboarding_status,
                "error": error_code,
                "error_type": type(exc).__name__,
            },
            resource_type="crm_contact" if payload.contact_id else "onboarding_email",
            resource_id=payload.contact_id or payload.recipient_email,
        )
        await interaction.followup.send(
            public_message,
            allowed_mentions=NO_MENTIONS,
            ephemeral=True,
        )

    async def _send_draft_response(
        self,
        interaction: discord.Interaction,
        *,
        summary: str,
        markdown_body: str,
        view: discord.ui.View | None = None,
    ) -> None:
        draft_heading = "**Copy/paste draft:**"
        combined = f"{summary}\n\n{draft_heading}\n{markdown_body}"
        if len(combined) <= 2000:
            kwargs: dict[str, Any] = {
                "allowed_mentions": NO_MENTIONS,
                "ephemeral": True,
            }
            if view is not None:
                kwargs["view"] = view
            await interaction.followup.send(combined, **kwargs)
            return

        summary_view = view
        if len(summary) <= 2000:
            kwargs = {
                "allowed_mentions": NO_MENTIONS,
                "ephemeral": True,
            }
            if summary_view is not None:
                kwargs["view"] = summary_view
            await interaction.followup.send(summary, **kwargs)
            summary_view = None
        else:
            chunks = [
                summary[index : index + 1900] for index in range(0, len(summary), 1900)
            ]
            for index, chunk in enumerate(chunks, 1):
                kwargs = {
                    "allowed_mentions": NO_MENTIONS,
                    "ephemeral": True,
                }
                if summary_view is not None:
                    kwargs["view"] = summary_view
                await interaction.followup.send(
                    f"**Summary ({index}/{len(chunks)}):**\n{chunk}",
                    **kwargs,
                )
                summary_view = None

        if len(f"{draft_heading}\n{markdown_body}") <= 2000:
            await interaction.followup.send(
                f"{draft_heading}\n{markdown_body}",
                allowed_mentions=NO_MENTIONS,
                ephemeral=True,
            )
            return

        chunks = [
            markdown_body[index : index + 1900]
            for index in range(0, len(markdown_body), 1900)
        ]
        for index, chunk in enumerate(chunks, 1):
            await interaction.followup.send(
                f"**Copy/paste draft ({index}/{len(chunks)}):**\n{chunk}",
                allowed_mentions=NO_MENTIONS,
                ephemeral=True,
            )

    async def _complete_onboarding_email(
        self,
        interaction: discord.Interaction,
        *,
        state: OnboardingEmailCommandState,
        selected_contact: dict[str, Any] | None,
    ) -> None:
        candidate_name = (
            self._contact_display_name(selected_contact)
            if selected_contact is not None
            else state.candidate_name
        )
        contact_status = (
            self._normalize_onboarding_status(
                selected_contact.get(ONBOARDING_STATUS_FIELD)
            )
            if selected_contact is not None
            else ""
        )
        contact_id = (
            str(selected_contact.get("id") or "").strip()
            if selected_contact is not None
            else None
        )
        if contact_status in TERMINAL_ONBOARDING_STATES:
            self._audit_command_safe(
                interaction=interaction,
                action="onboarding.email",
                result="denied",
                metadata={
                    "candidate_name": candidate_name,
                    "contact_id": contact_id,
                    "recipient_email": state.recipient_email,
                    "onboarding_status": contact_status,
                    "error": "candidate_terminal_onboarding_state",
                },
                resource_type="crm_contact",
                resource_id=contact_id,
            )
            await interaction.followup.send(
                f"⚠️ **{candidate_name}** is in terminal onboarding state "
                f"`{_discord_inline_code(contact_status)}`. "
                "No onboarding email was generated or sent.",
                allowed_mentions=NO_MENTIONS,
                ephemeral=True,
            )
            return

        normalized_recipient = state.recipient_email
        if normalized_recipient is None and selected_contact is not None:
            normalized_recipient = self._preferred_contact_email(selected_contact)

        authorization_source = await asyncio.to_thread(
            self._authorize_onboarding_email,
            interaction=interaction,
            selected_contact=selected_contact,
        )

        draft = build_onboarding_email(
            OnboardingEmailRequest(
                candidate_name=candidate_name,
                sender_name=state.signature_name,
                has_contributed=state.has_contributed,
                discord_joined=state.discord_joined,  # type: ignore[arg-type]
                membership_agreement_signed=state.agreement_signed,  # type: ignore[arg-type]
            )
        )
        try:
            resolved_reply_to = await asyncio.to_thread(
                self._reply_to_email_for_user,
                interaction=interaction,
                override=state.reply_to_email,
            )
        except EspoAPIError:
            logger.warning(
                "Unable to resolve onboarding email Reply-To from CRM",
                exc_info=True,
            )
            resolved_reply_to = None

        can_send = normalized_recipient is not None and resolved_reply_to is not None
        email_action = "drafted_for_send" if can_send else "drafted"
        heading = (
            "📝 Onboarding email draft generated. Review, edit, then press Send Email."
            if can_send
            else "📝 Onboarding email draft generated."
        )

        self._audit_command_safe(
            interaction=interaction,
            action="onboarding.email",
            result="success",
            metadata={
                "email_action": email_action,
                "candidate_name": candidate_name,
                "contact_id": contact_id,
                "recipient_email": normalized_recipient,
                "has_contributed": state.has_contributed,
                "discord_joined": state.discord_joined,
                "agreement_signed": state.agreement_signed,
                "sender_display_name": state.sender_display_name,
                "signature_name": state.signature_name,
                "reply_to_email": resolved_reply_to,
                "authorization_source": authorization_source,
                "onboarding_status": contact_status or None,
                "send_available": can_send,
            },
            resource_type="crm_contact" if contact_id else "onboarding_email",
            resource_id=contact_id or normalized_recipient,
        )
        reply_to_line = resolved_reply_to or "not resolved"
        lines = [
            heading,
            f"Subject: `{_discord_inline_code(draft.subject)}`",
            (
                "From: "
                f"`{_discord_inline_code(state.sender_display_name)} "
                f"<{_discord_inline_code(settings.onboarding_email_sender_email)}>`"
            ),
            f"Reply-To: `{_discord_inline_code(reply_to_line)}`",
        ]
        if selected_contact is not None:
            status_line = contact_status or "unknown"
            lines.append(
                f"CRM contact: `{_discord_inline_code(candidate_name)}` "
                f"(`{_discord_inline_code(contact_id or 'unknown')}`), "
                f"status: `{_discord_inline_code(status_line)}`"
            )
        summary = "\n".join(lines)
        send_payload = None
        if normalized_recipient and resolved_reply_to:
            send_payload = OnboardingEmailSendPayload(
                recipient_email=normalized_recipient,
                reply_to_email=resolved_reply_to,
                sender_display_name=state.sender_display_name,
                subject=draft.subject,
                recipient_from_crm=(
                    state.recipient_email is None and selected_contact is not None
                ),
                original_markdown_body=draft.markdown_body,
                original_text_body=draft.text_body,
                original_html_body=draft.html_body,
                candidate_name=candidate_name,
                contact_id=contact_id,
                has_contributed=state.has_contributed,
                discord_joined=state.discord_joined,
                agreement_signed=state.agreement_signed,
                authorization_source=authorization_source,
                onboarding_status=contact_status or None,
            )
        view = OnboardingEmailDraftEditView(
            cog=self,
            requester_id=interaction.user.id,
            summary=summary,
            markdown_body=draft.markdown_body,
            send_payload=send_payload,
        )
        await self._send_draft_response(
            interaction,
            summary=summary,
            markdown_body=draft.markdown_body,
            view=view,
        )

    async def _run_onboarding_email_flow(
        self,
        interaction: discord.Interaction,
        *,
        state: OnboardingEmailCommandState,
        selected_contact: dict[str, Any] | None,
    ) -> None:
        try:
            await self._complete_onboarding_email(
                interaction,
                state=state,
                selected_contact=selected_contact,
            )
        except Exception as exc:
            await self._handle_onboarding_email_error(
                interaction,
                exc,
                state=state,
                candidate_name=state.candidate_name,
                recipient_email=state.recipient_email,
                has_contributed=state.has_contributed,
                discord_joined=state.discord_joined,
                agreement_signed=state.agreement_signed,
                selected_contact=selected_contact,
            )

    async def _run_onboarding_email_selected_contact_flow(
        self,
        interaction: discord.Interaction,
        *,
        state: OnboardingEmailCommandState,
        selected_contact_snapshot: dict[str, Any],
    ) -> None:
        try:
            selected_contact = await asyncio.to_thread(
                self._fetch_candidate_contact_by_id,
                selected_contact_snapshot,
            )
        except Exception as exc:
            await self._handle_onboarding_email_error(
                interaction,
                exc,
                state=state,
                candidate_name=state.candidate_name,
                recipient_email=state.recipient_email,
                has_contributed=state.has_contributed,
                discord_joined=state.discord_joined,
                agreement_signed=state.agreement_signed,
                selected_contact=selected_contact_snapshot,
            )
            return

        await self._run_onboarding_email_flow(
            interaction,
            state=state,
            selected_contact=selected_contact,
        )

    @app_commands.command(
        name="onboarding-email",
        description="Draft a 508 candidate onboarding email for review.",
    )
    @app_commands.describe(
        candidate_name="Candidate name or email search term. CRM name is used when matched.",
        has_contributed="Whether the candidate has completed the contribution requirement.",
        recipient_email="Optional override; CRM email is used when available.",
        discord_joined="Whether the candidate has already joined Discord.",
        agreement_signed="Whether the membership agreement is already signed.",
        sender_name="Name to use in From display; signature uses its first name.",
        reply_to_email="Reply-To address. Defaults from your CRM-linked 508 email when available.",
    )
    @app_commands.choices(
        discord_joined=[
            app_commands.Choice(name="Unknown", value="unknown"),
            app_commands.Choice(name="Yes", value="yes"),
            app_commands.Choice(name="No", value="no"),
        ],
        agreement_signed=[
            app_commands.Choice(name="Unknown", value="unknown"),
            app_commands.Choice(name="Yes", value="yes"),
            app_commands.Choice(name="No", value="no"),
        ],
    )
    async def onboarding_email(
        self,
        interaction: discord.Interaction,
        candidate_name: str,
        has_contributed: bool,
        recipient_email: str | None = None,
        discord_joined: str = "unknown",
        agreement_signed: str = "unknown",
        sender_name: str | None = None,
        reply_to_email: str | None = None,
    ) -> None:
        """Generate a candidate onboarding email draft for review."""
        await interaction.response.defer(ephemeral=True)
        state: OnboardingEmailCommandState | None = None

        try:
            override_sender_name = sender_name.strip() if sender_name else ""
            if override_sender_name:
                sender_display_name = self._normalized_person_name(override_sender_name)
                signature_name = self._first_name(sender_display_name)
            else:
                try:
                    sender_identity = await asyncio.to_thread(
                        self._sender_identity_for_user,
                        interaction,
                    )
                except EspoAPIError:
                    logger.warning(
                        "Unable to resolve onboarding email sender name from CRM",
                        exc_info=True,
                    )
                    sender_identity = None
                if sender_identity is not None:
                    sender_display_name, signature_name = sender_identity
                else:
                    sender_display_name = self._display_name(interaction)
                    signature_name = self._first_name(sender_display_name)

            normalized_recipient = (
                self._validate_email(recipient_email, "recipient_email")
                if recipient_email
                else None
            )
            state = OnboardingEmailCommandState(
                candidate_name=candidate_name,
                has_contributed=has_contributed,
                recipient_email=normalized_recipient,
                discord_joined=discord_joined,
                agreement_signed=agreement_signed,
                sender_display_name=sender_display_name,
                signature_name=signature_name,
                reply_to_email=reply_to_email,
            )

            try:
                candidate_contacts = await asyncio.to_thread(
                    self._search_candidate_contacts,
                    candidate_name=candidate_name,
                    recipient_email=normalized_recipient,
                )
            except EspoAPIError:
                if normalized_recipient or not self._has_steering_committee_access(
                    interaction
                ):
                    raise
                logger.warning(
                    "Unable to search onboarding email candidate contacts",
                    exc_info=True,
                )
                candidate_contacts = []
            if candidate_contacts:
                candidate_contacts = await asyncio.to_thread(
                    self._authorized_candidate_contacts_for_actor,
                    interaction=interaction,
                    contacts=candidate_contacts,
                )
                if not candidate_contacts:
                    raise PermissionError(
                        "Only Steering Committee+ or the candidate's designated "
                        "onboarder can use this command."
                    )
            if len(candidate_contacts) > 1:
                view = OnboardingEmailContactSelectView(
                    cog=self,
                    requester_id=interaction.user.id,
                    state=state,
                    contacts=candidate_contacts,
                )
                await interaction.followup.send(
                    "⚠️ Multiple CRM contacts match this onboarding email. "
                    "Select the candidate to continue. Already-onboarded contacts "
                    "are labelled with `status: onboarded`.",
                    allowed_mentions=NO_MENTIONS,
                    view=view,
                    ephemeral=True,
                )
                return

            selected_contact = candidate_contacts[0] if candidate_contacts else None
            if normalized_recipient and selected_contact is None:
                if not self._has_steering_committee_access(interaction):
                    raise PermissionError(
                        "Only Steering Committee+ or the candidate's designated "
                        "onboarder can use this command."
                    )
                raise ValueError("No CRM contact found for recipient_email.")

            await self._run_onboarding_email_flow(
                interaction,
                state=state,
                selected_contact=selected_contact,
            )
        except Exception as exc:
            await self._handle_onboarding_email_error(
                interaction,
                exc,
                state=state,
                candidate_name=candidate_name,
                recipient_email=recipient_email,
                has_contributed=has_contributed,
                discord_joined=discord_joined,
                agreement_signed=agreement_signed,
            )


async def setup(bot: commands.Bot) -> None:
    """Add the onboarding email cog to the bot."""
    await bot.add_cog(OnboardingEmailCog(bot))
