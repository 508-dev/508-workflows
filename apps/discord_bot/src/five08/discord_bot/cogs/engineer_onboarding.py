"""Discord commands for ERPNext engineer onboarding."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from five08.clients.erpnext import ERPNextAPIError, ERPNextClient
from five08.discord_bot.config import settings
from five08.discord_bot.utils.audit import DiscordAuditCogMixin
from five08.discord_bot.utils.role_decorators import require_role
from five08.engineer_onboarding import (
    ActivityCostRequest,
    EngineerOnboardingDuplicateNameError,
    EngineerOnboardingError,
    EngineerSetupRequest,
    add_engineer_to_project,
    setup_engineer,
)

logger = logging.getLogger(__name__)


class EngineerOnboardingCog(DiscordAuditCogMixin, commands.Cog):
    """ERPNext engineer setup and project assignment commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._init_audit_logger()

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

    def _setup_engineer(self, request: EngineerSetupRequest) -> dict[str, Any]:
        client = self._erpnext_client()
        try:
            return setup_engineer(client, request)
        finally:
            client.close()

    def _add_engineer_to_project(
        self,
        *,
        project_id: str,
        user: str,
        activity_cost: ActivityCostRequest | None,
    ) -> dict[str, Any]:
        client = self._erpnext_client()
        try:
            return add_engineer_to_project(
                client,
                project_id=project_id,
                user=user,
                activity_cost=activity_cost,
            )
        finally:
            client.close()

    def _audit_engineer_setup_denied(
        self,
        *,
        interaction: discord.Interaction,
        email: str,
        error: str,
    ) -> None:
        self._audit_command_safe(
            interaction=interaction,
            action="erpnext.engineer_setup",
            result="denied",
            metadata={"email": email, "error": error},
            resource_type="erpnext_user",
            resource_id=email or None,
        )

    def _audit_project_engineer_add_denied(
        self,
        *,
        interaction: discord.Interaction,
        project_id: str,
        user: str,
        activity_type: str,
        billing_rate: float | None,
        costing_rate: float | None,
        error: str,
    ) -> None:
        self._audit_command_safe(
            interaction=interaction,
            action="erpnext.project_engineer_add",
            result="denied",
            metadata={
                "project_id": project_id,
                "user": user,
                "activity_type": activity_type,
                "billing_rate": billing_rate,
                "costing_rate": costing_rate,
                "error": error,
            },
            resource_type="erpnext_project",
            resource_id=project_id or None,
        )

    @app_commands.command(
        name="setup-engineer",
        description="Create or link ERPNext User, Employee, and Supplier for an engineer.",
    )
    @app_commands.describe(
        email="The engineer's 508.dev email address.",
        name="The engineer's first and last name.",
        country="Supplier country, required only when creating a new Supplier.",
        department="Optional Employee department.",
        gender="Optional Employee gender value if ERPNext requires it.",
        date_of_birth="Optional Employee DOB if ERPNext requires it, YYYY-MM-DD.",
    )
    @require_role("Steering Committee")
    async def setup_engineer_command(
        self,
        interaction: discord.Interaction,
        email: str,
        name: str,
        country: str | None = None,
        department: str | None = None,
        gender: str | None = None,
        date_of_birth: str | None = None,
    ) -> None:
        """Set up one engineer in ERPNext."""
        await interaction.response.defer(ephemeral=True)
        normalized_email = email.strip().lower()
        normalized_name = " ".join(name.strip().split())
        normalized_country = country.strip() if country else None
        if not normalized_email or not normalized_email.endswith("@508.dev"):
            self._audit_engineer_setup_denied(
                interaction=interaction,
                email=normalized_email,
                error="invalid_email",
            )
            await interaction.followup.send(
                "❌ Enter a valid @508.dev email.",
                ephemeral=True,
            )
            return
        if not normalized_name:
            self._audit_engineer_setup_denied(
                interaction=interaction,
                email=normalized_email,
                error="missing_name",
            )
            await interaction.followup.send(
                "❌ Enter the engineer name.", ephemeral=True
            )
            return
        first_name, _, last_name = normalized_name.partition(" ")

        try:
            result = await asyncio.to_thread(
                self._setup_engineer,
                EngineerSetupRequest(
                    email=normalized_email,
                    first_name=first_name,
                    last_name=last_name or None,
                    country=normalized_country,
                    department=department,
                    gender=gender,
                    date_of_birth=date_of_birth,
                    create_user_permission=True,
                ),
            )
        except EngineerOnboardingDuplicateNameError as exc:
            self._audit_command_safe(
                interaction=interaction,
                action="erpnext.engineer_setup",
                result="denied",
                metadata={
                    "email": normalized_email,
                    "error": str(exc),
                    "matches": exc.matches,
                },
                resource_type="erpnext_user",
                resource_id=normalized_email,
            )
            matches = [
                str(match.get("label") or match.get("email") or match.get("name"))
                for match in exc.matches[:5]
            ]
            await interaction.followup.send(
                "⚠️ Similar ERPNext account exists. Confirm the intended person first:\n"
                + "\n".join(f"- {match}" for match in matches if match),
                ephemeral=True,
            )
            return
        except (EngineerOnboardingError, ValueError) as exc:
            self._audit_command_safe(
                interaction=interaction,
                action="erpnext.engineer_setup",
                result="denied",
                metadata={"email": normalized_email, "error": str(exc)},
                resource_type="erpnext_user",
                resource_id=normalized_email,
            )
            await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)
            return
        except ERPNextAPIError as exc:
            logger.error("ERPNext engineer setup failed: %s", exc)
            self._audit_command_safe(
                interaction=interaction,
                action="erpnext.engineer_setup",
                result="error",
                metadata={"email": normalized_email, "error": str(exc)},
                resource_type="erpnext_user",
                resource_id=normalized_email,
            )
            await interaction.followup.send(
                "❌ ERPNext engineer setup failed.", ephemeral=True
            )
            return

        self._audit_command_safe(
            interaction=interaction,
            action="erpnext.engineer_setup",
            result="success",
            metadata={
                "email": normalized_email,
                "employee": result.get("employee"),
                "supplier": result.get("supplier"),
                "created": result.get("created"),
                "updated": result.get("updated"),
            },
            resource_type="erpnext_user",
            resource_id=normalized_email,
        )
        await interaction.followup.send(
            "\n".join(
                [
                    "✅ Engineer setup complete.",
                    f"User: `{result.get('user')}`",
                    f"Employee: `{result.get('employee')}`",
                    f"Supplier: `{result.get('supplier')}`",
                ]
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="add-engineer-to-project",
        description="Add an ERPNext User to a Project and optionally configure rates.",
    )
    @app_commands.describe(
        project_id="ERPNext Project id.",
        user="ERPNext User email.",
        activity_type="Optional Activity Type for project-specific rates.",
        billing_rate="Optional client bill rate in USD/hr.",
        costing_rate="Optional engineer cost rate in USD/hr.",
    )
    @require_role("Steering Committee")
    async def add_engineer_to_project_command(
        self,
        interaction: discord.Interaction,
        project_id: str,
        user: str,
        activity_type: str | None = None,
        billing_rate: float | None = None,
        costing_rate: float | None = None,
    ) -> None:
        """Add one engineer to an ERPNext Project roster."""
        await interaction.response.defer(ephemeral=True)
        normalized_project_id = project_id.strip()
        normalized_user = user.strip().lower()
        normalized_activity_type = (activity_type or "").strip()
        if not normalized_project_id:
            self._audit_project_engineer_add_denied(
                interaction=interaction,
                project_id=normalized_project_id,
                user=normalized_user,
                activity_type=normalized_activity_type,
                billing_rate=billing_rate,
                costing_rate=costing_rate,
                error="missing_project_id",
            )
            await interaction.followup.send(
                "❌ Enter an ERPNext Project id.", ephemeral=True
            )
            return
        if not normalized_user.endswith("@508.dev"):
            self._audit_project_engineer_add_denied(
                interaction=interaction,
                project_id=normalized_project_id,
                user=normalized_user,
                activity_type=normalized_activity_type,
                billing_rate=billing_rate,
                costing_rate=costing_rate,
                error="invalid_user_email",
            )
            await interaction.followup.send(
                "❌ Enter the engineer's @508.dev ERPNext User email.",
                ephemeral=True,
            )
            return
        has_billing_rate = billing_rate is not None
        has_costing_rate = costing_rate is not None
        if (has_billing_rate or has_costing_rate) and not normalized_activity_type:
            self._audit_project_engineer_add_denied(
                interaction=interaction,
                project_id=normalized_project_id,
                user=normalized_user,
                activity_type=normalized_activity_type,
                billing_rate=billing_rate,
                costing_rate=costing_rate,
                error="activity_type_required",
            )
            await interaction.followup.send(
                "❌ Activity Type is required when setting bill or cost rates.",
                ephemeral=True,
            )
            return
        if normalized_activity_type and not (has_billing_rate and has_costing_rate):
            self._audit_project_engineer_add_denied(
                interaction=interaction,
                project_id=normalized_project_id,
                user=normalized_user,
                activity_type=normalized_activity_type,
                billing_rate=billing_rate,
                costing_rate=costing_rate,
                error="activity_cost_rates_required",
            )
            await interaction.followup.send(
                "❌ Billing rate and costing rate are required with Activity Type.",
                ephemeral=True,
            )
            return
        if (billing_rate is not None and billing_rate < 0) or (
            costing_rate is not None and costing_rate < 0
        ):
            self._audit_project_engineer_add_denied(
                interaction=interaction,
                project_id=normalized_project_id,
                user=normalized_user,
                activity_type=normalized_activity_type,
                billing_rate=billing_rate,
                costing_rate=costing_rate,
                error="negative_activity_cost_rate",
            )
            await interaction.followup.send(
                "❌ Billing and costing rates must be non-negative.",
                ephemeral=True,
            )
            return

        activity_cost = None
        if normalized_activity_type:
            activity_cost = ActivityCostRequest(
                user=normalized_user,
                activity_type=normalized_activity_type,
                billing_rate=billing_rate,
                costing_rate=costing_rate,
            )

        try:
            result = await asyncio.to_thread(
                self._add_engineer_to_project,
                project_id=normalized_project_id,
                user=normalized_user,
                activity_cost=activity_cost,
            )
        except (EngineerOnboardingError, ValueError) as exc:
            self._audit_command_safe(
                interaction=interaction,
                action="erpnext.project_engineer_add",
                result="denied",
                metadata={
                    "project_id": normalized_project_id,
                    "user": normalized_user,
                    "activity_type": normalized_activity_type,
                    "error": str(exc),
                },
                resource_type="erpnext_project",
                resource_id=normalized_project_id,
            )
            await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)
            return
        except ERPNextAPIError as exc:
            logger.error("ERPNext project engineer add failed: %s", exc)
            self._audit_command_safe(
                interaction=interaction,
                action="erpnext.project_engineer_add",
                result="error",
                metadata={
                    "project_id": normalized_project_id,
                    "user": normalized_user,
                    "activity_type": normalized_activity_type,
                    "error": str(exc),
                },
                resource_type="erpnext_project",
                resource_id=normalized_project_id,
            )
            await interaction.followup.send(
                "❌ ERPNext project engineer update failed.",
                ephemeral=True,
            )
            return

        activity_cost_result = result.get("activity_cost")
        activity_cost_error = result.get("activity_cost_error")
        lines = [
            (
                "⚠️ Engineer added to project, but Activity Cost failed."
                if activity_cost_error
                else "✅ Engineer added to project."
            ),
            f"Project: `{normalized_project_id}`",
            f"User: `{normalized_user}`",
        ]
        if isinstance(activity_cost_result, dict):
            lines.append(
                f"Activity Cost: `{activity_cost_result.get('activity_cost')}`"
            )
        if activity_cost_error:
            lines.append(f"Activity Cost error: `{activity_cost_error}`")
        self._audit_command_safe(
            interaction=interaction,
            action="erpnext.project_engineer_add",
            result="partial_success" if activity_cost_error else "success",
            metadata={
                "project_id": normalized_project_id,
                "user": normalized_user,
                "activity_type": normalized_activity_type,
                "activity_cost": activity_cost_result,
                "activity_cost_error": activity_cost_error,
            },
            resource_type="erpnext_project",
            resource_id=normalized_project_id,
        )
        await interaction.followup.send("\n".join(lines), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EngineerOnboardingCog(bot))
