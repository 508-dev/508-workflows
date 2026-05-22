"""Invoice validation cog for the 508.dev Discord bot."""

import asyncio
import logging
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from five08.clients.erpnext import ERPNextClient, ERPNextAPIError
from five08.erpnext_validation import validate_invoice
from five08.projects import (
    list_dashboard_projects,
    project_viewer_emails_for_discord,
)
from five08.discord_bot.config import settings
from five08.discord_bot.utils.role_decorators import check_user_roles_with_hierarchy

logger = logging.getLogger(__name__)

DOCTYPE_CHOICES = [
    app_commands.Choice(name="Sales Invoice", value="Sales Invoice"),
    app_commands.Choice(name="Purchase Invoice", value="Purchase Invoice"),
]

STATUS_LABEL = {0: "Draft", 1: "Submitted", 2: "Cancelled"}

# Invoice access rules — a caller may validate an invoice if any of these hold:
#   1. They have a privileged role (one of _PRIVILEGED_ROLES) for full access.
#   2. They created the invoice (invoice owner matches one of their ERP emails).
#   3. They are on the invoice's ERP project roster.
_PRIVILEGED_ROLES = ["Steering Committee", "Workflows Engineer"]


def _can_view_invoice(
    invoice: dict[str, Any],
    include_all: bool,
    emails: list[str],
    project_ids: list[str],
) -> bool:
    """Return whether the caller is authorized to see this specific invoice."""
    if include_all:
        return True
    owner = str(invoice.get("owner") or "").strip().casefold()
    if owner and owner in {email.casefold() for email in emails}:
        return True
    project = invoice.get("project")
    return bool(project and project in project_ids)


class InvoicesCog(commands.Cog, name="Invoices"):
    """Cog for ERPNext invoice validation."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        base_url = (settings.erpnext_base_url or "").strip()
        api_key = (settings.erpnext_api_key or "").strip()
        self.client = ERPNextClient(
            base_url=base_url,
            api_key=api_key,
            timeout_seconds=settings.erpnext_api_timeout_seconds,
        )
        logger.info("Invoices cog initialized")

    def _resolve_access(
        self, interaction: discord.Interaction
    ) -> tuple[bool, list[str], list[str]]:
        """Resolve the caller's invoice access. Run inside a worker thread.

        Returns (include_all, emails, project_ids). A non-privileged caller
        with no ERP identity returns (False, [], []).
        """
        roles = getattr(interaction.user, "roles", [])
        if check_user_roles_with_hierarchy(roles, _PRIVILEGED_ROLES):
            return True, [], []

        emails = project_viewer_emails_for_discord(settings, str(interaction.user.id))
        if not emails:
            return False, [], []

        projects = list_dashboard_projects(
            settings,
            viewer_emails=emails,
            include_all=False,
            limit=500,
            include_roster=False,
        )
        project_ids = [
            str(pid)
            for project in projects
            if (pid := project.get("erpnext_project_id"))
        ]
        return False, emails, project_ids

    @app_commands.command(
        name="validate-invoice",
        description="Check an ERPNext invoice for common validation errors",
    )
    @app_commands.describe(
        doctype="Invoice type to validate",
        invoice_name="Invoice number (autocomplete). Changed doctype? Retype here to refresh options.",
    )
    @app_commands.choices(doctype=DOCTYPE_CHOICES)
    async def validate_invoice_command(
        self,
        interaction: discord.Interaction,
        doctype: app_commands.Choice[str],
        invoice_name: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            include_all, emails, project_ids = await asyncio.to_thread(
                self._resolve_access, interaction
            )
            if not include_all and not emails:
                roles_str = " or ".join(_PRIVILEGED_ROLES)
                await interaction.followup.send(
                    f"Invoice validation is restricted to {roles_str} "
                    "or confirmed ERP project members.",
                    ephemeral=True,
                )
                return

            invoice = await asyncio.to_thread(
                self.client.get_invoice, doctype.value, invoice_name
            )

            if invoice is None or not _can_view_invoice(
                invoice, include_all, emails, project_ids
            ):
                await interaction.followup.send(
                    f"{doctype.value} `{invoice_name}` not found or you don't have access to it.",
                    ephemeral=True,
                )
                return

            result = validate_invoice(invoice, doctype.value)

            docstatus = invoice.get("docstatus", 0)
            status_label = STATUS_LABEL.get(docstatus, str(docstatus))
            summary_lines = [
                f"**Status:** {status_label}",
                f"**Owner:** {invoice.get('owner') or '—'}",
                f"**Project:** {invoice.get('project') or '—'}",
                f"**Cost Center:** {invoice.get('cost_center') or '—'}",
                f"**Posting Date:** {invoice.get('posting_date') or '—'}",
                f"**Due Date:** {invoice.get('due_date') or '—'}",
            ]

            if result.passed:
                embed = discord.Embed(
                    title=f"✅ {invoice_name} — No issues found",
                    color=discord.Color.green(),
                )
            else:
                embed = discord.Embed(
                    title=f"⚠️ {invoice_name} — {len(result.issues)} issue(s) found",
                    color=discord.Color.red(),
                )

            embed.add_field(
                name="Invoice Info",
                value="\n".join(summary_lines),
                inline=False,
            )

            if not result.passed:
                issue_lines = "\n\n".join(
                    f"❌ {issue.message}" for issue in result.issues
                )
                if len(issue_lines) > 1024:
                    cutoff = issue_lines.rfind("\n\n", 0, 984)
                    if cutoff == -1:
                        cutoff = 984
                    shown = issue_lines[:cutoff].count("❌")
                    remaining = len(result.issues) - shown
                    issue_lines = (
                        issue_lines[:cutoff]
                        + f"\n\n_…{remaining} more issue(s) hidden_"
                    )
                embed.add_field(
                    name="Issues",
                    value=issue_lines,
                    inline=False,
                )

            embed.set_footer(text=f"Type: {doctype.value}")
            await interaction.followup.send(embed=embed, ephemeral=True)

        except ERPNextAPIError as e:
            logger.exception("ERPNext API error in validate_invoice")
            await interaction.followup.send(
                f"Failed to fetch invoice from ERPNext: {e}", ephemeral=True
            )
        except Exception:
            logger.exception("Unexpected error in validate_invoice")
            await interaction.followup.send(
                "An unexpected error occurred.", ephemeral=True
            )

    @validate_invoice_command.autocomplete("invoice_name")
    async def invoice_name_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        doctype_value = interaction.namespace.doctype
        if not doctype_value:
            return []

        try:
            include_all, emails, project_ids = await asyncio.to_thread(
                self._resolve_access, interaction
            )
            if not include_all and not emails:
                return []

            invoices = await asyncio.to_thread(
                self.client.search_invoices,
                doctype=doctype_value,
                query=current,
                limit=25,
                owners=None if include_all else emails,
                projects=None if include_all else project_ids,
            )
            choices = []
            for inv in invoices[:25]:
                inv_name = inv.get("name")
                # Ensure inv_name is a valid string and within Discord's 100-char limit
                if not isinstance(inv_name, str) or len(inv_name) > 100:
                    continue  # Skip invalid or overlong IDs to prevent lookup/API failures

                try:
                    status_int = int(inv.get("docstatus", 0))
                except (TypeError, ValueError):
                    status_int = -1

                label = f"[{STATUS_LABEL.get(status_int, '?')}] {inv_name} · {inv.get('owner', '')} · {inv.get('posting_date', '')}"

                # If label exceeds 100 chars, truncate gracefully with trailing dots
                display_name = label if len(label) <= 100 else f"{label[:97]}..."

                choices.append(
                    app_commands.Choice(
                        name=display_name,
                        value=inv_name,
                    )
                )
            return choices
        except Exception as e:
            logger.warning("ERPNext autocomplete error for %r: %s", doctype_value, e)
            return []


async def setup(bot: commands.Bot) -> None:
    if not all([settings.erpnext_base_url, settings.erpnext_api_key]):
        logger.warning(
            "ERPNext cog not loaded: missing ERPNEXT_BASE_URL or ERPNEXT_API_KEY"
        )
        return
    await bot.add_cog(InvoicesCog(bot))
