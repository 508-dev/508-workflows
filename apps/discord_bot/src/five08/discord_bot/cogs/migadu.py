"""
Migadu mailbox integration cog for the 508.dev Discord bot.

This cog handles mailbox creation in Migadu.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import re
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from five08.clients.espo import EspoAPIError, EspoClient
from five08.clients.migadu import (
    MigaduAPIError,
    MigaduClient,
    MigaduMailboxCreateRequest,
    normalize_migadu_mailbox_domain,
)
from five08.discord_bot.config import settings
from five08.discord_bot.utils.audit import DiscordAuditCogMixin
from five08.discord_bot.utils.role_decorators import require_role

logger = logging.getLogger(__name__)

CRM_CONTACT_SELECT_FIELDS = (
    "id,name,emailAddress,c508Email,cDiscordUsername,cDiscordUserID"
)


@dataclass(frozen=True, slots=True)
class MailboxCommandContext:
    """Normalized `/create-mailbox` inputs used across follow-up UI callbacks."""

    mailbox_username: str
    mailbox_email: str
    local_part: str
    search_term: str | None
    requested_name: str | None
    requested_backup_email: str | None


@dataclass(frozen=True, slots=True)
class MailboxCreationOutcome:
    """Final mailbox creation result, including any post-create CRM sync failure."""

    created_address: str
    backup_email: str
    mailbox_name: str
    crm_contact: dict[str, Any] | None
    sync_error: str | None = None


def _truncate_discord_text(value: str, *, limit: int) -> str:
    """Trim text to Discord component limits."""
    if len(value) <= limit:
        return value
    if limit <= 3:
        return value[:limit]
    return value[: limit - 3].rstrip() + "..."


class CreateMailboxContactSelect(discord.ui.Select["CreateMailboxContactSelectView"]):
    """Select menu used when multiple CRM contacts match a mailbox search."""

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

            label = _truncate_discord_text(
                str(contact.get("name") or "Unknown"),
                limit=100,
            )
            email = str(contact.get("emailAddress") or "No email")
            discord_username = str(contact.get("cDiscordUsername") or "No Discord")
            description = _truncate_discord_text(
                f"{email} | {discord_username}",
                limit=100,
            )
            options.append(
                discord.SelectOption(
                    label=label,
                    value=contact_id,
                    description=description,
                )
            )

        super().__init__(
            placeholder="Select the CRM contact for this mailbox...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="create_mailbox_contact_select",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Complete mailbox creation with the selected CRM contact."""
        if not isinstance(self.view, CreateMailboxContactSelectView):
            await interaction.response.send_message(
                "❌ Mailbox contact selection is no longer available.",
                ephemeral=True,
            )
            return

        contact = self._contact_lookup.get(self.values[0])
        if contact is None:
            await interaction.response.send_message(
                "❌ Selected CRM contact could not be resolved.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        await self.view.migadu_cog._handle_mailbox_creation(
            interaction=interaction,
            context=self.view.context,
            crm_contact=contact,
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
                    "Failed to disable mailbox contact selection view: %s",
                    exc,
                )


class CreateMailboxContactSelectView(discord.ui.View):
    """View containing the CRM candidate selector for mailbox creation."""

    def __init__(
        self,
        *,
        migadu_cog: "MigaduCog",
        requester_id: int,
        context: MailboxCommandContext,
        contacts: list[dict[str, Any]],
    ) -> None:
        super().__init__(timeout=300)
        self.migadu_cog = migadu_cog
        self.requester_id = requester_id
        self.context = context
        self.add_item(CreateMailboxContactSelect(contacts))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Allow only the original requester to use the selection UI."""
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "❌ Only the command requester can select the CRM contact.",
                ephemeral=True,
            )
            return False
        return True


class MigaduCog(DiscordAuditCogMixin, commands.Cog):
    """Migadu mailbox management cog."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.espo_api = EspoClient(settings.espo_base_url, settings.espo_api_key)
        self._init_audit_logger()

    def _migadu_credentials(self) -> tuple[str, str]:
        """Return Migadu username and API token from configured settings."""
        username = (settings.migadu_api_user or "").strip()
        if not username:
            raise ValueError("MIGADU_API_USER is required to create Migadu mailboxes.")

        raw_key = (settings.migadu_api_key or "").strip()
        if not raw_key:
            raise ValueError("MIGADU_API_KEY is required to create Migadu mailboxes.")
        return username, raw_key

    def _migadu_mailbox_domain(self) -> str:
        """Resolve the mailbox domain configured for new 508 addresses."""
        return normalize_migadu_mailbox_domain(settings.migadu_mailbox_domain)

    def _migadu_client(self) -> MigaduClient:
        """Build a Migadu client from the current runtime settings."""
        username, token = self._migadu_credentials()
        return MigaduClient(
            username=username,
            api_key=token,
            domain=self._migadu_mailbox_domain(),
        )

    def _normalize_mailbox_request(self, mailbox_username: str) -> tuple[str, str]:
        """
        Normalize user input and derive:
        - mailbox_email: the 508 mailbox address to create
        - local_part: mailbox local-part for Migadu API
        """
        normalized = mailbox_username.strip().lower()
        if not normalized:
            raise ValueError("Please provide a mailbox username like `user`.")
        if " " in normalized:
            raise ValueError("Mailbox username cannot include spaces.")

        configured_domain = self._migadu_mailbox_domain()
        if "@" not in normalized:
            local_part = normalized
        else:
            if normalized.count("@") != 1:
                raise ValueError(
                    "Mailbox username must be in the format `name@domain`."
                )
            local_part, username_domain = normalized.split("@", 1)
            if username_domain != configured_domain:
                raise ValueError(
                    f"Mailbox username must be omitted or use the @{configured_domain} domain."
                )

        if not local_part:
            raise ValueError("Mailbox username is missing a local part.")

        mailbox_email = f"{local_part}@{configured_domain}"
        return mailbox_email, local_part

    @staticmethod
    def _normalize_optional_text(value: str | None) -> str | None:
        """Collapse blank optional string inputs to None."""
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        return normalized

    def _normalize_backup_email(self, backup_email: str | None) -> str | None:
        """Validate and normalize the optional backup email argument."""
        normalized = self._normalize_optional_text(backup_email)
        if normalized is None:
            return None

        lowered = normalized.lower()
        if " " in lowered:
            raise ValueError("Backup email cannot include spaces.")
        if lowered.count("@") != 1:
            raise ValueError("Backup email must be a full email address.")
        return lowered

    async def _create_migadu_mailbox(
        self, *, local_part: str, backup_email: str, name: str
    ) -> dict[str, Any]:
        """Create a mailbox in Migadu for the given local-part."""
        request = MigaduMailboxCreateRequest(
            local_part=local_part,
            backup_email=backup_email,
            name=name,
        )
        return await asyncio.to_thread(self._migadu_client().create_mailbox, request)

    @staticmethod
    def _extract_discord_id_from_mention(value: str) -> str | None:
        """Extract a Discord user ID from `<@...>` mention syntax."""
        match = re.fullmatch(r"<@!?(\d+)>", value.strip())
        if not match:
            return None
        return match.group(1)

    @staticmethod
    def _is_hex_string(value: str) -> bool:
        """Check if a string looks like a CRM contact ID."""
        return len(value) >= 15 and all(
            char in "0123456789abcdefABCDEF" for char in value
        )

    @staticmethod
    def _deduplicate_contacts(
        contacts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Deduplicate CRM contacts by ID while keeping original order."""
        seen_ids: set[str] = set()
        deduplicated_contacts: list[dict[str, Any]] = []

        for contact in contacts:
            contact_id = str(contact.get("id") or "").strip()
            if not contact_id or contact_id in seen_ids:
                continue
            seen_ids.add(contact_id)
            deduplicated_contacts.append(contact)

        return deduplicated_contacts

    async def _search_contacts(
        self,
        *,
        filters: list[dict[str, Any]],
        max_size: int = 10,
    ) -> list[dict[str, Any]]:
        """Run one contact search query against EspoCRM."""
        if not filters:
            return []

        response = await asyncio.to_thread(
            self.espo_api.request,
            "GET",
            "Contact",
            {
                "where": [{"type": "or", "value": filters}],
                "maxSize": max_size,
                "select": CRM_CONTACT_SELECT_FIELDS,
            },
        )
        contacts = response.get("list", [])
        if not isinstance(contacts, list):
            return []
        return [
            contact
            for contact in contacts
            if isinstance(contact, dict) and str(contact.get("id") or "").strip()
        ]

    async def _search_contacts_for_mailbox_candidate(
        self,
        search_term: str,
    ) -> list[dict[str, Any]]:
        """Search CRM by email, name, or Discord username for mailbox linking."""
        normalized = search_term.strip()
        if not normalized:
            return []

        if self._is_hex_string(normalized):
            try:
                response = await asyncio.to_thread(
                    self.espo_api.request,
                    "GET",
                    f"Contact/{normalized}",
                )
            except EspoAPIError:
                response = {}
            if response and response.get("id"):
                return [response]

        mention_user_id = self._extract_discord_id_from_mention(normalized)
        if mention_user_id:
            return await self._search_contacts(
                filters=[
                    {
                        "type": "equals",
                        "attribute": "cDiscordUserID",
                        "value": mention_user_id,
                    }
                ],
                max_size=1,
            )

        primary_filters: list[dict[str, Any]] = []
        fallback_filters: list[dict[str, Any]] = []
        lowered = normalized.lower()

        if "@" in lowered:
            primary_filters.extend(
                [
                    {
                        "type": "equals",
                        "attribute": "emailAddress",
                        "value": lowered,
                    },
                    {
                        "type": "equals",
                        "attribute": "c508Email",
                        "value": lowered,
                    },
                ]
            )
        else:
            primary_filters.extend(
                [
                    {
                        "type": "contains",
                        "attribute": "name",
                        "value": normalized,
                    },
                    {
                        "type": "contains",
                        "attribute": "cDiscordUsername",
                        "value": normalized,
                    },
                ]
            )
            if " " not in normalized:
                fallback_filters.append(
                    {
                        "type": "equals",
                        "attribute": "c508Email",
                        "value": f"{lowered}@{self._migadu_mailbox_domain()}",
                    }
                )

        contacts = await self._search_contacts(filters=primary_filters, max_size=10)
        if fallback_filters:
            contacts.extend(
                await self._search_contacts(filters=fallback_filters, max_size=10)
            )

        return self._deduplicate_contacts(contacts)

    async def _lookup_contacts_by_backup_email(
        self, backup_email: str
    ) -> list[dict[str, Any]]:
        """Find CRM contacts with a matching primary email address."""
        return await self._search_contacts(
            filters=[
                {
                    "type": "equals",
                    "attribute": "emailAddress",
                    "value": backup_email.lower(),
                }
            ],
            max_size=10,
        )

    async def _try_resolve_contact_by_backup_email(
        self,
        backup_email: str,
    ) -> dict[str, Any] | None:
        """Resolve a unique CRM contact by backup email, or return None when ambiguous."""
        contacts = await self._lookup_contacts_by_backup_email(backup_email)
        if len(contacts) != 1:
            return None
        return contacts[0]

    async def _resolve_unique_contact_by_backup_email(
        self,
        backup_email: str,
    ) -> dict[str, Any]:
        """Resolve exactly one CRM contact by backup email."""
        contacts = await self._lookup_contacts_by_backup_email(backup_email)
        if not contacts:
            raise ValueError(
                f"Mailbox was created, but no CRM contact was found for backup email `{backup_email}`."
            )
        if len(contacts) > 1:
            raise ValueError(
                "Mailbox was created, but multiple CRM contacts matched "
                f"backup email `{backup_email}`."
            )
        return contacts[0]

    def _existing_508_email(self, contact: dict[str, Any]) -> str | None:
        """Return the configured 508 mailbox already stored on the contact, if any."""
        raw_value = str(contact.get("c508Email") or "").strip().lower()
        if not raw_value:
            return None

        configured_domain = self._migadu_mailbox_domain()
        if raw_value.endswith(f"@{configured_domain}"):
            return raw_value
        return None

    def _contact_display_name(self, contact: dict[str, Any]) -> str:
        """Format a concise CRM contact label for user-facing responses."""
        name = str(contact.get("name") or "").strip()
        email = str(contact.get("emailAddress") or "").strip()
        if name and email:
            return f"{name} ({email})"
        if name:
            return name
        if email:
            return email
        return str(contact.get("id") or "Unknown contact")

    def _prepare_mailbox_context(
        self,
        *,
        mailbox_username: str,
        search_term: str | None,
        name: str | None,
        backup_email: str | None,
    ) -> MailboxCommandContext:
        """Normalize raw command inputs into a reusable context object."""
        mailbox_email, local_part = self._normalize_mailbox_request(mailbox_username)
        normalized_search_term = self._normalize_optional_text(search_term)
        normalized_name = self._normalize_optional_text(name)
        normalized_backup_email = self._normalize_backup_email(backup_email)

        if normalized_search_term is None and normalized_backup_email is None:
            raise ValueError(
                "`backup_email` is required when `search_term` is not provided."
            )

        return MailboxCommandContext(
            mailbox_username=mailbox_username,
            mailbox_email=mailbox_email,
            local_part=local_part,
            search_term=normalized_search_term,
            requested_name=normalized_name,
            requested_backup_email=normalized_backup_email,
        )

    def _resolve_mailbox_name_and_backup(
        self,
        *,
        context: MailboxCommandContext,
        crm_contact: dict[str, Any] | None,
    ) -> tuple[str, str]:
        """Resolve the final mailbox display name and backup email before creation."""
        mailbox_name = context.requested_name
        if mailbox_name is None and crm_contact is not None:
            mailbox_name = self._normalize_optional_text(
                str(crm_contact.get("name") or "")
            )
        if mailbox_name is None:
            mailbox_name = context.local_part

        backup_email = context.requested_backup_email
        if backup_email is None and crm_contact is not None:
            backup_email = self._normalize_backup_email(
                str(crm_contact.get("emailAddress") or "")
            )
        if backup_email is None:
            raise ValueError(
                "A backup email is required. Provide `backup_email` or use a CRM contact with an email address."
            )

        return mailbox_name, backup_email

    async def _execute_mailbox_creation(
        self,
        *,
        context: MailboxCommandContext,
        crm_contact: dict[str, Any] | None,
    ) -> MailboxCreationOutcome:
        """
        Create the mailbox, then attempt the CRM `c508Email` sync.

        Post-create CRM sync failures are returned on the outcome so the caller can
        notify the user without hiding the successful mailbox creation.
        """
        pre_resolved_contact = crm_contact
        if pre_resolved_contact is None and context.requested_backup_email is not None:
            pre_resolved_contact = await self._try_resolve_contact_by_backup_email(
                context.requested_backup_email
            )

        existing_email = (
            self._existing_508_email(pre_resolved_contact)
            if pre_resolved_contact is not None
            else None
        )
        if existing_email is not None:
            assert pre_resolved_contact is not None
            raise ValueError(
                f"CRM contact `{self._contact_display_name(pre_resolved_contact)}` "
                f"already has a 508 mailbox: `{existing_email}`."
            )

        mailbox_name, backup_email = self._resolve_mailbox_name_and_backup(
            context=context,
            crm_contact=pre_resolved_contact,
        )
        mailbox = await self._create_migadu_mailbox(
            local_part=context.local_part,
            backup_email=backup_email,
            name=mailbox_name,
        )
        created_address = str(mailbox.get("address") or context.mailbox_email)

        contact_to_update = pre_resolved_contact
        sync_error: str | None = None

        try:
            if contact_to_update is None:
                contact_to_update = await self._resolve_unique_contact_by_backup_email(
                    backup_email
                )

            existing_email = self._existing_508_email(contact_to_update)
            if existing_email is not None and existing_email != context.mailbox_email:
                raise ValueError(
                    f"CRM contact `{self._contact_display_name(contact_to_update)}` "
                    f"already has a different 508 mailbox: `{existing_email}`."
                )

            if existing_email != context.mailbox_email:
                contact_id = str(contact_to_update.get("id") or "").strip()
                if not contact_id:
                    raise ValueError(
                        "Mailbox was created, but the matched CRM contact has no ID."
                    )
                await asyncio.to_thread(
                    self.espo_api.update_contact,
                    contact_id,
                    {"c508Email": context.mailbox_email},
                )
        except (EspoAPIError, ValueError) as exc:
            sync_error = str(exc)

        return MailboxCreationOutcome(
            created_address=created_address,
            backup_email=backup_email,
            mailbox_name=mailbox_name,
            crm_contact=contact_to_update,
            sync_error=sync_error,
        )

    def _build_mailbox_embed(
        self,
        *,
        title: str,
        color: int,
        context: MailboxCommandContext,
        outcome: MailboxCreationOutcome,
    ) -> discord.Embed:
        """Build the result embed for mailbox creation responses."""
        embed = discord.Embed(title=title, color=color)
        embed.add_field(
            name="Mailbox",
            value=outcome.created_address or context.mailbox_email,
            inline=True,
        )
        embed.add_field(name="Backup", value=outcome.backup_email, inline=True)
        embed.add_field(name="Name", value=outcome.mailbox_name, inline=False)

        if outcome.crm_contact is not None:
            embed.add_field(
                name="CRM Contact",
                value=self._contact_display_name(outcome.crm_contact),
                inline=False,
            )

        if outcome.sync_error:
            embed.add_field(
                name="CRM Sync",
                value=outcome.sync_error,
                inline=False,
            )

        return embed

    async def _handle_mailbox_creation(
        self,
        *,
        interaction: discord.Interaction,
        context: MailboxCommandContext,
        crm_contact: dict[str, Any] | None = None,
    ) -> None:
        """Create the mailbox, send the Discord response, and emit audit logs."""
        try:
            outcome = await self._execute_mailbox_creation(
                context=context,
                crm_contact=crm_contact,
            )
        except MigaduAPIError as exc:
            logger.error("Migadu API error in create_mailbox: %s", exc)
            self._audit_command(
                interaction=interaction,
                action="migadu.create_mailbox",
                result="error",
                metadata={
                    "mailbox_username": context.mailbox_username,
                    "search_term": context.search_term,
                    "backup_email": context.requested_backup_email,
                    "name": context.requested_name,
                    "mailbox_email": context.mailbox_email,
                    "error": str(exc),
                },
            )
            await interaction.followup.send(
                "❌ Migadu mailbox creation failed. Please try again later.",
                ephemeral=True,
            )
            return
        except ValueError as exc:
            logger.error("Invalid request in create_mailbox: %s", exc)
            self._audit_command(
                interaction=interaction,
                action="migadu.create_mailbox",
                result="denied",
                metadata={
                    "mailbox_username": context.mailbox_username,
                    "search_term": context.search_term,
                    "backup_email": context.requested_backup_email,
                    "name": context.requested_name,
                    "mailbox_email": context.mailbox_email,
                    "error": str(exc),
                },
            )
            await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)
            return
        except Exception as exc:
            logger.error("Unexpected error in create_mailbox: %s", exc)
            self._audit_command(
                interaction=interaction,
                action="migadu.create_mailbox",
                result="error",
                metadata={
                    "mailbox_username": context.mailbox_username,
                    "search_term": context.search_term,
                    "backup_email": context.requested_backup_email,
                    "name": context.requested_name,
                    "mailbox_email": context.mailbox_email,
                    "error": str(exc),
                },
            )
            await interaction.followup.send(
                "❌ An unexpected error occurred while creating the mailbox.",
                ephemeral=True,
            )
            return

        if outcome.sync_error is not None:
            embed = self._build_mailbox_embed(
                title="⚠️ Mailbox Created, CRM Sync Failed",
                color=0xF1C40F,
                context=context,
                outcome=outcome,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            self._audit_command(
                interaction=interaction,
                action="migadu.create_mailbox",
                result="error",
                metadata={
                    "mailbox_username": context.mailbox_username,
                    "search_term": context.search_term,
                    "backup_email": outcome.backup_email,
                    "name": outcome.mailbox_name,
                    "mailbox_email": context.mailbox_email,
                    "created_address": outcome.created_address,
                    "crm_contact_id": (
                        str(outcome.crm_contact.get("id") or "")
                        if outcome.crm_contact is not None
                        else None
                    ),
                    "crm_contact_name": (
                        self._contact_display_name(outcome.crm_contact)
                        if outcome.crm_contact is not None
                        else None
                    ),
                    "crm_sync_error": outcome.sync_error,
                    "mailbox_created": True,
                },
                resource_type="discord_command",
            )
            return

        embed = self._build_mailbox_embed(
            title="✅ Mailbox Created",
            color=0x00FF00,
            context=context,
            outcome=outcome,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        self._audit_command(
            interaction=interaction,
            action="migadu.create_mailbox",
            result="success",
            metadata={
                "mailbox_username": context.mailbox_username,
                "search_term": context.search_term,
                "backup_email": outcome.backup_email,
                "name": outcome.mailbox_name,
                "mailbox_email": context.mailbox_email,
                "created_address": outcome.created_address,
                "crm_contact_id": (
                    str(outcome.crm_contact.get("id") or "")
                    if outcome.crm_contact is not None
                    else None
                ),
                "crm_contact_name": (
                    self._contact_display_name(outcome.crm_contact)
                    if outcome.crm_contact is not None
                    else None
                ),
                "forwarded_to": outcome.backup_email,
            },
            resource_type="discord_command",
        )

    @app_commands.command(
        name="create-mailbox",
        description="Create a Migadu mailbox for a 508 username (Admin only).",
    )
    @app_commands.describe(
        mailbox_username=(
            "508 mailbox username or address (e.g. alice or alice@508.dev)."
        ),
        search_term=(
            "Optional CRM lookup by email, name, Discord username, or contact ID."
        ),
        name="Optional full name for the mailbox. Defaults from CRM when available.",
        backup_email=(
            "Optional backup email for recovery/invites. Required if search_term is omitted."
        ),
    )
    @require_role("Admin")
    async def create_mailbox(
        self,
        interaction: discord.Interaction,
        mailbox_username: str,
        search_term: str | None = None,
        name: str | None = None,
        backup_email: str | None = None,
    ) -> None:
        """Create a 508 mailbox via Migadu and sync the CRM contact."""
        await interaction.response.defer(ephemeral=True)

        try:
            context = self._prepare_mailbox_context(
                mailbox_username=mailbox_username,
                search_term=search_term,
                name=name,
                backup_email=backup_email,
            )

            crm_contact: dict[str, Any] | None = None
            if context.search_term is not None:
                contacts = await self._search_contacts_for_mailbox_candidate(
                    context.search_term
                )
                if not contacts:
                    raise ValueError(
                        f"No CRM contact found for `{context.search_term}`."
                    )

                eligible_contacts = [
                    contact
                    for contact in contacts
                    if self._existing_508_email(contact) is None
                ]
                if not eligible_contacts:
                    if len(contacts) == 1:
                        existing_email = self._existing_508_email(contacts[0])
                        raise ValueError(
                            f"CRM contact `{self._contact_display_name(contacts[0])}` "
                            f"already has a 508 mailbox: `{existing_email}`."
                        )
                    raise ValueError(
                        "All matching CRM contacts already have a 508 mailbox."
                    )

                if len(eligible_contacts) > 1:
                    view = CreateMailboxContactSelectView(
                        migadu_cog=self,
                        requester_id=interaction.user.id,
                        context=context,
                        contacts=eligible_contacts,
                    )
                    await interaction.followup.send(
                        "⚠️ Multiple CRM contacts match "
                        f"`{context.search_term}`. Select the contact to update.",
                        view=view,
                        ephemeral=True,
                    )
                    return

                crm_contact = eligible_contacts[0]

            await self._handle_mailbox_creation(
                interaction=interaction,
                context=context,
                crm_contact=crm_contact,
            )
        except ValueError as exc:
            logger.error("Invalid request in create_mailbox: %s", exc)
            self._audit_command(
                interaction=interaction,
                action="migadu.create_mailbox",
                result="denied",
                metadata={
                    "mailbox_username": mailbox_username,
                    "search_term": search_term,
                    "backup_email": backup_email,
                    "name": name,
                    "error": str(exc),
                },
            )
            await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)
        except Exception as exc:
            logger.error("Unexpected error in create_mailbox: %s", exc)
            self._audit_command(
                interaction=interaction,
                action="migadu.create_mailbox",
                result="error",
                metadata={
                    "mailbox_username": mailbox_username,
                    "search_term": search_term,
                    "backup_email": backup_email,
                    "name": name,
                    "error": str(exc),
                },
            )
            await interaction.followup.send(
                "❌ An unexpected error occurred while creating the mailbox.",
                ephemeral=True,
            )


async def setup(bot: commands.Bot) -> None:
    """Add the Migadu cog to the bot."""
    await bot.add_cog(MigaduCog(bot))
