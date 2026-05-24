"""Self-service ERPNext payment-info Discord command."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from five08.clients.erpnext import ERPNextAPIError, ERPNextClient
from five08.clients.espo import EspoAPIError, EspoClient
from five08.discord_bot.config import settings
from five08.discord_bot.utils.audit import DiscordAuditCogMixin
from five08.payment_info import (
    PaymentIdentity,
    PaymentInfoError,
    PaymentInfoInput,
    get_supplier_payment_details,
    normalize_508_email,
    payment_info_summary,
    resolve_payment_identity,
    update_supplier_payment_details,
)

logger = logging.getLogger(__name__)


class PaymentInfoModal(discord.ui.Modal, title="Update Payment Info"):
    """Modal for self-service payment-info updates."""

    account_name: discord.ui.TextInput = discord.ui.TextInput(
        label="Account holder",
        placeholder="Legal name on the account",
        required=False,
        max_length=140,
    )
    bank: discord.ui.TextInput = discord.ui.TextInput(
        label="Bank name",
        placeholder="Bank name in ERPNext",
        required=False,
        max_length=140,
    )
    branch_code: discord.ui.TextInput = discord.ui.TextInput(
        label="Routing / SWIFT / branch code",
        placeholder="Routing number, SWIFT, or branch code",
        required=False,
        max_length=80,
    )
    bank_account_no: discord.ui.TextInput = discord.ui.TextInput(
        label="Account number",
        placeholder="Full account number",
        required=False,
        max_length=120,
    )
    iban: discord.ui.TextInput = discord.ui.TextInput(
        label="IBAN",
        placeholder="Optional IBAN",
        required=False,
        max_length=120,
    )

    def __init__(self, cog: "PaymentInfoCog", owner_user_id: str) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.owner_user_id = owner_user_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if str(interaction.user.id) != self.owner_user_id:
            await interaction.response.send_message(
                "This payment-info form belongs to another Discord user.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        await self.cog.handle_payment_info_update(
            interaction,
            PaymentInfoInput(
                account_name=str(self.account_name.value or ""),
                bank=str(self.bank.value or ""),
                bank_account_no=str(self.bank_account_no.value or ""),
                branch_code=str(self.branch_code.value or ""),
                iban=str(self.iban.value or ""),
            ),
        )


class PaymentInfoView(discord.ui.View):
    """Ephemeral view with a self-only update button."""

    def __init__(self, cog: "PaymentInfoCog", owner_user_id: str) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.owner_user_id = owner_user_id

    @discord.ui.button(
        label="Update Payment Info",
        style=discord.ButtonStyle.primary,
        custom_id="payment_info_update_self",
    )
    async def update_payment_info(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button["PaymentInfoView"],
    ) -> None:
        if str(interaction.user.id) != self.owner_user_id:
            await interaction.response.send_message(
                "This payment-info view belongs to another Discord user.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            PaymentInfoModal(self.cog, self.owner_user_id)
        )


class PaymentInfoCog(DiscordAuditCogMixin, commands.Cog, name="Payment Info"):
    """Cog for users to view and update their own ERPNext payment info."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.crm = EspoClient(
            settings.espo_base_url,
            settings.espo_api_key,
            timeout_seconds=20.0,
        )
        self._init_audit_logger()
        logger.info("Payment Info cog initialized")

    def _erpnext_client(self) -> ERPNextClient:
        base_url = (settings.erpnext_base_url or "").strip()
        api_key = (settings.erpnext_api_key or "").strip()
        if not base_url or not api_key:
            raise ValueError("ERPNext API settings are required.")
        return ERPNextClient(
            base_url=base_url,
            api_key=api_key,
            timeout_seconds=settings.erpnext_api_timeout_seconds,
        )

    def _crm_contact_for_discord_user(
        self,
        discord_user_id: str,
    ) -> dict[str, Any]:
        response = self.crm.list_contacts(
            {
                "where": [
                    {
                        "type": "equals",
                        "attribute": "cDiscordUserID",
                        "value": discord_user_id,
                    }
                ],
                "maxSize": 2,
                "select": "id,name,emailAddress,c508Email,cDiscordUserID",
            }
        )
        contacts = response.get("list", [])
        if not isinstance(contacts, list) or not contacts:
            raise PaymentInfoError(
                "Your Discord account is not linked to a CRM contact yet."
            )
        if len(contacts) > 1:
            raise PaymentInfoError(
                "Your Discord account is linked to multiple CRM contacts. "
                "Ask the operations team to fix the CRM links first."
            )
        contact = contacts[0]
        if not isinstance(contact, dict):
            raise PaymentInfoError("CRM returned an invalid contact record.")
        linked_discord_id = str(contact.get("cDiscordUserID") or "").strip()
        if linked_discord_id != discord_user_id:
            raise PaymentInfoError("CRM Discord link did not match your Discord user.")
        return contact

    def _resolve_self_identity(
        self,
        discord_user_id: str,
    ) -> tuple[dict[str, Any], PaymentIdentity]:
        contact = self._crm_contact_for_discord_user(discord_user_id)
        email = normalize_508_email(contact.get("c508Email"))
        client = self._erpnext_client()
        try:
            identity = resolve_payment_identity(client, email)
        finally:
            client.close()
        return contact, identity

    def _read_self_payment_info(
        self,
        discord_user_id: str,
    ) -> tuple[dict[str, Any], PaymentIdentity, dict[str, Any]]:
        contact = self._crm_contact_for_discord_user(discord_user_id)
        email = normalize_508_email(contact.get("c508Email"))
        client = self._erpnext_client()
        try:
            identity = resolve_payment_identity(client, email)
            supplier = get_supplier_payment_details(client, identity)
        finally:
            client.close()
        return contact, identity, supplier

    def _update_self_payment_info(
        self,
        discord_user_id: str,
        payment_info: PaymentInfoInput,
    ) -> tuple[dict[str, Any], PaymentIdentity, dict[str, Any], list[str]]:
        contact = self._crm_contact_for_discord_user(discord_user_id)
        email = normalize_508_email(contact.get("c508Email"))
        client = self._erpnext_client()
        try:
            identity = resolve_payment_identity(client, email)
            supplier, changed_fields = update_supplier_payment_details(
                client,
                identity,
                payment_info,
            )
        finally:
            client.close()
        return contact, identity, supplier, changed_fields

    @app_commands.command(
        name="payment-info",
        description="View or update your own ERPNext payment info.",
    )
    async def payment_info_command(self, interaction: discord.Interaction) -> None:
        """Show the invoking user's own payment info, masked."""
        await interaction.response.defer(ephemeral=True)
        try:
            contact, identity, supplier = await asyncio.to_thread(
                self._read_self_payment_info,
                str(interaction.user.id),
            )
        except PaymentInfoError as exc:
            self._audit_command_safe(
                interaction=interaction,
                action="erpnext.payment_info_view",
                result="denied",
                metadata={"error": str(exc)},
                resource_type="erpnext_payment_info",
            )
            await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)
            return
        except (ERPNextAPIError, EspoAPIError, ValueError) as exc:
            logger.error("Payment-info lookup failed: %s", exc)
            self._audit_command_safe(
                interaction=interaction,
                action="erpnext.payment_info_view",
                result="error",
                metadata={"error": str(exc)},
                resource_type="erpnext_payment_info",
            )
            await interaction.followup.send(
                "❌ Payment-info lookup failed. Please try again later.",
                ephemeral=True,
            )
            return

        self._audit_command_safe(
            interaction=interaction,
            action="erpnext.payment_info_view",
            result="success",
            metadata={
                "crm_contact_id": contact.get("id"),
                "erpnext_user": identity.email,
                "supplier_id": identity.supplier_id,
                "has_supplier_details": bool(supplier.get("supplier_details")),
            },
            resource_type="erpnext_supplier",
            resource_id=identity.supplier_id,
        )
        await interaction.followup.send(
            "\n".join(payment_info_summary(identity, supplier)),
            view=PaymentInfoView(self, str(interaction.user.id)),
            ephemeral=True,
        )

    async def handle_payment_info_update(
        self,
        interaction: discord.Interaction,
        payment_info: PaymentInfoInput,
    ) -> None:
        """Apply a modal payment-info update for the invoking user."""
        try:
            (
                contact,
                identity,
                supplier,
                changed_fields,
            ) = await asyncio.to_thread(
                self._update_self_payment_info,
                str(interaction.user.id),
                payment_info,
            )
        except PaymentInfoError as exc:
            self._audit_command_safe(
                interaction=interaction,
                action="erpnext.payment_info_update",
                result="denied",
                metadata={"error": str(exc)},
                resource_type="erpnext_payment_info",
            )
            await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)
            return
        except (ERPNextAPIError, EspoAPIError, ValueError) as exc:
            logger.error("Payment-info update failed: %s", exc)
            self._audit_command_safe(
                interaction=interaction,
                action="erpnext.payment_info_update",
                result="error",
                metadata={"error": str(exc)},
                resource_type="erpnext_payment_info",
            )
            await interaction.followup.send(
                "❌ Payment-info update failed. Please try again later.",
                ephemeral=True,
            )
            return

        self._audit_command_safe(
            interaction=interaction,
            action="erpnext.payment_info_update",
            result="success",
            metadata={
                "crm_contact_id": contact.get("id"),
                "erpnext_user": identity.email,
                "supplier_id": identity.supplier_id,
                "changed_fields": changed_fields,
            },
            resource_type="erpnext_supplier",
            resource_id=identity.supplier_id,
        )
        await interaction.followup.send(
            "✅ Payment info updated.\n"
            + "\n".join(payment_info_summary(identity, supplier)),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    if not all(
        [
            settings.espo_base_url,
            settings.espo_api_key,
            settings.erpnext_base_url,
            settings.erpnext_api_key,
        ]
    ):
        logger.warning(
            "Payment Info cog not loaded: missing CRM or ERPNext API settings"
        )
        return
    await bot.add_cog(PaymentInfoCog(bot))
