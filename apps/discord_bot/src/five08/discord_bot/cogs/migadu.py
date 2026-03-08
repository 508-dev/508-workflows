"""
Migadu mailbox integration cog for the 508.dev Discord bot.

This cog handles mailbox creation in Migadu.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from five08.discord_bot.config import settings
from five08.discord_bot.utils.audit import DiscordAuditCogMixin
from five08.discord_bot.utils.role_decorators import require_role

logger = logging.getLogger(__name__)

MIGADU_API_BASE_URL = "https://api.migadu.com/v1"


class MigaduCog(DiscordAuditCogMixin, commands.Cog):
    """Migadu mailbox management cog."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
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
        domain = (
            (settings.migadu_mailbox_domain or "508.dev").strip().lower().lstrip(".")
        )
        if not domain:
            domain = "508.dev"
        return domain

    def _normalize_mailbox_request(
        self, mailbox_username: str, backup_email: str
    ) -> tuple[str, str, str]:
        """
        Normalize user input and derive:
        - mailbox_email: the 508 mailbox address to create
        - backup_lookup: email where the invitation should be sent
        - local_part: mailbox local-part for Migadu API
        """
        mailbox_username = mailbox_username.strip().lower()
        if not mailbox_username:
            raise ValueError("Please provide a mailbox username like `user@508.dev`.")
        if " " in mailbox_username:
            raise ValueError("Mailbox username cannot include spaces.")
        if mailbox_username.count("@") != 1:
            raise ValueError("Mailbox username must be in the format `name@domain`.")

        local_part, username_domain = mailbox_username.split("@", 1)
        if not local_part:
            raise ValueError("Mailbox username is missing a local part.")

        configured_domain = self._migadu_mailbox_domain()
        if username_domain != configured_domain:
            raise ValueError(
                f"Mailbox username must be in the @{configured_domain} domain."
            )

        backup_normalized = backup_email.strip().lower()
        if not backup_normalized:
            raise ValueError("Please provide a full backup email address.")
        if " " in backup_normalized:
            raise ValueError("Backup email cannot include spaces.")
        if backup_normalized.count("@") != 1:
            raise ValueError("Backup email must be a full email address.")

        mailbox_email = f"{local_part}@{configured_domain}"
        return mailbox_email, backup_normalized, local_part

    async def _create_migadu_mailbox(
        self, *, local_part: str, backup_email: str
    ) -> dict[str, Any]:
        """Create a mailbox in Migadu for the given local-part."""
        username, token = self._migadu_credentials()
        base_url = MIGADU_API_BASE_URL.rstrip("/")
        domain = self._migadu_mailbox_domain()

        payload = {
            "local_part": local_part,
            "name": local_part,
            "password_method": "invitation",
            "password_recovery_email": backup_email,
            "forwarding_to": backup_email,
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(
                    f"{base_url}/domains/{domain}/mailboxes",
                    auth=aiohttp.BasicAuth(login=username, password=token),
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status not in {200, 201}:
                        body = await response.text()
                        raise ValueError(
                            f"Migadu mailbox creation failed: status={response.status}, body={body}"
                        )
                    data = await response.json()
                    if not isinstance(data, dict):
                        raise ValueError(
                            "Migadu response payload must be a JSON object."
                        )
                    return data
            except aiohttp.ClientError as exc:
                raise ValueError(f"Migadu API request failed: {exc}") from exc

    @app_commands.command(
        name="create-mailbox",
        description="Create a Migadu mailbox for a 508 username (Admin only).",
    )
    @app_commands.describe(
        mailbox_username=("Full 508 address for the mailbox (e.g. alice@508.dev)."),
        backup_email=("Full backup email where password recovery should be sent."),
    )
    @require_role("Admin")
    async def create_mailbox(
        self, interaction: discord.Interaction, mailbox_username: str, backup_email: str
    ) -> None:
        """Create a 508 mailbox via Migadu."""
        try:
            await interaction.response.defer(ephemeral=True)

            mailbox_email, backup_lookup, local_part = self._normalize_mailbox_request(
                mailbox_username, backup_email
            )
            mailbox = await self._create_migadu_mailbox(
                local_part=local_part,
                backup_email=backup_lookup,
            )

            created_address = mailbox.get("address")
            embed = discord.Embed(
                title="✅ Mailbox Created",
                color=0x00FF00,
            )
            embed.add_field(
                name="Mailbox", value=created_address or mailbox_email, inline=True
            )
            embed.add_field(name="Backup", value=backup_lookup, inline=True)
            await interaction.followup.send(embed=embed)

            self._audit_command(
                interaction=interaction,
                action="migadu.create_mailbox",
                result="success",
                metadata={
                    "mailbox_username": mailbox_username,
                    "backup_email": backup_lookup,
                    "mailbox_email": mailbox_email,
                    "created_address": created_address,
                    "forwarded_to": backup_lookup,
                },
                resource_type="discord_command",
            )
        except ValueError as e:
            logger.error(f"Invalid request in create_mailbox: {e}")
            self._audit_command(
                interaction=interaction,
                action="migadu.create_mailbox",
                result="denied",
                metadata={
                    "mailbox_username": mailbox_username,
                    "backup_email": backup_email,
                    "error": str(e),
                },
            )
            await interaction.followup.send(f"⚠️ {e}")
        except Exception as e:
            logger.error(f"Unexpected error in create_mailbox: {e}")
            self._audit_command(
                interaction=interaction,
                action="migadu.create_mailbox",
                result="error",
                metadata={
                    "mailbox_username": mailbox_username,
                    "backup_email": backup_email,
                    "error": str(e),
                },
            )
            await interaction.followup.send(
                "❌ An unexpected error occurred while creating the mailbox."
            )


async def setup(bot: commands.Bot) -> None:
    """Add the Migadu cog to the bot."""
    await bot.add_cog(MigaduCog(bot))
