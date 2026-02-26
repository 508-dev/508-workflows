"""
CRM integration cog for the 508.dev Discord bot.

This cog provides commands for interacting with EspoCRM through Discord slash commands.
It allows team members to quickly access CRM data without leaving Discord.
"""

import asyncio
import ast
import io
import json
import logging
from datetime import date, datetime
import re
from typing import Any

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from five08.discord_bot.config import settings
from five08.clients import espo
from five08.skills import normalize_skill_list
from five08.discord_bot.utils.audit import DiscordAuditLogger
from five08.discord_bot.utils.role_decorators import (
    require_role,
    check_user_roles_with_hierarchy,
)

logger = logging.getLogger(__name__)

ID_VERIFIED_FIELD = "cIdVerified"
ID_VERIFIED_AT_FIELD = "cIdVerifiedAt"
ID_VERIFIED_BY_FIELD = "cIdVerifiedBy"

EspoAPI = espo.EspoAPI
EspoAPIError = espo.EspoAPIError


class ResumeButtonView(discord.ui.View):
    """View containing resume download buttons for contact search results."""

    def __init__(self) -> None:
        super().__init__(timeout=300)  # 5 minute timeout

    def add_resume_button(self, contact_name: str, resume_id: str) -> None:
        """Add a resume download button for a contact."""
        if len(self.children) >= 5:  # Discord limit of 5 buttons per row
            return

        button = ResumeDownloadButton(contact_name, resume_id)
        self.add_item(button)


class ResumeDownloadButton(discord.ui.Button[discord.ui.View]):
    """Button for downloading a specific contact's resume."""

    def __init__(self, contact_name: str, resume_id: str) -> None:
        self.contact_name = contact_name
        self.resume_id = resume_id

        # Truncate long names for button label
        label = f"📄 Resume: {contact_name}"
        if len(label) > 80:  # Discord button label limit
            # Account for "📄 Resume: " (11 chars) + "..." (3 chars) = 14 chars
            max_name_length = 80 - 14
            label = f"📄 Resume: {contact_name[:max_name_length]}..."

        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
            custom_id=f"resume_{resume_id}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle resume download button click."""
        try:
            # Get the CRM cog to access the API
            cog = interaction.client.get_cog("CRMCog")  # type: ignore[attr-defined]
            if not cog:
                await interaction.response.send_message(
                    "❌ CRM functionality not available.", ephemeral=True
                )
                return

            # Check if user has Member role
            if not cog._check_member_role(interaction):
                cog._audit_command(
                    interaction=interaction,
                    action="crm.resume_download_button",
                    result="denied",
                    metadata={"reason": "missing_member_role"},
                    resource_type="discord_ui_action",
                    resource_id=self.resume_id,
                )
                await interaction.response.send_message(
                    "❌ You must have the Member role to download resumes.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer(ephemeral=True)

            # Use shared download method
            download_ok = await cog._download_and_send_resume(
                interaction, self.contact_name, self.resume_id
            )
            cog._audit_command(
                interaction=interaction,
                action="crm.resume_download_button",
                result="success" if download_ok else "error",
                metadata={"contact_name": self.contact_name},
                resource_type="crm_contact",
                resource_id=self.resume_id,
            )

        except Exception as e:
            logger.error(f"Unexpected error in resume button callback: {e}")
            if "cog" in locals() and cog:
                cog._audit_command(
                    interaction=interaction,
                    action="crm.resume_download_button",
                    result="error",
                    metadata={"error": str(e)},
                    resource_type="discord_ui_action",
                    resource_id=self.resume_id,
                )
            await interaction.followup.send(
                "❌ An unexpected error occurred while downloading the resume."
            )


class ContactSelectionView(discord.ui.View):
    """View containing contact selection buttons for Discord linking."""

    def __init__(self, user: discord.Member, search_term: str) -> None:
        super().__init__(timeout=300)  # 5 minute timeout
        self.user = user
        self.search_term = search_term

    def add_contact_button(self, contact: dict[str, Any]) -> None:
        """Add a contact selection button."""
        if len(self.children) >= 5:  # Discord limit of 5 buttons per row
            return

        button = ContactSelectionButton(contact, self.user)
        self.add_item(button)


class ContactSelectionButton(discord.ui.Button[ContactSelectionView]):
    """Button for selecting a contact to link to Discord user."""

    def __init__(self, contact: dict[str, Any], user: discord.Member) -> None:
        # Create button label from contact name (truncate if too long)
        contact_name = contact.get("name", "Unknown")
        label = contact_name[:80] if len(contact_name) > 80 else contact_name

        super().__init__(style=discord.ButtonStyle.primary, label=label, emoji="🔗")
        self.contact = contact
        self.user = user

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle contact selection and perform the Discord linking."""
        try:
            # Check if user has required role
            if not hasattr(
                interaction.user, "roles"
            ) or not check_user_roles_with_hierarchy(
                interaction.user.roles, ["Steering Committee"]
            ):
                await interaction.response.send_message(
                    "❌ You must have Steering Committee role or higher to use this command.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer(ephemeral=True)

            # Get the CRM cog to perform the linking
            if not self.view:
                await interaction.followup.send("❌ View not found.")
                return

            from discord.ext import commands

            bot = interaction.client
            assert isinstance(bot, commands.Bot)
            cog = bot.get_cog("CRMCog")
            if not cog or not isinstance(cog, CRMCog):
                await interaction.followup.send("❌ CRM cog not found.")
                return

            # Perform the Discord linking
            success = await cog._perform_discord_linking(
                interaction, self.user, self.contact
            )

            if success and self.view:
                # Disable all buttons in the view
                for item in self.view.children:
                    if isinstance(item, discord.ui.Button):
                        item.disabled = True

                # Update the original message to show selection was made
                embed = discord.Embed(
                    title="✅ Contact Selected",
                    description=f"Selected **{self.contact.get('name', 'Unknown')}** for linking.",
                    color=0x00FF00,
                )

                # Edit the original message with disabled buttons
                if interaction.message:
                    try:
                        await interaction.message.edit(embed=embed, view=self.view)
                    except discord.NotFound:
                        # Message was deleted or not found, ignore this error
                        logger.debug(
                            "Original message not found when trying to update button view"
                        )
                    except discord.HTTPException as e:
                        # Other Discord API errors
                        logger.warning(f"Failed to update original message: {e}")

        except Exception as e:
            logger.error(f"Error in contact selection callback: {e}")
            await interaction.followup.send(
                "❌ An error occurred while linking the contact."
            )


class MarkIdVerifiedSelectionButton(discord.ui.Button["MarkIdVerifiedSelectionView"]):
    """Button for selecting a contact to mark ID verification on."""

    def __init__(
        self,
        contact: dict[str, Any],
        verified_by: str | None,
        verified_at: str,
        requester_id: int,
    ) -> None:
        contact_name = contact.get("name", "Unknown")
        label = contact_name[:80] if len(contact_name) > 80 else contact_name
        super().__init__(style=discord.ButtonStyle.success, label=label, emoji="✅")
        self.contact = contact
        self.verified_by = verified_by
        self.verified_at = verified_at
        self.requester_id = requester_id

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle contact selection and perform the ID verification."""
        try:
            if not self.view:
                await interaction.response.send_message("❌ View not found.")
                return
            if interaction.user.id != self.requester_id:
                await interaction.response.send_message(
                    "❌ Only the command requester can confirm this action.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer(ephemeral=True)
            await self.view.crm_cog._mark_id_verified_for_contact(
                interaction=interaction,
                contact=self.contact,
                verified_by=self.verified_by,
                verified_at=self.verified_at,
            )

            for item in self.view.children:
                if isinstance(item, discord.ui.Button):
                    item.disabled = True

            if interaction.message:
                try:
                    await interaction.message.edit(view=self.view)
                except discord.NotFound:
                    pass
                except discord.HTTPException as exc:
                    logger.warning(
                        f"Failed to update ID verification selection view: {exc}"
                    )
        except Exception as exc:
            logger.error(f"Error in ID verified selection callback: {exc}")
            await interaction.followup.send(
                "❌ An error occurred while marking ID verification."
            )


class MarkIdVerifiedSelectionView(discord.ui.View):
    """View containing contact selection buttons for ID verification."""

    def __init__(
        self,
        crm_cog: "CRMCog",
        requester_id: int,
        verified_by: str | None,
        verified_at: str,
    ) -> None:
        super().__init__(timeout=300)  # 5 minute timeout
        self.crm_cog = crm_cog
        self.requester_id = requester_id
        self.verified_by = verified_by
        self.verified_at = verified_at

    def add_contact_button(
        self,
        contact: dict[str, Any],
    ) -> None:
        """Add a contact selection button."""
        if len(self.children) >= 5:
            return
        button = MarkIdVerifiedSelectionButton(
            contact=contact,
            verified_by=self.verified_by,
            verified_at=self.verified_at,
            requester_id=self.requester_id,
        )
        self.add_item(button)


class ResumeConfirmationView(discord.ui.View):
    """View for confirming resume upload when duplicate is detected."""

    def __init__(
        self,
        crm_cog: "CRMCog",
        interaction: discord.Interaction,
        file: discord.Attachment,
        contact_id: str,
        contact_name: str,
        existing_resume_id: str,
        overwrite: bool = False,
    ) -> None:
        super().__init__(timeout=300)  # 5 minute timeout
        self.crm_cog = crm_cog
        self.original_interaction = interaction
        self.file = file
        self.contact_id = contact_id
        self.contact_name = contact_name
        self.existing_resume_id = existing_resume_id
        self.overwrite = overwrite

    @discord.ui.button(
        label="Yes, Upload Anyway", style=discord.ButtonStyle.primary, emoji="📄"
    )
    async def confirm_upload(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["ResumeConfirmationView"],
    ) -> None:
        """Proceed with the upload despite duplicate."""
        await interaction.response.defer()

        try:
            # Download file content from Discord
            file_content = await self.file.read()

            # Upload file to EspoCRM
            attachment = self.crm_cog.espo_api.upload_file(
                file_content=file_content,
                filename=self.file.filename,
                related_type="Contact",
                related_id=self.contact_id,
                field="resume",
            )

            attachment_id = attachment.get("id")
            if not attachment_id:
                await interaction.followup.send("❌ Failed to upload file to CRM.")
                return

            # Update contact's resume field (use original overwrite setting)
            if await self.crm_cog._update_contact_resume(
                self.contact_id, attachment_id, self.overwrite
            ):
                # Create success embed
                embed = discord.Embed(
                    title="✅ Resume Uploaded Successfully",
                    description="Resume has been uploaded and linked to the contact.",
                    color=0x00FF00,
                )
                embed.add_field(name="👤 Contact", value=self.contact_name, inline=True)
                embed.add_field(name="📄 File", value=self.file.filename, inline=True)
                embed.add_field(
                    name="📁 Size", value=f"{self.file.size / 1024:.1f} KB", inline=True
                )

                # Add CRM link
                profile_url = f"{self.crm_cog.base_url}/#Contact/view/{self.contact_id}"
                embed.add_field(
                    name="🔗 CRM Profile",
                    value=f"[View in CRM]({profile_url})",
                    inline=False,
                )

                await interaction.followup.send(embed=embed)

                logger.info(
                    f"Resume uploaded for {self.contact_name} (ID: {self.contact_id}) "
                    f"by {self.original_interaction.user.name}: {self.file.filename}"
                )
            else:
                await interaction.followup.send(
                    "⚠️ File uploaded but failed to link to contact. Please check CRM manually."
                )

        except Exception as e:
            logger.error(f"Error during confirmed resume upload: {e}")
            await interaction.followup.send(
                "❌ An error occurred while uploading the resume."
            )

        # Disable all buttons
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

        # Update the original message
        try:
            if interaction.message:
                await interaction.message.edit(view=self)
        except discord.NotFound:
            pass

    @discord.ui.button(
        label="No, Cancel", style=discord.ButtonStyle.secondary, emoji="❌"
    )
    async def cancel_upload(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["ResumeConfirmationView"],
    ) -> None:
        """Cancel the upload."""
        await interaction.response.send_message(
            "📄 Resume upload cancelled. No changes were made.", ephemeral=True
        )

        # Disable all buttons
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

        # Update the original message
        try:
            if interaction.message:
                await interaction.message.edit(view=self)
        except discord.NotFound:
            pass


class ResumeUpdateConfirmationView(discord.ui.View):
    """Confirm extracted profile updates before writing to CRM."""

    def __init__(
        self,
        *,
        crm_cog: "CRMCog",
        requester_id: int,
        contact_id: str,
        contact_name: str,
        proposed_updates: dict[str, str],
        link_discord: dict[str, str] | None = None,
    ) -> None:
        super().__init__(timeout=300)
        self.crm_cog = crm_cog
        self.requester_id = requester_id
        self.contact_id = contact_id
        self.contact_name = contact_name
        self.proposed_updates = proposed_updates
        self.link_discord = link_discord

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Allow only the original requester to confirm/cancel."""
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "❌ Only the command requester can confirm these updates.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Confirm Updates", style=discord.ButtonStyle.primary)
    async def confirm_updates(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["ResumeUpdateConfirmationView"],
    ) -> None:
        """Apply confirmed updates through the worker."""
        await interaction.response.defer(thinking=True, ephemeral=True)

        def _audit_apply_event(result: str, metadata: dict[str, Any]) -> None:
            try:
                self.crm_cog._audit_command(
                    interaction=interaction,
                    action="crm.upload_resume.apply",
                    result=result,
                    metadata=metadata,
                    resource_type="crm_contact",
                    resource_id=self.contact_id,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to write resume apply audit event for contact_id=%s: %s",
                    self.contact_id,
                    exc,
                )

        try:
            apply_job_id = await self.crm_cog._enqueue_resume_apply_job(
                contact_id=self.contact_id,
                updates=self.proposed_updates,
                link_discord=self.link_discord,
            )
        except Exception as exc:
            logger.error("Failed to enqueue resume apply job: %s", exc)
            _audit_apply_event(
                "error",
                {
                    "contact_id": self.contact_id,
                    "stage": "apply_enqueue",
                    "error": str(exc),
                    "updated_fields": [],
                    "proposed_updates_count": len(self.proposed_updates),
                    "link_member_requested": bool(self.link_discord),
                    "link_discord_applied": None,
                },
            )
            await interaction.followup.send(
                "❌ Failed to enqueue CRM apply job. Please try again.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            "🛠️ Applying confirmed updates to CRM...",
            ephemeral=True,
        )
        try:
            apply_result = await self.crm_cog._wait_for_backend_job_result(apply_job_id)
        except Exception as exc:
            logger.error(
                "Worker polling failed for apply_job_id=%s contact_id=%s error=%s",
                apply_job_id,
                self.contact_id,
                exc,
            )
            _audit_apply_event(
                "error",
                {
                    "contact_id": self.contact_id,
                    "stage": "apply_polling_failed",
                    "job_id": apply_job_id,
                    "error": str(exc),
                    "updated_fields": [],
                    "link_discord_applied": None,
                },
            )
            await interaction.followup.send(
                "⚠️ Resume apply polling failed. Please retry or check CRM manually.",
                ephemeral=True,
            )
            return

        if not apply_result:
            _audit_apply_event(
                "error",
                {
                    "contact_id": self.contact_id,
                    "stage": "apply_timeout",
                    "job_id": apply_job_id,
                    "updated_fields": [],
                    "link_discord_applied": None,
                },
            )
            await interaction.followup.send(
                "⚠️ Timed out waiting for apply job. Please check again shortly.",
                ephemeral=True,
            )
            return

        status = str(apply_result.get("status", "unknown"))
        result = apply_result.get("result")
        updated_fields: list[str] = []
        link_discord_applied: bool | None = None
        if isinstance(result, dict):
            raw_fields = result.get("updated_fields")
            if isinstance(raw_fields, list):
                updated_fields = [str(field) for field in raw_fields]
            raw_link_applied = result.get("link_discord_applied")
            if isinstance(raw_link_applied, bool):
                link_discord_applied = raw_link_applied

        if status != "succeeded":
            _audit_apply_event(
                "error",
                {
                    "contact_id": self.contact_id,
                    "stage": "apply_failed",
                    "job_id": apply_job_id,
                    "job_status": status,
                    "last_error": str(apply_result.get("last_error", "")),
                    "updated_fields": updated_fields,
                    "link_discord_applied": link_discord_applied,
                },
            )
            await interaction.followup.send(
                f"❌ Apply job failed (status: {status}). "
                f"Error: {apply_result.get('last_error') or 'Unknown error'}",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="✅ CRM Updated",
            description=f"Applied updates for **{self.contact_name}**.",
            color=0x00FF00,
        )
        embed.add_field(
            name="Updated Fields",
            value=", ".join(updated_fields) if updated_fields else "No field changes",
            inline=False,
        )
        profile_url = f"{self.crm_cog.base_url}/#Contact/view/{self.contact_id}"
        embed.add_field(name="🔗 CRM Profile", value=f"[View in CRM]({profile_url})")
        _audit_apply_event(
            "success",
            {
                "contact_id": self.contact_id,
                "stage": "apply_succeeded",
                "job_id": apply_job_id,
                "updated_fields": updated_fields,
                "proposed_updates_count": len(self.proposed_updates),
                "link_member_requested": bool(self.link_discord),
                "link_discord_applied": link_discord_applied,
            },
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        if interaction.message:
            try:
                await interaction.message.edit(view=self)
            except discord.NotFound:
                pass
            except discord.HTTPException as exc:
                logger.warning("Failed to update confirmation view: %s", exc)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_updates(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["ResumeUpdateConfirmationView"],
    ) -> None:
        """Cancel CRM updates after preview."""
        self.crm_cog._audit_command(
            interaction=interaction,
            action="crm.upload_resume",
            result="denied",
            metadata={
                "stage": "apply_cancelled",
                "proposed_updates_count": len(self.proposed_updates),
                "link_member_requested": bool(self.link_discord),
            },
            resource_type="crm_contact",
            resource_id=self.contact_id,
        )
        await interaction.response.send_message(
            "No CRM profile updates were applied.", ephemeral=True
        )
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        if interaction.message:
            try:
                await interaction.message.edit(view=self)
            except discord.NotFound:
                pass
            except discord.HTTPException as exc:
                logger.warning("Failed to update confirmation view: %s", exc)


class ResumeCreateContactView(discord.ui.View):
    """Prompt to create a new contact from parsed resume data."""

    def __init__(
        self,
        crm_cog: "CRMCog",
        interaction: discord.Interaction,
        file_content: bytes,
        filename: str,
        file_size: int,
        search_term: str | None,
        overwrite: bool,
        link_user: discord.Member | None,
        inferred_contact_meta: dict[str, str] | None,
        target_scope: str,
    ) -> None:
        super().__init__(timeout=180)
        self.crm_cog = crm_cog
        self.original_interaction = interaction
        self.file_content = file_content
        self.filename = filename
        self.file_size = file_size
        self.search_term = search_term
        self.overwrite = overwrite
        self.link_user = link_user
        self.inferred_contact_meta = inferred_contact_meta
        self.target_scope = target_scope

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_interaction.user.id:
            await interaction.response.send_message(
                "❌ Only the command requester can confirm contact creation.",
                ephemeral=True,
            )
            return False
        return True

    async def _finalize(
        self, interaction: discord.Interaction, *, error_message: str | None = None
    ) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        if interaction.message:
            try:
                if error_message:
                    await interaction.followup.send(error_message, ephemeral=True)
                await interaction.message.edit(view=self)
            except discord.NotFound:
                pass
            except discord.HTTPException as exc:
                logger.warning("Failed to update create-contact view: %s", exc)

    @discord.ui.button(label="Create Contact", style=discord.ButtonStyle.primary)
    async def confirm_create(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["ResumeCreateContactView"],
    ) -> None:
        """Create the inferred contact and continue resume upload."""
        await interaction.response.defer(ephemeral=True)
        try:
            create_payload = self.crm_cog._build_resume_create_contact_payload(
                file_content=self.file_content
            )
            target_contact = self.crm_cog.espo_api.request(
                "POST", "Contact", create_payload
            )
            contact_id = (
                target_contact.get("id") if isinstance(target_contact, dict) else None
            )

            if not contact_id:
                raise ValueError("Created contact had no valid ID.")

            logger.info(
                "Created new contact %s from resume for %s",
                contact_id,
                interaction.user.name,
            )

            await self.crm_cog._upload_resume_attachment_to_contact(
                interaction=interaction,
                file_content=self.file_content,
                filename=self.filename,
                file_size=self.file_size,
                contact=target_contact,
                target_scope="created",
                search_term=self.search_term,
                overwrite=self.overwrite,
                link_user=self.link_user,
                inferred_contact_meta=self.inferred_contact_meta,
            )
        except (EspoAPIError, ValueError, TypeError) as exc:
            self.crm_cog._audit_command(
                interaction=interaction,
                action="crm.upload_resume",
                result="error",
                metadata={
                    "filename": self.filename,
                    "target_scope": self.target_scope,
                    "reason": "contact_create_failed",
                    "error": str(exc),
                },
            )
            await interaction.followup.send(
                "⚠️ Could not create a contact from this resume. "
                "Please provide `search_term` or `link_user`.",
                ephemeral=True,
            )
        finally:
            await self._finalize(interaction)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_create(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["ResumeCreateContactView"],
    ) -> None:
        await interaction.response.send_message(
            "ℹ️ Resume upload cancelled. No new contact was created.",
            ephemeral=True,
        )
        await self._finalize(interaction)


class CRMCog(commands.Cog):
    """CRM integration cog for EspoCRM operations."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Construct API URL from base URL
        api_url = settings.espo_base_url.rstrip("/") + "/api/v1"
        self.espo_api = EspoAPI(api_url, settings.espo_api_key)
        # Store base URL for profile links
        self.base_url = settings.espo_base_url.rstrip("/")
        self.audit_logger = DiscordAuditLogger(
            base_url=settings.audit_api_base_url,
            shared_secret=settings.api_shared_secret,
            timeout_seconds=settings.audit_api_timeout_seconds,
        )

    def _audit_command(
        self,
        *,
        interaction: discord.Interaction,
        action: str,
        result: str,
        metadata: dict[str, Any] | None = None,
        resource_type: str | None = "discord_command",
        resource_id: str | None = None,
    ) -> None:
        """Queue a best-effort audit write for CRM command activity."""
        self.audit_logger.log_command(
            interaction=interaction,
            action=action,
            result=result,
            metadata=metadata,
            resource_type=resource_type,
            resource_id=resource_id,
        )

    def _backend_headers(self) -> dict[str, str]:
        """Build auth headers for internal backend API calls."""
        if not settings.api_shared_secret:
            raise ValueError("API_SHARED_SECRET is required for backend API requests.")
        return {
            "X-API-Secret": settings.api_shared_secret,
            "Content-Type": "application/json",
        }

    def _backend_url(self, path: str) -> str:
        return f"{settings.backend_api_base_url.rstrip('/')}{path}"

    async def _enqueue_resume_extract_job(
        self, *, contact_id: str, attachment_id: str, filename: str
    ) -> str:
        payload = {
            "contact_id": contact_id,
            "attachment_id": attachment_id,
            "filename": filename,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._backend_url("/jobs/resume-extract"),
                headers=self._backend_headers(),
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                data = await response.json()
                if response.status != 202:
                    raise ValueError(f"Backend extract enqueue failed: {data}")
                job_id = data.get("job_id")
                if not isinstance(job_id, str) or not job_id:
                    raise ValueError("Missing backend extract job_id in response.")
                return job_id

    async def _enqueue_resume_apply_job(
        self,
        *,
        contact_id: str,
        updates: dict[str, str],
        link_discord: dict[str, str] | None = None,
    ) -> str:
        payload = {
            "contact_id": contact_id,
            "updates": updates,
            "link_discord": link_discord,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._backend_url("/jobs/resume-apply"),
                headers=self._backend_headers(),
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                data = await response.json()
                if response.status != 202:
                    raise ValueError(f"Backend apply enqueue failed: {data}")
                job_id = data.get("job_id")
                if not isinstance(job_id, str) or not job_id:
                    raise ValueError("Missing backend apply job_id in response.")
                return job_id

    async def _get_backend_job_status(self, job_id: str) -> dict[str, Any]:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                self._backend_url(f"/jobs/{job_id}"),
                headers=self._backend_headers(),
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                data = await response.json()
                if response.status != 200:
                    raise ValueError(f"Backend job status failed: {data}")
                if not isinstance(data, dict):
                    raise ValueError("Backend job status response must be an object.")
                return data

    async def _wait_for_backend_job_result(
        self, job_id: str, *, timeout_seconds: int = 180, poll_seconds: float = 2.0
    ) -> dict[str, Any] | None:
        """Poll backend job status until terminal or timeout."""
        terminal = {"succeeded", "dead", "canceled"}
        max_attempts = max(1, int(timeout_seconds / poll_seconds))

        for _ in range(max_attempts):
            job = await self._get_backend_job_status(job_id)
            status = str(job.get("status", ""))
            if status in terminal:
                return job
            await asyncio.sleep(poll_seconds)

        return None

    def _build_resume_preview_embed(
        self,
        *,
        contact_id: str,
        contact_name: str,
        result: dict[str, Any],
        link_member: discord.Member | None,
    ) -> tuple[discord.Embed, dict[str, str]]:
        """Render backend extraction result as a Discord preview embed."""
        proposed_updates_raw = result.get("proposed_updates")
        proposed_updates: dict[str, str] = {}
        if isinstance(proposed_updates_raw, dict):
            proposed_updates = {
                str(field): str(value)
                for field, value in proposed_updates_raw.items()
                if value is not None and str(value).strip()
            }

        changes = result.get("proposed_changes")
        new_skills = result.get("new_skills")
        skipped = result.get("skipped")
        extracted_profile = result.get("extracted_profile")

        embed = discord.Embed(
            title="🧾 Resume Parsed",
            description=f"Review extracted updates for **{contact_name}**.",
            color=0x0099FF,
        )

        if isinstance(changes, list) and changes:
            lines: list[str] = []
            for change in changes[:8]:
                if not isinstance(change, dict):
                    continue
                label = str(change.get("label", change.get("field", "Field")))
                current = str(change.get("current", "None"))
                proposed = str(change.get("proposed", ""))
                lines.append(f"**{label}**: `{current}` → `{proposed}`")
            embed.add_field(
                name="Proposed Changes",
                value="\n".join(lines) if lines else "No changes",
                inline=False,
            )
        else:
            embed.add_field(
                name="Proposed Changes",
                value="No CRM field updates were extracted.",
                inline=False,
            )

        if isinstance(new_skills, list) and new_skills:
            formatted_skills = ", ".join(str(skill) for skill in new_skills[:25])
            embed.add_field(
                name="New Skills",
                value=formatted_skills,
                inline=False,
            )

        if isinstance(skipped, list) and skipped:
            skip_lines: list[str] = []
            for item in skipped[:4]:
                if not isinstance(item, dict):
                    continue
                field = str(item.get("field", "field"))
                reason = str(item.get("reason", "Skipped"))
                value = str(item.get("value", ""))
                skip_lines.append(f"`{field}`: `{value}` ({reason})")
            if skip_lines:
                embed.add_field(
                    name="Skipped",
                    value="\n".join(skip_lines),
                    inline=False,
                )

        if isinstance(extracted_profile, dict):
            confidence = extracted_profile.get("confidence")
            source = extracted_profile.get("source")
            if confidence is not None or source:
                embed.add_field(
                    name="Extraction",
                    value=f"Source: `{source or 'unknown'}` | Confidence: `{confidence}`",
                    inline=False,
                )

        if link_member:
            embed.add_field(
                name="Discord Link",
                value=f"Will link contact to {link_member.mention}",
                inline=False,
            )

        profile_url = f"{self.base_url}/#Contact/view/{contact_id}"
        embed.add_field(name="🔗 CRM Profile", value=f"[View in CRM]({profile_url})")
        return embed, proposed_updates

    async def _run_resume_extract_and_preview(
        self,
        *,
        interaction: discord.Interaction,
        contact_id: str,
        contact_name: str,
        attachment_id: str,
        filename: str,
        link_member: discord.Member | None,
    ) -> None:
        """Kick off worker extraction and show confirmation preview."""
        try:
            job_id = await self._enqueue_resume_extract_job(
                contact_id=contact_id,
                attachment_id=attachment_id,
                filename=filename,
            )
        except Exception as exc:
            logger.error("Failed to enqueue resume extract job: %s", exc)
            self._audit_command(
                interaction=interaction,
                action="crm.upload_resume",
                result="error",
                metadata={
                    "filename": filename,
                    "attachment_id": attachment_id,
                    "stage": "extract_enqueue",
                    "error": str(exc),
                },
                resource_type="crm_contact",
                resource_id=str(contact_id),
            )
            await interaction.followup.send(
                "⚠️ Resume uploaded, but extraction job could not be enqueued.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            "📥 Resume uploaded. Extracting profile fields now...",
            ephemeral=True,
        )

        try:
            job = await self._wait_for_backend_job_result(job_id)
        except Exception as exc:
            logger.error("Worker polling failed for job_id=%s error=%s", job_id, exc)
            self._audit_command(
                interaction=interaction,
                action="crm.upload_resume",
                result="error",
                metadata={
                    "filename": filename,
                    "attachment_id": attachment_id,
                    "job_id": job_id,
                    "stage": "extract_polling",
                    "error": str(exc),
                },
                resource_type="crm_contact",
                resource_id=str(contact_id),
            )
            await interaction.followup.send(
                "⚠️ Resume uploaded, but extraction polling failed.",
                ephemeral=True,
            )
            return
        if not job:
            self._audit_command(
                interaction=interaction,
                action="crm.upload_resume",
                result="error",
                metadata={
                    "filename": filename,
                    "attachment_id": attachment_id,
                    "job_id": job_id,
                    "stage": "extract_timeout",
                },
                resource_type="crm_contact",
                resource_id=str(contact_id),
            )
            await interaction.followup.send(
                "⚠️ Timed out waiting for extraction result. Try again in a moment.",
                ephemeral=True,
            )
            return

        status = str(job.get("status", "unknown"))
        if status != "succeeded":
            self._audit_command(
                interaction=interaction,
                action="crm.upload_resume",
                result="error",
                metadata={
                    "filename": filename,
                    "attachment_id": attachment_id,
                    "job_id": job_id,
                    "stage": "extract_failed",
                    "job_status": status,
                    "last_error": str(job.get("last_error", "")),
                },
                resource_type="crm_contact",
                resource_id=str(contact_id),
            )
            await interaction.followup.send(
                f"❌ Extraction job failed (status: {status}). "
                f"Error: {job.get('last_error') or 'Unknown error'}",
                ephemeral=True,
            )
            return

        result = job.get("result")
        if not isinstance(result, dict):
            self._audit_command(
                interaction=interaction,
                action="crm.upload_resume",
                result="error",
                metadata={
                    "filename": filename,
                    "attachment_id": attachment_id,
                    "job_id": job_id,
                    "stage": "extract_malformed_result",
                },
                resource_type="crm_contact",
                resource_id=str(contact_id),
            )
            await interaction.followup.send(
                "❌ Extraction result was empty or malformed.",
                ephemeral=True,
            )
            return

        if not result.get("success", False):
            self._audit_command(
                interaction=interaction,
                action="crm.upload_resume",
                result="error",
                metadata={
                    "filename": filename,
                    "attachment_id": attachment_id,
                    "job_id": job_id,
                    "stage": "extract_unsuccessful",
                    "error": str(result.get("error", "")),
                },
                resource_type="crm_contact",
                resource_id=str(contact_id),
            )
            await interaction.followup.send(
                f"❌ Resume extraction failed: {result.get('error') or 'Unknown error'}",
                ephemeral=True,
            )
            return

        embed, proposed_updates = self._build_resume_preview_embed(
            contact_id=contact_id,
            contact_name=contact_name,
            result=result,
            link_member=link_member,
        )

        if not proposed_updates and not link_member:
            self._audit_command(
                interaction=interaction,
                action="crm.upload_resume",
                result="success",
                metadata={
                    "filename": filename,
                    "attachment_id": attachment_id,
                    "job_id": job_id,
                    "stage": "preview_no_changes",
                },
                resource_type="crm_contact",
                resource_id=str(contact_id),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        link_discord_payload: dict[str, str] | None = None
        if link_member:
            link_discord_payload = {
                "user_id": str(link_member.id),
                "username": str(link_member),
            }

        view = ResumeUpdateConfirmationView(
            crm_cog=self,
            requester_id=interaction.user.id,
            contact_id=contact_id,
            contact_name=contact_name,
            proposed_updates=proposed_updates,
            link_discord=link_discord_payload,
        )
        self._audit_command(
            interaction=interaction,
            action="crm.upload_resume",
            result="success",
            metadata={
                "filename": filename,
                "attachment_id": attachment_id,
                "job_id": job_id,
                "stage": "preview_ready",
                "proposed_updates_count": len(proposed_updates),
                "link_member_requested": bool(link_member),
            },
            resource_type="crm_contact",
            resource_id=str(contact_id),
        )
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    async def _download_and_send_resume(
        self, interaction: discord.Interaction, contact_name: str, resume_id: str
    ) -> bool:
        """Download and send a resume file as a Discord attachment."""
        try:
            # Download the resume file
            file_content = self.espo_api.download_file(f"Attachment/file/{resume_id}")

            # Get file metadata to determine filename
            file_info = self.espo_api.request("GET", f"Attachment/{resume_id}")
            filename = file_info.get("name", f"{contact_name}_resume.pdf")

            # Create Discord file object
            file_buffer = io.BytesIO(file_content)
            discord_file = discord.File(file_buffer, filename=filename)

            await interaction.followup.send(
                f"📄 Resume for **{contact_name}**:", file=discord_file
            )
            return True

        except EspoAPIError as e:
            logger.error(f"Failed to download resume {resume_id}: {e}")
            await interaction.followup.send(f"❌ Failed to download resume: {str(e)}")
            return False

    def _check_member_role(self, interaction: discord.Interaction) -> bool:
        """Check if user has Member role or higher for resume access."""
        if not hasattr(interaction.user, "roles"):
            return False
        return check_user_roles_with_hierarchy(interaction.user.roles, ["Member"])

    def _parse_contact_skill_attrs(self, value: Any) -> dict[str, int]:
        """Parse skill attributes from a contact record into normalized strengths."""
        if value is None:
            return {}

        candidate = value
        if isinstance(candidate, str):
            raw_value = candidate.strip()
            if not raw_value:
                return {}
            parsed_payload = self._parse_json_object_with_recovery(raw_value)
            if parsed_payload is None:
                return {}
            candidate = parsed_payload

        if not isinstance(candidate, dict):
            return {}

        parsed: dict[str, int] = {}
        for raw_skill, payload in candidate.items():
            normalized_skill = self._normalize_skill(str(raw_skill))
            if not normalized_skill:
                continue

            if not isinstance(payload, dict):
                continue

            raw_strength = payload.get("strength")
            if raw_strength is None:
                continue
            try:
                strength = int(float(raw_strength))
            except (TypeError, ValueError):
                continue

            if not 1 <= strength <= 5:
                continue

            parsed[normalized_skill.casefold()] = strength

        return parsed

    def _normalize_skill(self, value: str) -> str:
        """Normalize one skill name from source data."""
        normalized = normalize_skill_list([value])
        return normalized[0] if normalized else ""

    def _parse_skills_input(self, raw: str) -> dict[str, int]:
        """Parse 'python:5, react:4' into {normalized_skill: strength}.

        Raises ValueError with a user-friendly message on bad input.
        """
        if not raw or not raw.strip():
            raise ValueError("Skills input cannot be empty.")

        parsed: dict[str, int] = {}
        for token in raw.split(","):
            token = token.strip()
            if not token:
                continue
            if ":" not in token:
                raise ValueError(
                    f"Invalid format `{token}`, expected `skill:strength` (e.g. `python:5`)."
                )
            skill_raw, strength_raw = token.rsplit(":", 1)
            skill_name = self._normalize_skill(skill_raw.strip())
            if not skill_name:
                raise ValueError(f"Empty skill name in `{token}`.")
            try:
                strength = int(strength_raw.strip())
            except ValueError:
                raise ValueError(
                    f"Invalid strength `{strength_raw.strip()}` for skill `{skill_name}`. Must be an integer 1-5."
                ) from None
            if not 1 <= strength <= 5:
                raise ValueError(
                    f"Strength `{strength}` for skill `{skill_name}` is out of range. Must be 1-5."
                )
            parsed[skill_name] = strength

        if not parsed:
            raise ValueError("No valid skills found in input.")
        return parsed

    def _format_requested_skills(
        self, requested_skills: list[str], contact: dict[str, Any]
    ) -> str:
        """Format requested skills with strength values from contact attributes."""
        if not requested_skills:
            return ""

        skill_attrs = self._parse_contact_skill_attrs(contact.get("cSkillAttrs"))
        if not skill_attrs:
            return ", ".join(requested_skills)

        rendered: list[str] = []
        for skill in requested_skills:
            normalized_skill = self._normalize_skill(skill)
            if not normalized_skill:
                continue

            strength = skill_attrs.get(normalized_skill.casefold())
            if strength is None:
                rendered.append(skill)
            else:
                rendered.append(f"{skill} ({strength})")

        return ", ".join(rendered)

    @app_commands.command(
        name="search-members", description="Search for candidates / members in the CRM"
    )
    @app_commands.describe(
        query="Search term (name, Discord username, email, or 508 email)",
        skills="Comma-separated skills (AND match)",
    )
    @require_role("Member")
    async def search_members(
        self,
        interaction: discord.Interaction,
        query: str | None = None,
        skills: str | None = None,
    ) -> None:
        """Search for contacts in the CRM."""
        try:
            await interaction.response.defer(ephemeral=True)

            query_value = (query or "").strip()
            raw_skills_list = (
                [skill.strip() for skill in skills.split(",") if skill.strip()]
                if skills
                else []
            )
            skills_list = normalize_skill_list(raw_skills_list)

            if not query_value and not skills_list:
                self._audit_command(
                    interaction=interaction,
                    action="crm.search_members",
                    result="denied",
                    metadata={"reason": "missing_query_and_skills"},
                )
                await interaction.followup.send(
                    "❌ Please provide a search term or skills to search by."
                )
                return

            search_parts = []
            if query_value:
                search_parts.append(f"`{query_value}`")
            if skills_list:
                search_parts.append(f"skills: `{', '.join(skills_list)}`")
            search_summary = ", ".join(search_parts)

            # Search contacts using EspoCRM API
            where_filters = []
            if query_value:
                where_filters.append(
                    {
                        "type": "or",
                        "value": [
                            {
                                "type": "contains",
                                "attribute": "name",
                                "value": query_value,
                            },
                            {
                                "type": "contains",
                                "attribute": "cDiscordUsername",
                                "value": query_value,
                            },
                            {
                                "type": "contains",
                                "attribute": "emailAddress",
                                "value": query_value,
                            },
                            {
                                "type": "contains",
                                "attribute": "c508Email",
                                "value": query_value,
                            },
                        ],
                    }
                )

            if skills_list:
                where_filters.append(
                    {
                        "type": "arrayAllOf",
                        "attribute": "skills",
                        "value": skills_list,
                    }
                )

            search_params = {
                "where": where_filters,
                "maxSize": 10,
                "select": "id,name,emailAddress,c508Email,cDiscordUsername,cDiscordUserID,phoneNumber,type,resumeIds,resumeNames,resumeTypes,skills,cSkillAttrs",
            }

            response = self.espo_api.request("GET", "Contact", search_params)
            contacts = response.get("list", [])

            if not contacts:
                self._audit_command(
                    interaction=interaction,
                    action="crm.search_members",
                    result="success",
                    metadata={
                        "query": query_value or None,
                        "skills": skills_list,
                        "contacts_found": 0,
                    },
                )
                await interaction.followup.send(
                    f"🔍 No contacts found for: {search_summary}"
                )
                return

            logger.info(f"Found {len(contacts)} contacts for: {search_summary}")

            # Create embed with results
            embed = discord.Embed(
                title="🔍 CRM Contact Search Results",
                description=f"Found {len(contacts)} contact(s) for: {search_summary}",
                color=0x0099FF,
            )

            # Create view with resume download buttons
            view = ResumeButtonView()

            for i, contact in enumerate(contacts):
                name = contact.get("name", "Unknown")
                email = contact.get("emailAddress", "No email")
                contact_type = contact.get("type", "Unknown")
                email_508 = contact.get("c508Email", "None")
                discord_username = contact.get("cDiscordUsername") or "No Discord"
                discord_user_id = contact.get("cDiscordUserID")
                contact_id = contact.get("id", "")

                # Create Discord display with @mention if user ID exists
                # Remove "(ID: ...)" from stored username for cleaner display
                clean_discord_username = discord_username
                if discord_username and " (ID: " in discord_username:
                    clean_discord_username = discord_username.split(" (ID: ")[0]

                discord_display = clean_discord_username
                if (
                    discord_user_id
                    and discord_user_id != "No Discord"
                    and interaction.guild
                ):
                    try:
                        # Try to get the Discord member for @mention
                        member = interaction.guild.get_member(int(discord_user_id))
                        if member:
                            discord_display = (
                                f"{member.mention} ({clean_discord_username})"
                            )
                    except (ValueError, AttributeError):
                        # If user ID is invalid or guild is None, just use username
                        pass

                contact_info = f"📧 {email}\n🏷️ Type: {contact_type}"

                # Only show 508 email and Discord for Candidates/Members
                if contact_type in ["Candidate / Member", "Member"]:
                    contact_info += (
                        f"\n🏢 508 Email: {email_508}\n💬 Discord: {discord_display}"
                    )

                if skills_list:
                    contact_skills = self._format_requested_skills(skills_list, contact)
                    if contact_skills:
                        contact_info += f"\n🧠 Skills: {contact_skills}"

                # Add clickable CRM link at the top of contact info
                if contact_id:
                    profile_url = f"{self.base_url}/#Contact/view/{contact_id}"
                    contact_info = f"🔗 [View in CRM]({profile_url})\n{contact_info}"

                embed.add_field(name=f"👤 {name}", value=contact_info, inline=True)

                # Check for resume data directly from search results
                resume_ids = contact.get("resumeIds", [])
                resume_names = contact.get("resumeNames", {})

                if resume_ids and len(resume_ids) > 0:
                    # Use the last resume ID (newest uploaded)
                    last_resume_id = resume_ids[-1]
                    resume_name = resume_names.get(last_resume_id, f"{name}_resume")
                    logger.info(
                        f"Found resume for {name}: {resume_name} (ID: {last_resume_id})"
                    )
                    view.add_resume_button(name, last_resume_id)
                else:
                    logger.info(f"No resumes found for {name}")

            # Send embed with view only if there are buttons
            if view.children:
                await interaction.followup.send(embed=embed, view=view)
            else:
                await interaction.followup.send(embed=embed)

            self._audit_command(
                interaction=interaction,
                action="crm.search_members",
                result="success",
                metadata={
                    "query": query_value or None,
                    "skills": skills_list,
                    "contacts_found": len(contacts),
                    "resume_button_count": len(view.children),
                },
            )

        except EspoAPIError as e:
            logger.error(f"EspoCRM API error: {e}")
            self._audit_command(
                interaction=interaction,
                action="crm.search_members",
                result="error",
                metadata={"error": str(e)},
            )
            await interaction.followup.send(f"❌ CRM API error: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error in CRM search: {e}")
            self._audit_command(
                interaction=interaction,
                action="crm.search_members",
                result="error",
                metadata={"error": str(e)},
            )
            await interaction.followup.send(
                "❌ An unexpected error occurred while searching the CRM."
            )

    @app_commands.command(
        name="crm-status", description="Check CRM API connection status"
    )
    async def crm_status(self, interaction: discord.Interaction) -> None:
        """Check if the CRM API is accessible."""
        try:
            await interaction.response.defer(ephemeral=True)

            # Try a simple API call to check connectivity
            response = self.espo_api.request("GET", "App/user")
            user_name = response.get("user", {}).get("name", "Unknown")

            embed = discord.Embed(
                title="✅ CRM Status",
                description="Connection to EspoCRM is working!",
                color=0x00FF00,
            )
            embed.add_field(name="Connected as", value=user_name, inline=True)
            embed.add_field(name="Base URL", value=settings.espo_base_url, inline=True)

            await interaction.followup.send(embed=embed)
            self._audit_command(
                interaction=interaction,
                action="crm.status",
                result="success",
                metadata={"connected_as": user_name},
            )

        except EspoAPIError as e:
            logger.error(f"EspoCRM API error: {e}")
            self._audit_command(
                interaction=interaction,
                action="crm.status",
                result="error",
                metadata={"error": str(e)},
            )
            embed = discord.Embed(
                title="❌ CRM Status",
                description=f"Failed to connect to EspoCRM: {str(e)}",
                color=0xFF0000,
            )
            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error(f"Unexpected error in CRM status: {e}")
            self._audit_command(
                interaction=interaction,
                action="crm.status",
                result="error",
                metadata={"error": str(e)},
            )
            embed = discord.Embed(
                title="❌ CRM Status",
                description="An unexpected error occurred while checking CRM status.",
                color=0xFF0000,
            )
            await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="get-resume", description="Download and send a contact's resume"
    )
    @app_commands.describe(
        query="Email address, 508 email (username, username@, or username@508.dev), or Discord username"
    )
    @require_role("Member")
    async def get_resume(self, interaction: discord.Interaction, query: str) -> None:
        """Download and send a contact's resume as a file attachment."""
        try:
            await interaction.response.defer(ephemeral=True)

            # Normalize the query - add @508.dev if it looks like a username or ends with @
            normalized_query = query
            if "@" not in query and not any(char in query for char in [" ", ".", "#"]):
                # Looks like a username, add @508.dev
                normalized_query = f"{query}@508.dev"
            elif query.endswith("@"):
                # Handle john@ -> john@508.dev
                normalized_query = f"{query}508.dev"

            # Search for the contact first
            search_params = {
                "where": [
                    {
                        "type": "or",
                        "value": [
                            {
                                "type": "contains",
                                "attribute": "emailAddress",
                                "value": normalized_query,
                            },
                            {
                                "type": "contains",
                                "attribute": "c508Email",
                                "value": normalized_query,
                            },
                            {
                                "type": "contains",
                                "attribute": "cDiscordUsername",
                                "value": query,  # Use original query for Discord username
                            },
                        ],
                    }
                ],
                "maxSize": 1,
                "select": "id,name,emailAddress,c508Email,cDiscordUsername,resumeIds,resumeNames,resumeTypes",
            }

            response = self.espo_api.request("GET", "Contact", search_params)
            contacts = response.get("list", [])

            if not contacts:
                self._audit_command(
                    interaction=interaction,
                    action="crm.get_resume",
                    result="success",
                    metadata={"query": query, "contact_found": False},
                )
                await interaction.followup.send(f"❌ No contact found for: `{query}`")
                return

            contact = contacts[0]
            contact_name = contact.get("name", "Unknown")

            # Get resume data directly from search results
            resume_ids = contact.get("resumeIds", [])
            resume_names = contact.get("resumeNames", {})

            if not resume_ids or len(resume_ids) == 0:
                self._audit_command(
                    interaction=interaction,
                    action="crm.get_resume",
                    result="success",
                    metadata={
                        "query": query,
                        "contact_found": True,
                        "has_resume": False,
                    },
                )
                await interaction.followup.send(
                    f"❌ No resume found for {contact_name}"
                )
                return

            # Use the last resume (newest uploaded)
            resume_id = resume_ids[-1]
            resume_name = resume_names.get(resume_id, f"{contact_name}_resume")

            logger.info(
                f"Downloading resume for {contact_name}: {resume_name} (ID: {resume_id})"
            )

            # Use shared download method
            download_ok = await self._download_and_send_resume(
                interaction, contact_name, resume_id
            )
            self._audit_command(
                interaction=interaction,
                action="crm.get_resume",
                result="success" if download_ok else "error",
                metadata={
                    "query": query,
                    "contact_found": True,
                    "has_resume": True,
                    "download_ok": download_ok,
                },
                resource_type="crm_contact",
                resource_id=str(contact.get("id", "")),
            )

        except EspoAPIError as e:
            logger.error(f"EspoCRM API error in get_resume: {e}")
            self._audit_command(
                interaction=interaction,
                action="crm.get_resume",
                result="error",
                metadata={"query": query, "error": str(e)},
            )
            await interaction.followup.send(f"❌ CRM API error: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error in get_resume: {e}")
            self._audit_command(
                interaction=interaction,
                action="crm.get_resume",
                result="error",
                metadata={"query": query, "error": str(e)},
            )
            await interaction.followup.send(
                "❌ An unexpected error occurred while fetching the resume."
            )

    def _is_hex_string(self, s: str) -> bool:
        """Check if string looks like a hex contact ID."""
        return len(s) >= 15 and all(c in "0123456789abcdefABCDEF" for c in s)

    async def _search_contact_for_linking(
        self, search_term: str
    ) -> list[dict[str, Any]]:
        """Search for contacts using multiple criteria."""
        # Check if it looks like a hex contact ID
        if self._is_hex_string(search_term):
            try:
                response = self.espo_api.request("GET", f"Contact/{search_term}")
                if response and response.get("id"):
                    return [response]
            except EspoAPIError:
                pass  # If direct ID lookup fails, fall through to regular search

        # Determine if this is an email search vs name search
        is_email = "@" in search_term
        has_space = " " in search_term

        # For email searches or full names (with space), auto-select if single result
        # For names without space, always show choices
        should_auto_select = is_email or has_space

        if is_email:
            # Email search - check both email fields
            normalized_email = search_term
            if "@" not in search_term.split("@")[-1]:  # Handle incomplete emails
                if search_term.endswith("@"):
                    normalized_email = f"{search_term}508.dev"
                elif "@" not in search_term:
                    normalized_email = f"{search_term}@508.dev"

            search_params = {
                "where": [
                    {
                        "type": "or",
                        "value": [
                            {
                                "type": "equals",
                                "attribute": "emailAddress",
                                "value": normalized_email,
                            },
                            {
                                "type": "equals",
                                "attribute": "c508Email",
                                "value": normalized_email,
                            },
                        ],
                    }
                ],
                "maxSize": 10,
                "select": "id,name,emailAddress,c508Email,cDiscordUsername",
            }
        else:
            # Name search
            search_params = {
                "where": [
                    {"type": "contains", "attribute": "name", "value": search_term}
                ],
                "maxSize": 10 if not should_auto_select else 1,
                "select": "id,name,emailAddress,c508Email,cDiscordUsername",
            }

        response = self.espo_api.request("GET", "Contact", search_params)
        contacts: list[dict[str, Any]] = response.get("list", [])

        # Deduplicate contacts by ID to avoid showing duplicates
        seen_ids = set()
        deduplicated_contacts = []
        for contact in contacts:
            contact_id = contact.get("id")
            if contact_id and contact_id not in seen_ids:
                seen_ids.add(contact_id)
                deduplicated_contacts.append(contact)

        # For email or full name searches, auto-select if exactly one result
        if should_auto_select and len(deduplicated_contacts) > 1:
            # Multiple results for email/full name - still show choices
            pass

        return deduplicated_contacts

    def _extract_resume_contact_hints(
        self, file_content: bytes
    ) -> dict[str, list[str]]:
        """Extract basic contact-identifying signals from resume bytes."""
        text = file_content.decode("utf-8", errors="ignore")
        if not text:
            return {"emails": [], "github_usernames": [], "linkedin_urls": []}

        # Keep this lightweight; heuristics are only used for contact targeting.
        snippet = text[:12000]
        email_re = re.compile(
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
            flags=re.IGNORECASE,
        )
        github_re = re.compile(
            r"(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9-]{1,39})",
            flags=re.IGNORECASE,
        )
        linkedin_re = re.compile(
            r"(?:https?://)?(?:[\w.-]+\.)?linkedin\.com/in/[A-Za-z0-9\\-_%]+/?",
            flags=re.IGNORECASE,
        )

        email_matches: list[str] = []
        for email in email_re.findall(snippet):
            candidate = str(email).strip().lower()
            if candidate and candidate not in email_matches:
                email_matches.append(candidate)

        github_matches: list[str] = []
        for username in github_re.findall(snippet):
            candidate = str(username).strip().lower()
            if candidate and candidate not in github_matches:
                github_matches.append(candidate)

        linkedin_matches: list[str] = []
        for linkedin_url in linkedin_re.findall(snippet):
            candidate = str(linkedin_url).strip().lower().rstrip("/")
            if candidate and candidate not in linkedin_matches:
                linkedin_matches.append(candidate)

        return {
            "emails": email_matches,
            "github_usernames": github_matches,
            "linkedin_urls": linkedin_matches,
        }

    def _extract_resume_name_hint(self, file_content: bytes) -> str:
        """Best-effort contact name extraction from resume text."""
        text = file_content.decode("utf-8", errors="ignore")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in lines[:40]:
            candidate = line.strip()
            if not candidate:
                continue
            if len(candidate) < 2:
                continue
            if "@" in candidate or "http" in candidate.lower():
                continue
            if not any(char.isalpha() for char in candidate):
                continue
            # Prefer short, title-like lines at the top as candidate names.
            if len(candidate.split()) >= 1 and len(candidate) <= 70:
                return candidate

        return "Unknown Contact"

    def _build_resume_create_contact_payload(
        self, file_content: bytes
    ) -> dict[str, str]:
        """Build a minimal contact create payload from resume hints."""
        hints = self._extract_resume_contact_hints(file_content)
        name = self._extract_resume_name_hint(file_content)

        payload: dict[str, str] = {"name": name}
        if hints["emails"]:
            payload["emailAddress"] = hints["emails"][0]
            payload["c508Email"] = hints["emails"][0]
        if hints["github_usernames"]:
            payload["cGitHubUsername"] = hints["github_usernames"][0]
        if hints["linkedin_urls"]:
            payload["cLinkedInUrl"] = hints["linkedin_urls"][0]

        return payload

    async def _search_contacts_by_field(
        self, *, field: str, value: str, max_size: int = 10
    ) -> list[dict[str, Any]]:
        """Search contacts using an exact field equals match."""
        search_params = {
            "where": [{"type": "equals", "attribute": field, "value": value}],
            "maxSize": max_size,
            "select": "id,name,emailAddress,c508Email,cDiscordUsername,cGitHubUsername,cLinkedInUrl",
        }

        response = self.espo_api.request("GET", "Contact", search_params)
        contacts: list[dict[str, Any]] = response.get("list", [])

        deduplicated_contacts: list[dict[str, Any]] = []
        seen_ids = set()
        for contact in contacts:
            contact_id = contact.get("id")
            if contact_id and contact_id not in seen_ids:
                seen_ids.add(contact_id)
                deduplicated_contacts.append(contact)

        return deduplicated_contacts

    async def _infer_contact_from_resume(
        self, file_content: bytes
    ) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
        """Infer target contact from resume identifiers."""
        hints = self._extract_resume_contact_hints(file_content)
        for email in hints["emails"]:
            contacts = await self._search_contact_for_linking(email)
            if len(contacts) == 1:
                return contacts[0], {"method": "email", "value": email}
            if len(contacts) > 1:
                return None, {
                    "method": "email",
                    "value": email,
                    "reason": "multiple_matches",
                }

        for github_username in hints["github_usernames"]:
            contacts = await self._search_contacts_by_field(
                field="cGitHubUsername", value=github_username
            )
            if len(contacts) == 1:
                return contacts[0], {"method": "github", "value": github_username}
            if len(contacts) > 1:
                return None, {
                    "method": "github",
                    "value": github_username,
                    "reason": "multiple_matches",
                }

        for linkedin_url in hints["linkedin_urls"]:
            contacts = await self._search_contacts_by_field(
                field="cLinkedInUrl", value=linkedin_url
            )
            if len(contacts) == 1:
                return contacts[0], {"method": "linkedin", "value": linkedin_url}
            if len(contacts) > 1:
                return None, {
                    "method": "linkedin",
                    "value": linkedin_url,
                    "reason": "multiple_matches",
                }

        return None, {"reason": "no_matching_contact"}

    async def _perform_discord_linking(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        contact: dict[str, Any],
    ) -> bool:
        """Shared method to perform Discord user linking to a contact."""
        try:
            contact_id = contact.get("id")
            contact_name = contact.get("name", "Unknown")

            # Prepare the Discord username for storage (without ID) and display
            if hasattr(user, "discriminator") and user.discriminator != "0":
                discord_display = f"{user.name}#{user.discriminator}"
            else:
                discord_display = f"{user.name}"

            # Update the contact's Discord username and user ID
            update_data = {
                "cDiscordUsername": discord_display,
                "cDiscordUserID": str(user.id),
            }

            update_response = self.espo_api.request(
                "PUT", f"Contact/{contact_id}", update_data
            )

            if update_response:
                # Create success embed
                embed = discord.Embed(
                    title="✅ Discord User Linked",
                    description="Successfully linked Discord user to CRM contact (updated username and user ID)",
                    color=0x00FF00,
                )
                embed.add_field(
                    name="👤 Contact", value=f"{contact_name}", inline=False
                )
                embed.add_field(
                    name="📧 Email",
                    value=f"{contact.get('c508Email') or contact.get('emailAddress', 'N/A')}",
                    inline=True,
                )
                embed.add_field(
                    name="💬 Discord User",
                    value=f"{user.mention} ({discord_display})",
                    inline=True,
                )
                # Add CRM link
                if contact_id:
                    profile_url = f"{self.base_url}/#Contact/view/{contact_id}"
                    embed.add_field(
                        name="🔗 CRM Profile",
                        value=f"[View in CRM]({profile_url})",
                        inline=True,
                    )

                await interaction.followup.send(embed=embed)

                logger.info(
                    f"Discord user {user.name} (ID: {user.id}) linked to CRM contact "
                    f"{contact_name} (ID: {contact_id}) by {interaction.user.name}"
                )
                self._audit_command(
                    interaction=interaction,
                    action="crm.link_discord_user.execute",
                    result="success",
                    metadata={
                        "linked_user_id": str(user.id),
                        "linked_username": user.name,
                        "contact_name": contact_name,
                    },
                    resource_type="crm_contact",
                    resource_id=str(contact_id),
                )
                return True
            else:
                self._audit_command(
                    interaction=interaction,
                    action="crm.link_discord_user.execute",
                    result="error",
                    metadata={
                        "linked_user_id": str(user.id),
                        "contact_name": contact_name,
                        "error": "crm_update_failed",
                    },
                    resource_type="crm_contact",
                    resource_id=str(contact_id),
                )
                await interaction.followup.send(
                    "❌ Failed to update contact in CRM. Please try again."
                )
                return False

        except EspoAPIError as e:
            logger.error(f"EspoCRM API error in _perform_discord_linking: {e}")
            self._audit_command(
                interaction=interaction,
                action="crm.link_discord_user.execute",
                result="error",
                metadata={"linked_user_id": str(user.id), "error": str(e)},
            )
            await interaction.followup.send(f"❌ CRM API error: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error in _perform_discord_linking: {e}")
            self._audit_command(
                interaction=interaction,
                action="crm.link_discord_user.execute",
                result="error",
                metadata={"linked_user_id": str(user.id), "error": str(e)},
            )
            await interaction.followup.send(
                "❌ An unexpected error occurred while linking the user."
            )
            return False

    async def _show_contact_choices(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        search_term: str,
        contacts: list[dict[str, Any]],
    ) -> None:
        """Show contact choices when multiple results found."""
        embed = discord.Embed(
            title="🔍 Multiple Contacts Found",
            description=f"Found {len(contacts)} contacts for `{search_term}`. Click a button below to link the Discord user.",
            color=0xFFA500,
        )

        # Create view with contact selection buttons
        view = ContactSelectionView(user, search_term)

        for i, contact in enumerate(contacts[:5], 1):  # Show max 5
            name = contact.get("name", "Unknown")
            email = contact.get("emailAddress", "No email")
            email_508 = contact.get("c508Email", "No 508 email")
            contact_id = contact.get("id", "")

            contact_info = (
                f"📧 {email}\n🏢 508 Email: {email_508}\n🆔 ID: `{contact_id}`"
            )
            embed.add_field(name=f"{i}. {name}", value=contact_info, inline=True)

            # Add button for this contact
            view.add_contact_button(contact)

        embed.add_field(
            name="💡 Tip",
            value="Click the button for the contact you want to link, or use the contact ID for exact matching.",
            inline=False,
        )

        await interaction.followup.send(embed=embed, view=view)

    def _normalize_508_username(self, value: str | None) -> str | None:
        """Normalize a 508 username candidate."""
        if not value:
            return None

        normalized = value.strip()
        if not normalized:
            return None

        normalized = normalized.lstrip("@").strip()
        normalized = " ".join(normalized.split())
        if not normalized:
            return None

        if "@" in normalized:
            username, _, domain = normalized.partition("@")
            if not username:
                return None
            if domain.lower() in {"", "508", "508.dev"}:
                return username.lower()
            return username.lower()

        return normalized.lower()

    async def _resolve_verified_by(
        self, interaction: discord.Interaction, verified_by: str
    ) -> str | None:
        """Resolve verifier.

        If a value is provided:
            - If it looks like a Discord mention, try to resolve from CRM using the
              linked Discord user ID.
            - Otherwise normalize directly as a 508 username.

        If no value is provided, resolve the invoker from CRM first by Discord ID, then by
        Discord username.
        """
        if verified_by.strip():
            match = re.match(r"^<@!?(\d+)>$", verified_by.strip())
            if match and interaction.guild:
                member = interaction.guild.get_member(int(match.group(1)))
                if member:
                    contact = await self._find_contact_by_discord_id(str(member.id))
                    if contact:
                        candidate = self._normalize_508_username(
                            contact.get("c508Email") or ""
                        )
                        if candidate:
                            return candidate
                    return self._normalize_508_username(member.name)

            return self._normalize_508_username(verified_by)

        return await self._resolve_verified_by_from_interaction_user(interaction)

    async def _resolve_verified_by_from_interaction_user(
        self, interaction: discord.Interaction
    ) -> str | None:
        """Resolve verifier using the invoking Discord user."""
        user = interaction.user
        if not user:
            return None

        if getattr(user, "id", None):
            contact = await self._find_contact_by_discord_id(str(user.id))
            if contact:
                candidate = self._normalize_508_username(contact.get("c508Email") or "")
                if candidate:
                    return candidate

        discord_username = self._normalize_508_username(str(user.name))
        if discord_username:
            contact = await self._find_contact_by_discord_username(discord_username)
            if contact:
                candidate = self._normalize_508_username(contact.get("c508Email") or "")
                if candidate:
                    return candidate
            return discord_username

        return None

    async def _find_contact_by_discord_username(
        self, discord_username: str
    ) -> dict[str, Any] | None:
        """Find a contact by Discord username."""
        search_params = {
            "where": [
                {
                    "type": "equals",
                    "attribute": "cDiscordUsername",
                    "value": discord_username,
                }
            ],
            "maxSize": 1,
            "select": "id,name,emailAddress,c508Email,cDiscordUsername,cDiscordUserID",
        }

        response = self.espo_api.request("GET", "Contact", search_params)
        contacts = response.get("list", [])
        return contacts[0] if contacts else None

    async def _parse_verified_at(self, raw_verified_at: str | None) -> str:
        """Parse an ID verification date or default to today."""
        if not raw_verified_at or not raw_verified_at.strip():
            return date.today().isoformat()

        value = raw_verified_at.strip()
        normalized = " ".join(value.replace(",", " ").split())

        for fmt in (
            "%B %d %Y",
            "%b %d %Y",
            "%d %B %Y",
            "%d %b %Y",
        ):
            try:
                return datetime.strptime(normalized, fmt).date().isoformat()
            except ValueError:
                continue

        normalized_numeric = re.sub(r"[./\s]+", "-", value)
        for fmt in (
            "%Y-%m-%d",
            "%d-%m-%Y",
            "%d-%m-%y",
            "%m-%d-%Y",
            "%m-%d-%y",
        ):
            try:
                return datetime.strptime(normalized_numeric, fmt).date().isoformat()
            except ValueError:
                continue

        raise ValueError(f"Invalid verified_at format: '{raw_verified_at}'.")

    async def _search_contacts_for_mark_id_verification(
        self, search_term: str
    ) -> list[dict[str, Any]]:
        """Search for contacts for ID verification."""
        contacts = await self._search_contact_for_linking(search_term)
        if contacts:
            return contacts

        if "@" not in search_term and " " not in search_term:
            contacts = await self._search_contact_for_linking(
                f"{search_term.strip()}@508.dev"
            )
            if contacts:
                return contacts

        return contacts

    async def _mark_id_verified_for_contact(
        self,
        interaction: discord.Interaction,
        contact: dict[str, Any],
        verified_by: str | None,
        verified_at: str,
    ) -> bool:
        """Persist ID verification metadata to CRM."""
        contact_id = contact.get("id")
        contact_name = contact.get("name", "Unknown")

        if not contact_id:
            self._audit_command(
                interaction=interaction,
                action="crm.mark_id_verified",
                result="error",
                metadata={
                    "verified_by": verified_by,
                    "verified_at": verified_at,
                    "error": "contact_id_missing",
                },
            )
            await interaction.followup.send("❌ Contact ID not found.")
            return False

        payload = {ID_VERIFIED_FIELD: True, ID_VERIFIED_AT_FIELD: verified_at}
        if verified_by:
            payload[ID_VERIFIED_BY_FIELD] = verified_by

        try:
            update_response = self.espo_api.request(
                "PUT", f"Contact/{contact_id}", payload
            )
            if update_response:
                embed = discord.Embed(
                    title="✅ ID Verified",
                    description=f"Marked **{contact_name}** as ID verified.",
                    color=0x00FF00,
                )
                embed.add_field(name="📅 Verified at", value=verified_at, inline=True)
                if verified_by:
                    embed.add_field(
                        name="✅ Verified by", value=verified_by, inline=True
                    )
                profile_url = f"{self.base_url}/#Contact/view/{contact_id}"
                embed.add_field(
                    name="🔗 CRM Profile",
                    value=f"[View in CRM]({profile_url})",
                    inline=True,
                )
                await interaction.followup.send(embed=embed)

                self._audit_command(
                    interaction=interaction,
                    action="crm.mark_id_verified",
                    result="success",
                    metadata={
                        "contact_id": str(contact_id),
                        "verified_by": verified_by,
                        "verified_at": verified_at,
                    },
                    resource_type="crm_contact",
                    resource_id=str(contact_id),
                )
                return True

            self._audit_command(
                interaction=interaction,
                action="crm.mark_id_verified",
                result="error",
                metadata={
                    "contact_id": str(contact_id),
                    "verified_by": verified_by,
                    "verified_at": verified_at,
                    "error": "crm_update_failed",
                },
                resource_type="crm_contact",
                resource_id=str(contact_id),
            )
            await interaction.followup.send(
                "❌ Failed to update contact in CRM. Please try again."
            )
            return False

        except EspoAPIError as exc:
            logger.error(f"Failed to update contact ID verification: {exc}")
            self._audit_command(
                interaction=interaction,
                action="crm.mark_id_verified",
                result="error",
                metadata={
                    "contact_id": str(contact_id),
                    "verified_by": verified_by,
                    "verified_at": verified_at,
                    "error": str(exc),
                },
                resource_type="crm_contact",
                resource_id=str(contact_id),
            )
            await interaction.followup.send(f"❌ CRM API error: {str(exc)}")
            return False
        except Exception as exc:
            logger.error(f"Unexpected error marking ID verification: {exc}")
            self._audit_command(
                interaction=interaction,
                action="crm.mark_id_verified",
                result="error",
                metadata={
                    "contact_id": str(contact_id),
                    "verified_by": verified_by,
                    "verified_at": verified_at,
                    "error": str(exc),
                },
                resource_type="crm_contact",
                resource_id=str(contact_id),
            )
            await interaction.followup.send(
                "❌ An unexpected error occurred while marking ID verification."
            )
            return False

    async def _show_mark_id_verified_contact_choices(
        self,
        interaction: discord.Interaction,
        search_term: str,
        contacts: list[dict[str, Any]],
        verified_by: str | None,
        verified_at: str,
    ) -> None:
        """Show contact choices when multiple candidates are found."""
        embed = discord.Embed(
            title="🔍 Multiple Contacts Found",
            description=(
                f"Found {len(contacts)} contacts for `{search_term}`. "
                "Select the correct person to mark as ID verified."
            ),
            color=0xFFA500,
        )

        view = MarkIdVerifiedSelectionView(
            crm_cog=self,
            requester_id=interaction.user.id,
            verified_by=verified_by,
            verified_at=verified_at,
        )

        for i, contact in enumerate(contacts[:5], 1):
            name = contact.get("name", "Unknown")
            email = contact.get("emailAddress", "No email")
            email_508 = contact.get("c508Email", "No 508 email")
            contact_id = contact.get("id", "")
            contact_info = (
                f"📧 {email}\n🏢 508 Email: {email_508}\n🆔 ID: `{contact_id}`"
            )
            embed.add_field(name=f"{i}. {name}", value=contact_info, inline=True)
            view.add_contact_button(contact)

        embed.add_field(
            name="💡 Tip",
            value="Select the contact button to continue, or rerun with a more specific term.",
            inline=False,
        )

        await interaction.followup.send(embed=embed, view=view)

    @app_commands.command(
        name="mark-id-verified",
        description="Mark a contact as ID verified (Admin only).",
    )
    @app_commands.describe(
        search_term="Email, 508 username, or name.",
        verified_by=(
            "Verifier 508 username or @Discord mention. "
            "Defaults to the command invoker if not provided."
        ),
        verified_at=(
            "Date verified (e.g. YYYY-MM-DD, DD/MM/YYYY, March 5, 2026). "
            "Defaults to today."
        ),
    )
    @require_role("Admin")
    async def mark_id_verified(
        self,
        interaction: discord.Interaction,
        search_term: str,
        verified_by: str | None = None,
        verified_at: str | None = None,
    ) -> None:
        """Mark a contact as ID verified."""
        try:
            await interaction.response.defer(ephemeral=True)

            resolved_verified_by = await self._resolve_verified_by(
                interaction, verified_by or ""
            )
            if verified_by is None and not resolved_verified_by:
                resolved_verified_by = self._normalize_508_username(
                    str(interaction.user.name)
                )
            resolved_verified_at = await self._parse_verified_at(verified_at)

            contacts = await self._search_contacts_for_mark_id_verification(search_term)

            if not contacts:
                self._audit_command(
                    interaction=interaction,
                    action="crm.mark_id_verified",
                    result="success",
                    metadata={
                        "search_term": search_term,
                        "verified_by": resolved_verified_by,
                        "verified_at": resolved_verified_at,
                        "contacts_found": 0,
                    },
                )
                await interaction.followup.send(
                    f"❌ No contact found for: `{search_term}`"
                )
                return

            if len(contacts) > 1:
                self._audit_command(
                    interaction=interaction,
                    action="crm.mark_id_verified",
                    result="success",
                    metadata={
                        "search_term": search_term,
                        "verified_by": resolved_verified_by,
                        "verified_at": resolved_verified_at,
                        "contacts_found": len(contacts),
                        "requires_selection": True,
                    },
                )
                await self._show_mark_id_verified_contact_choices(
                    interaction=interaction,
                    search_term=search_term,
                    contacts=contacts,
                    verified_by=resolved_verified_by,
                    verified_at=resolved_verified_at,
                )
                return

            target_contact = contacts[0]
            self._audit_command(
                interaction=interaction,
                action="crm.mark_id_verified",
                result="success",
                metadata={
                    "search_term": search_term,
                    "verified_by": resolved_verified_by,
                    "verified_at": resolved_verified_at,
                    "contacts_found": 1,
                },
                resource_type="crm_contact",
                resource_id=str(target_contact.get("id")),
            )
            await self._mark_id_verified_for_contact(
                interaction=interaction,
                contact=target_contact,
                verified_by=resolved_verified_by,
                verified_at=resolved_verified_at,
            )
        except ValueError as exc:
            logger.error(f"Invalid verified_at value: {exc}")
            self._audit_command(
                interaction=interaction,
                action="crm.mark_id_verified",
                result="denied",
                metadata={"verified_at": verified_at, "reason": str(exc)},
            )
            await interaction.followup.send(f"❌ {exc}")
        except Exception as exc:
            logger.error(f"Unexpected error in mark_id_verified: {exc}")
            self._audit_command(
                interaction=interaction,
                action="crm.mark_id_verified",
                result="error",
                metadata={"search_term": search_term, "error": str(exc)},
            )
            await interaction.followup.send(
                "❌ An unexpected error occurred while marking ID verification."
            )

    @app_commands.command(
        name="link-discord-user",
        description="Link a Discord user to a CRM contact (Steering Committee+ only)",
    )
    @app_commands.describe(
        user="Discord user to link (mention them)",
        search_term="Email, 508 email, name, or contact ID to find the contact",
    )
    @require_role("Steering Committee")
    async def link_discord_user(
        self, interaction: discord.Interaction, user: discord.Member, search_term: str
    ) -> None:
        """Link a Discord user to a CRM contact by updating the contact's Discord username."""
        try:
            await interaction.response.defer(ephemeral=True)

            # Determine search strategy based on search_term format
            contacts = await self._search_contact_for_linking(search_term)

            if not contacts:
                self._audit_command(
                    interaction=interaction,
                    action="crm.link_discord_user",
                    result="success",
                    metadata={
                        "search_term": search_term,
                        "linked_user_id": str(user.id),
                        "contacts_found": 0,
                    },
                )
                await interaction.followup.send(
                    f"❌ No contact found for: `{search_term}`"
                )
                return

            # Handle multiple results - show choices
            if len(contacts) > 1:
                self._audit_command(
                    interaction=interaction,
                    action="crm.link_discord_user",
                    result="success",
                    metadata={
                        "search_term": search_term,
                        "linked_user_id": str(user.id),
                        "contacts_found": len(contacts),
                        "requires_selection": True,
                    },
                )
                await self._show_contact_choices(
                    interaction, user, search_term, contacts
                )
                return

            # Single result - proceed with linking
            contact = contacts[0]
            self._audit_command(
                interaction=interaction,
                action="crm.link_discord_user",
                result="success",
                metadata={
                    "search_term": search_term,
                    "linked_user_id": str(user.id),
                    "contacts_found": 1,
                    "requires_selection": False,
                },
                resource_type="crm_contact",
                resource_id=str(contact.get("id", "")),
            )
            await self._perform_discord_linking(interaction, user, contact)

        except EspoAPIError as e:
            logger.error(f"EspoCRM API error in link_discord_user: {e}")
            self._audit_command(
                interaction=interaction,
                action="crm.link_discord_user",
                result="error",
                metadata={
                    "search_term": search_term,
                    "linked_user_id": str(user.id),
                    "error": str(e),
                },
            )
            await interaction.followup.send(f"❌ CRM API error: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error in link_discord_user: {e}")
            self._audit_command(
                interaction=interaction,
                action="crm.link_discord_user",
                result="error",
                metadata={
                    "search_term": search_term,
                    "linked_user_id": str(user.id),
                    "error": str(e),
                },
            )
            await interaction.followup.send(
                "❌ An unexpected error occurred while linking the Discord user."
            )

    @app_commands.command(
        name="unlinked-discord-users",
        description="List Discord users with Member role who aren't linked in CRM (Steering Committee+ only)",
    )
    @require_role("Steering Committee")
    async def unlinked_discord_users(self, interaction: discord.Interaction) -> None:
        """Find Discord users with Member role who don't have CRM links."""
        try:
            await interaction.response.defer(ephemeral=True)

            if not interaction.guild:
                self._audit_command(
                    interaction=interaction,
                    action="crm.unlinked_discord_users",
                    result="denied",
                    metadata={"reason": "not_in_guild"},
                )
                await interaction.followup.send(
                    "❌ This command can only be used in a server."
                )
                return

            # Get all contacts that have Discord user IDs set
            linked_user_ids = await self._get_linked_discord_user_ids()

            # Get all guild members with Member role
            member_role_users = []
            for member in interaction.guild.members:
                if not member.bot and hasattr(member, "roles"):
                    if check_user_roles_with_hierarchy(member.roles, ["Member"]):
                        member_role_users.append(member)

            # Find unlinked users
            unlinked_users = []
            for member in member_role_users:
                if str(member.id) not in linked_user_ids:
                    unlinked_users.append(member)

            if not unlinked_users:
                self._audit_command(
                    interaction=interaction,
                    action="crm.unlinked_discord_users",
                    result="success",
                    metadata={"unlinked_count": 0},
                )
                await interaction.followup.send(
                    "✅ **All Members Linked**\nAll Discord users with Member role are linked in the CRM!"
                )
                return

            # Send list of unlinked users
            await self._send_unlinked_users_list(interaction, unlinked_users)
            self._audit_command(
                interaction=interaction,
                action="crm.unlinked_discord_users",
                result="success",
                metadata={"unlinked_count": len(unlinked_users)},
            )

        except EspoAPIError as e:
            logger.error(f"EspoCRM API error in unlinked_discord_users: {e}")
            self._audit_command(
                interaction=interaction,
                action="crm.unlinked_discord_users",
                result="error",
                metadata={"error": str(e)},
            )
            await interaction.followup.send(f"❌ CRM API error: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error in unlinked_discord_users: {e}")
            self._audit_command(
                interaction=interaction,
                action="crm.unlinked_discord_users",
                result="error",
                metadata={"error": str(e)},
            )
            await interaction.followup.send(
                "❌ An unexpected error occurred while checking unlinked users."
            )

    async def _get_linked_discord_user_ids(self) -> set[str]:
        """Get all Discord user IDs that are linked in the CRM."""
        search_params = {
            "where": [
                {
                    "type": "isNotNull",
                    "attribute": "cDiscordUserID",
                }
            ],
            "maxSize": 200,  # Adjust based on your needs
            "select": "cDiscordUserID",
        }

        response = self.espo_api.request("GET", "Contact", search_params)
        contacts = response.get("list", [])

        linked_ids = set()
        for contact in contacts:
            discord_id = contact.get("cDiscordUserID")
            if discord_id and discord_id != "No Discord":
                linked_ids.add(discord_id)

        return linked_ids

    async def _send_unlinked_users_list(
        self, interaction: discord.Interaction, unlinked_users: list[discord.Member]
    ) -> None:
        """Send list of unlinked users as simple text."""
        # Create simple text list with mentions and display names
        user_lines = []
        for member in unlinked_users:
            user_lines.append(f"{member.mention} ({member.display_name})")

        # Create message content
        header = f"🔗 **Unlinked Discord Users ({len(unlinked_users)})**\n"
        user_list = "\n".join(user_lines)
        footer = "\n\n💡 Use `/link-discord-user @user <email/name/id>` to link these users to CRM contacts."

        message = header + user_list + footer

        # Discord message limit is 2000 characters, split if needed
        if len(message) <= 2000:
            await interaction.followup.send(message)
        else:
            # Split into multiple messages if too long
            messages = []
            current_message = header

            for user_line in user_lines:
                test_message = current_message + user_line + "\n"
                if len(test_message + footer) <= 2000:
                    current_message = test_message
                else:
                    # Send current message and start new one
                    messages.append(current_message.rstrip())
                    current_message = user_line + "\n"

            # Add the last message with footer
            if current_message.strip():
                messages.append(current_message.rstrip() + footer)

            # Send all messages
            for message in messages:
                await interaction.followup.send(message)

    async def _find_contact_by_discord_id(
        self, discord_user_id: str
    ) -> dict[str, Any] | None:
        """Find a contact by Discord user ID."""
        search_params = {
            "where": [
                {
                    "type": "equals",
                    "attribute": "cDiscordUserID",
                    "value": discord_user_id,
                }
            ],
            "maxSize": 1,
            "select": "id,name,emailAddress,c508Email,cDiscordUsername,cGitHubUsername",
        }

        response = self.espo_api.request("GET", "Contact", search_params)
        contacts = response.get("list", [])
        return contacts[0] if contacts else None

    def _extract_discord_id_from_mention(self, value: str) -> str | None:
        """Extract Discord user ID from @mention syntax."""
        match = re.fullmatch(r"<@!?(\d+)>", value.strip())
        if not match:
            return None
        return match.group(1)

    def _parse_json_object_with_recovery(self, raw: str) -> dict[str, Any] | None:
        """Parse JSON object with lightweight recovery for common malformed payloads."""

        def _load_json_object(candidate: str) -> dict[str, Any] | None:
            try:
                parsed = json.loads(candidate)
            except Exception:
                return None
            if isinstance(parsed, dict):
                return parsed
            return None

        text = raw.strip()
        if not text:
            return None

        attempts: list[str] = []
        attempts.append(text)

        # Keep only the outer object if prefix/suffix noise exists.
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            attempts.append(text[start : end + 1])

        normalized_quotes = (
            text.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")
        )
        attempts.append(normalized_quotes)

        attempts_with_trimmed_commas = [
            re.sub(r",\s*([}\]])", r"\1", candidate) for candidate in attempts
        ]
        attempts.extend(attempts_with_trimmed_commas)

        for candidate in attempts:
            parsed = _load_json_object(candidate)
            if parsed is not None:
                return parsed

        for candidate in attempts:
            try:
                parsed = ast.literal_eval(candidate)
            except Exception:
                continue
            if isinstance(parsed, dict):
                return parsed

        return None

    def _extract_contact_skills_for_view(
        self, contact: dict[str, Any]
    ) -> tuple[list[tuple[str, int | None]], str]:
        """Return display skills with source priority: structured attrs then multi-enum."""
        parsed_attrs = self._parse_contact_skill_attrs(contact.get("cSkillAttrs"))
        if parsed_attrs:
            ordered = sorted(
                parsed_attrs.items(),
                key=lambda item: (-item[1], item[0].casefold()),
            )
            return [(skill, strength) for skill, strength in ordered], "cSkillAttrs"

        raw_skills = contact.get("skills")
        if isinstance(raw_skills, list):
            skills = normalize_skill_list(
                [str(item) for item in raw_skills if str(item).strip()]
            )
        else:
            skills = normalize_skill_list(
                [
                    item.strip()
                    for item in str(raw_skills or "").split(",")
                    if item.strip()
                ]
            )
        return [(skill, None) for skill in skills], "skills"

    async def _search_contacts_for_view_skills(
        self, search_term: str
    ) -> list[dict[str, Any]]:
        """Resolve search term for view-skills lookup."""
        mention_user_id = self._extract_discord_id_from_mention(search_term)
        if mention_user_id:
            by_discord_id = await self._find_contact_by_discord_id(mention_user_id)
            return [by_discord_id] if by_discord_id else []

        contacts = await self._search_contact_for_linking(search_term)
        if contacts:
            return contacts

        # `john` fallback -> `john@508.dev`
        if (
            "@" not in search_term
            and " " not in search_term
            and not self._is_hex_string(search_term)
        ):
            fallback_term = f"{search_term}@508.dev"
            contacts = await self._search_contact_for_linking(fallback_term)
        return contacts

    @app_commands.command(
        name="view-skills",
        description="View CRM skills for yourself or a specific member",
    )
    @app_commands.describe(
        search_term="Optional: @mention, email, 508 username, name, or contact ID",
    )
    @require_role("Member")
    async def view_skills(
        self, interaction: discord.Interaction, search_term: str | None = None
    ) -> None:
        """View structured skills (with strengths) or fallback multi-enum skills."""
        try:
            await interaction.response.defer(ephemeral=True)

            query = (search_term or "").strip()
            target_scope = "other" if query else "self"

            target_contact: dict[str, Any] | None = None
            if not query:
                target_contact = await self._find_contact_by_discord_id(
                    str(interaction.user.id)
                )
                if not target_contact:
                    self._audit_command(
                        interaction=interaction,
                        action="crm.view_skills",
                        result="denied",
                        metadata={
                            "target_scope": "self",
                            "reason": "discord_not_linked",
                        },
                    )
                    await interaction.followup.send(
                        "❌ Your Discord account is not linked to a CRM contact. "
                        "Please ask a Steering Committee member to link your account first."
                    )
                    return
            else:
                contacts = await self._search_contacts_for_view_skills(query)
                if not contacts:
                    self._audit_command(
                        interaction=interaction,
                        action="crm.view_skills",
                        result="success",
                        metadata={
                            "search_term": query,
                            "target_scope": target_scope,
                            "contacts_found": 0,
                        },
                    )
                    await interaction.followup.send(
                        f"❌ No contact found for: `{query}`"
                    )
                    return

                if len(contacts) > 1:
                    lines: list[str] = []
                    for contact in contacts[:5]:
                        name = str(contact.get("name", "Unknown"))
                        contact_id = str(contact.get("id", ""))
                        lines.append(f"- **{name}** (`{contact_id}`)")
                    suffix = (
                        f"\n...and {len(contacts) - 5} more."
                        if len(contacts) > 5
                        else ""
                    )
                    self._audit_command(
                        interaction=interaction,
                        action="crm.view_skills",
                        result="success",
                        metadata={
                            "search_term": query,
                            "target_scope": target_scope,
                            "contacts_found": len(contacts),
                            "requires_selection": True,
                        },
                    )
                    await interaction.followup.send(
                        "⚠️ Multiple contacts found. Please refine your search:\n"
                        + "\n".join(lines)
                        + suffix
                    )
                    return

                target_contact = contacts[0]

            assert target_contact is not None
            contact_id = str(target_contact.get("id") or "").strip()
            if not contact_id:
                self._audit_command(
                    interaction=interaction,
                    action="crm.view_skills",
                    result="error",
                    metadata={
                        "search_term": query or None,
                        "error": "missing_contact_id",
                    },
                )
                await interaction.followup.send("❌ Contact ID not found.")
                return

            full_contact = self.espo_api.request("GET", f"Contact/{contact_id}")
            contact_name = str(
                full_contact.get("name") or target_contact.get("name") or "Unknown"
            )
            skills, source = self._extract_contact_skills_for_view(full_contact)

            if not skills:
                self._audit_command(
                    interaction=interaction,
                    action="crm.view_skills",
                    result="success",
                    metadata={
                        "search_term": query or None,
                        "target_scope": target_scope,
                        "skills_count": 0,
                        "source": source,
                    },
                    resource_type="crm_contact",
                    resource_id=contact_id,
                )
                await interaction.followup.send(
                    f"ℹ️ No skills found for **{contact_name}**."
                )
                return

            embed = discord.Embed(
                title="🛠️ CRM Skills",
                description=f"Skills for **{contact_name}**",
                color=0x0099FF,
            )
            skill_lines: list[str] = []
            for skill, strength in skills[:25]:
                if strength is None:
                    skill_lines.append(f"- `{skill}`")
                else:
                    skill_lines.append(f"- `{skill}` ({strength}/5)")
            if len(skills) > 25:
                skill_lines.append(f"...and {len(skills) - 25} more.")
            embed.add_field(name="Skills", value="\n".join(skill_lines), inline=False)
            embed.add_field(
                name="🔗 CRM Profile",
                value=f"[View in CRM]({self.base_url}/#Contact/view/{contact_id})",
                inline=False,
            )

            await interaction.followup.send(embed=embed)
            self._audit_command(
                interaction=interaction,
                action="crm.view_skills",
                result="success",
                metadata={
                    "search_term": query or None,
                    "target_scope": target_scope,
                    "skills_count": len(skills),
                    "source": source,
                },
                resource_type="crm_contact",
                resource_id=contact_id,
            )
        except EspoAPIError as e:
            logger.error(f"EspoCRM API error in view_skills: {e}")
            self._audit_command(
                interaction=interaction,
                action="crm.view_skills",
                result="error",
                metadata={"search_term": search_term, "error": str(e)},
            )
            await interaction.followup.send(f"❌ CRM API error: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error in view_skills: {e}")
            self._audit_command(
                interaction=interaction,
                action="crm.view_skills",
                result="error",
                metadata={"search_term": search_term, "error": str(e)},
            )
            await interaction.followup.send(
                "❌ An unexpected error occurred while fetching skills."
            )

    @app_commands.command(
        name="update-skills",
        description="Update CRM skills for yourself or another member (Steering Committee+ for others)",
    )
    @app_commands.describe(
        skills='Comma-separated skill:strength pairs, e.g. "python:5, react:4, go:3"',
        search_term="Optional: @mention, email, 508 username, name, or contact ID",
    )
    @require_role("Member")
    async def update_skills(
        self,
        interaction: discord.Interaction,
        skills: str,
        search_term: str | None = None,
    ) -> None:
        """Update structured skills (with strengths 1-5) on a CRM contact."""
        try:
            await interaction.response.defer(ephemeral=True)

            # Parse and validate input
            try:
                new_skills = self._parse_skills_input(skills)
            except ValueError as exc:
                self._audit_command(
                    interaction=interaction,
                    action="crm.update_skills",
                    result="error",
                    metadata={"raw_input": skills, "error": str(exc)},
                )
                await interaction.followup.send(f"❌ {exc}")
                return

            query = (search_term or "").strip()
            target_scope = "other" if query else "self"

            # Resolve target contact
            target_contact: dict[str, Any] | None = None
            if query:
                if not hasattr(
                    interaction.user, "roles"
                ) or not check_user_roles_with_hierarchy(
                    interaction.user.roles, ["Steering Committee"]
                ):
                    self._audit_command(
                        interaction=interaction,
                        action="crm.update_skills",
                        result="denied",
                        metadata={
                            "search_term": query,
                            "reason": "missing_required_role",
                        },
                    )
                    await interaction.followup.send(
                        "❌ You must have Steering Committee role or higher to update skills for other people."
                    )
                    return

                contacts = await self._search_contact_for_linking(query)
                if not contacts:
                    self._audit_command(
                        interaction=interaction,
                        action="crm.update_skills",
                        result="success",
                        metadata={
                            "search_term": query,
                            "target_scope": target_scope,
                            "contacts_found": 0,
                        },
                    )
                    await interaction.followup.send(
                        f"❌ No contact found for: `{query}`"
                    )
                    return

                if len(contacts) > 1:
                    lines: list[str] = []
                    for c in contacts[:5]:
                        name = str(c.get("name", "Unknown"))
                        cid = str(c.get("id", ""))
                        lines.append(f"- **{name}** (`{cid}`)")
                    suffix = (
                        f"\n...and {len(contacts) - 5} more."
                        if len(contacts) > 5
                        else ""
                    )
                    self._audit_command(
                        interaction=interaction,
                        action="crm.update_skills",
                        result="success",
                        metadata={
                            "search_term": query,
                            "target_scope": target_scope,
                            "contacts_found": len(contacts),
                            "requires_selection": True,
                        },
                    )
                    await interaction.followup.send(
                        "⚠️ Multiple contacts found. Please refine your search:\n"
                        + "\n".join(lines)
                        + suffix
                    )
                    return

                target_contact = contacts[0]
            else:
                target_contact = await self._find_contact_by_discord_id(
                    str(interaction.user.id)
                )
                if not target_contact:
                    self._audit_command(
                        interaction=interaction,
                        action="crm.update_skills",
                        result="denied",
                        metadata={
                            "target_scope": "self",
                            "reason": "discord_not_linked",
                        },
                    )
                    await interaction.followup.send(
                        "❌ Your Discord account is not linked to a CRM contact. "
                        "Please ask a Steering Committee member to link your account first."
                    )
                    return

            contact_id = str(target_contact.get("id") or "").strip()
            contact_name = str(target_contact.get("name") or "Unknown")
            if not contact_id:
                self._audit_command(
                    interaction=interaction,
                    action="crm.update_skills",
                    result="error",
                    metadata={"error": "missing_contact_id"},
                )
                await interaction.followup.send("❌ Contact ID not found.")
                return

            # Fetch current skills and merge
            full_contact = self.espo_api.request("GET", f"Contact/{contact_id}")
            existing = self._parse_contact_skill_attrs(
                full_contact.get("cSkillAttrs")
            )
            merged = {**existing, **new_skills}

            # Build CRM-format payload {skill: {"strength": N}}
            attrs_payload = {
                skill: {"strength": strength}
                for skill, strength in sorted(merged.items())
            }

            self.espo_api.request(
                "PUT",
                f"Contact/{contact_id}",
                {"cSkillAttrs": json.dumps(attrs_payload)},
            )

            embed = discord.Embed(
                title="✅ Skills Updated",
                description=f"Skills for **{contact_name}** have been updated.",
                color=0x00FF00,
            )
            updated_lines: list[str] = []
            for skill, strength in sorted(
                merged.items(), key=lambda item: (-item[1], item[0].casefold())
            ):
                marker = " *" if skill in new_skills else ""
                updated_lines.append(f"- `{skill}` ({strength}/5){marker}")
            if len(updated_lines) > 25:
                updated_lines = updated_lines[:25]
                updated_lines.append(f"...and {len(merged) - 25} more.")
            embed.add_field(
                name="Skills", value="\n".join(updated_lines), inline=False
            )
            embed.add_field(
                name="🔗 CRM Profile",
                value=f"[View in CRM]({self.base_url}/#Contact/view/{contact_id})",
                inline=False,
            )

            await interaction.followup.send(embed=embed)
            self._audit_command(
                interaction=interaction,
                action="crm.update_skills",
                result="success",
                metadata={
                    "search_term": query or None,
                    "target_scope": target_scope,
                    "skills_updated": list(new_skills.keys()),
                    "skills_count": len(merged),
                },
                resource_type="crm_contact",
                resource_id=contact_id,
            )
        except EspoAPIError as e:
            logger.error(f"EspoCRM API error in update_skills: {e}")
            self._audit_command(
                interaction=interaction,
                action="crm.update_skills",
                result="error",
                metadata={"search_term": search_term, "error": str(e)},
            )
            await interaction.followup.send(f"❌ CRM API error: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error in update_skills: {e}")
            self._audit_command(
                interaction=interaction,
                action="crm.update_skills",
                result="error",
                metadata={"search_term": search_term, "error": str(e)},
            )
            await interaction.followup.send(
                "❌ An unexpected error occurred while updating skills."
            )

    @app_commands.command(
        name="set-github-username",
        description="Set GitHub username for a CRM contact (your own or someone else if Steering Committee+)",
    )
    @app_commands.describe(
        github_username="GitHub username to set",
        search_term="Email, name, or contact ID to find contact (optional - if not provided, sets your own GitHub username)",
    )
    async def set_github_username(
        self,
        interaction: discord.Interaction,
        github_username: str,
        search_term: str | None = None,
    ) -> None:
        """Set GitHub username for a CRM contact."""
        try:
            await interaction.response.defer(ephemeral=True)

            # Clean the GitHub username (remove @ if present)
            clean_github_username = github_username.lstrip("@")

            # Determine target contact
            target_contact = None

            if search_term:
                # Setting for someone else - requires Steering Committee+ role
                if not hasattr(
                    interaction.user, "roles"
                ) or not check_user_roles_with_hierarchy(
                    interaction.user.roles, ["Steering Committee"]
                ):
                    self._audit_command(
                        interaction=interaction,
                        action="crm.set_github_username",
                        result="denied",
                        metadata={
                            "search_term": search_term,
                            "reason": "missing_required_role",
                        },
                    )
                    await interaction.followup.send(
                        "❌ You must have Steering Committee role or higher to set GitHub usernames for other people."
                    )
                    if not target_contact:
                        return

                # Search for target contact
                contacts = await self._search_contact_for_linking(search_term)
                if not contacts:
                    self._audit_command(
                        interaction=interaction,
                        action="crm.set_github_username",
                        result="success",
                        metadata={
                            "search_term": search_term,
                            "contact_found": False,
                            "target_scope": "other",
                        },
                    )
                    await interaction.followup.send(
                        f"❌ No contact found for: `{search_term}`"
                    )
                    if not target_contact:
                        return
                elif len(contacts) > 1:
                    self._audit_command(
                        interaction=interaction,
                        action="crm.set_github_username",
                        result="success",
                        metadata={
                            "search_term": search_term,
                            "contact_found": False,
                            "target_scope": "other",
                            "reason": "multiple_contacts",
                        },
                    )
                    await interaction.followup.send(
                        f"❌ Multiple contacts found for `{search_term}`. Please be more specific or use the contact ID."
                    )
                    return

                target_contact = contacts[0]
            else:
                # Setting own GitHub username - find contact by Discord user ID
                target_contact = await self._find_contact_by_discord_id(
                    str(interaction.user.id)
                )
                if not target_contact:
                    self._audit_command(
                        interaction=interaction,
                        action="crm.set_github_username",
                        result="denied",
                        metadata={
                            "target_scope": "self",
                            "reason": "discord_not_linked",
                        },
                    )
                    await interaction.followup.send(
                        "❌ Your Discord account is not linked to a CRM contact. "
                        "Please ask a Steering Committee member to link your account first."
                    )
                    return

            contact_id = target_contact.get("id")
            contact_name = target_contact.get("name", "Unknown")

            if not contact_id:
                self._audit_command(
                    interaction=interaction,
                    action="crm.set_github_username",
                    result="error",
                    metadata={
                        "search_term": search_term,
                        "error": "contact_id_missing",
                    },
                )
                await interaction.followup.send("❌ Contact ID not found.")
                return

            # Update the contact's GitHub username
            update_data = {"cGitHubUsername": clean_github_username}

            update_response = self.espo_api.request(
                "PUT", f"Contact/{contact_id}", update_data
            )

            if update_response:
                # Create success embed
                embed = discord.Embed(
                    title="✅ GitHub Username Set",
                    description="Successfully updated GitHub username in CRM contact",
                    color=0x00FF00,
                )
                embed.add_field(
                    name="👤 Contact", value=f"{contact_name}", inline=False
                )
                embed.add_field(
                    name="📧 Email",
                    value=f"{target_contact.get('c508Email') or target_contact.get('emailAddress', 'N/A')}",
                    inline=True,
                )
                embed.add_field(
                    name="🐙 GitHub Username",
                    value=f"@{clean_github_username}",
                    inline=True,
                )
                # Add CRM link
                if contact_id:
                    profile_url = f"{self.base_url}/#Contact/view/{contact_id}"
                    embed.add_field(
                        name="🔗 CRM Profile",
                        value=f"[View in CRM]({profile_url})",
                        inline=True,
                    )

                await interaction.followup.send(embed=embed)

                logger.info(
                    f"GitHub username set for {contact_name} (ID: {contact_id}) "
                    f"to @{clean_github_username} by {interaction.user.name}"
                )
                self._audit_command(
                    interaction=interaction,
                    action="crm.set_github_username",
                    result="success",
                    metadata={
                        "search_term": search_term,
                        "github_username": clean_github_username,
                        "target_scope": "other" if search_term else "self",
                    },
                    resource_type="crm_contact",
                    resource_id=str(contact_id),
                )
            else:
                self._audit_command(
                    interaction=interaction,
                    action="crm.set_github_username",
                    result="error",
                    metadata={
                        "search_term": search_term,
                        "github_username": clean_github_username,
                        "error": "crm_update_failed",
                    },
                    resource_type="crm_contact",
                    resource_id=str(contact_id),
                )
                await interaction.followup.send(
                    "❌ Failed to update contact in CRM. Please try again."
                )

        except EspoAPIError as e:
            logger.error(f"EspoCRM API error in set_github_username: {e}")
            self._audit_command(
                interaction=interaction,
                action="crm.set_github_username",
                result="error",
                metadata={"search_term": search_term, "error": str(e)},
            )
            await interaction.followup.send(f"❌ CRM API error: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error in set_github_username: {e}")
            self._audit_command(
                interaction=interaction,
                action="crm.set_github_username",
                result="error",
                metadata={"search_term": search_term, "error": str(e)},
            )
            await interaction.followup.send(
                "❌ An unexpected error occurred while setting the GitHub username."
            )

    async def _check_existing_resume(
        self, contact_id: str, filename: str, filesize: int
    ) -> tuple[bool, str | None]:
        """Check if contact already has a resume with the same name and size."""
        try:
            # Get current contact data
            contact_data = self.espo_api.request("GET", f"Contact/{contact_id}")
            current_resume_ids = contact_data.get("resumeIds", [])

            # Check each existing resume
            for resume_id in current_resume_ids:
                try:
                    # Get attachment details
                    attachment_data = self.espo_api.request(
                        "GET", f"Attachment/{resume_id}"
                    )

                    # Compare filename and size
                    if (
                        attachment_data.get("name") == filename
                        and attachment_data.get("size") == filesize
                    ):
                        return True, resume_id

                except EspoAPIError:
                    # If we can't fetch attachment details, skip it
                    continue

            return False, None

        except EspoAPIError as e:
            logger.error(f"Failed to check existing resumes: {e}")
            return False, None

    async def _update_contact_resume(
        self, contact_id: str, attachment_id: str, overwrite: bool = False
    ) -> bool:
        """Update contact's resume field with the attachment ID."""
        try:
            # Get current contact data to preserve existing resume IDs
            contact_data = self.espo_api.request("GET", f"Contact/{contact_id}")
            current_resume_ids = contact_data.get("resumeIds", [])

            # Handle overwrite vs append
            if overwrite:
                # Replace all existing resumes with just this one
                new_resume_ids = [attachment_id]
            else:
                # Add new attachment ID to the end of resume IDs list
                if attachment_id not in current_resume_ids:
                    current_resume_ids.append(attachment_id)
                new_resume_ids = current_resume_ids

            # Update the contact with new resume IDs
            update_data = {"resumeIds": new_resume_ids}

            self.espo_api.request("PUT", f"Contact/{contact_id}", update_data)
            return True
        except EspoAPIError as e:
            logger.error(f"Failed to update contact resume: {e}")
            return False

    async def _upload_resume_attachment_to_contact(
        self,
        *,
        interaction: discord.Interaction,
        file_content: bytes,
        filename: str,
        file_size: int,
        contact: dict[str, Any],
        target_scope: str,
        search_term: str | None,
        overwrite: bool,
        link_user: discord.Member | None,
        inferred_contact_meta: dict[str, str] | None,
    ) -> None:
        """Upload attachment and launch the worker preview for a given contact."""
        contact_id = contact.get("id")
        contact_name = contact.get("name", "Unknown")

        if not contact_id:
            self._audit_command(
                interaction=interaction,
                action="crm.upload_resume",
                result="error",
                metadata={
                    "search_term": search_term,
                    "filename": filename,
                    "error": "contact_id_missing",
                },
            )
            await interaction.followup.send("❌ Contact ID not found.")
            return

        try:
            attachment = self.espo_api.upload_file(
                file_content=file_content,
                filename=filename,
                related_type="Contact",
                related_id=contact_id,
                field="resume",
            )

            attachment_id = attachment.get("id")
            if not attachment_id:
                self._audit_command(
                    interaction=interaction,
                    action="crm.upload_resume",
                    result="error",
                    metadata={
                        "search_term": search_term,
                        "filename": filename,
                        "error": "attachment_id_missing",
                    },
                    resource_type="crm_contact",
                    resource_id=str(contact_id),
                )
                await interaction.followup.send("❌ Failed to upload file to CRM.")
                return

            if not await self._update_contact_resume(
                contact_id, attachment_id, overwrite
            ):
                self._audit_command(
                    interaction=interaction,
                    action="crm.upload_resume",
                    result="error",
                    metadata={
                        "search_term": search_term,
                        "filename": filename,
                        "attachment_id": attachment_id,
                        "overwrite": overwrite,
                        "target_scope": target_scope,
                        "reason": "resume_link_update_failed",
                    },
                    resource_type="crm_contact",
                    resource_id=str(contact_id),
                )
                await interaction.followup.send(
                    "⚠️ File uploaded, but failed to link in contact resume field."
                )
                return

            logger.info(
                "Resume uploaded for %s (contact_id=%s, attachment_id=%s) by %s",
                contact_name,
                contact_id,
                attachment_id,
                interaction.user.name,
            )
            success_metadata = {
                "search_term": search_term,
                "filename": filename,
                "size_bytes": file_size,
                "overwrite": overwrite,
                "target_scope": target_scope,
                "attachment_id": attachment_id,
                "stage": "uploaded_and_linked",
            }
            if inferred_contact_meta:
                for key in ("method", "value"):
                    value = inferred_contact_meta.get(key)
                    if value:
                        success_metadata[f"inferred_{key}"] = value

            self._audit_command(
                interaction=interaction,
                action="crm.upload_resume",
                result="success",
                metadata=success_metadata,
                resource_type="crm_contact",
                resource_id=str(contact_id),
            )
            await self._run_resume_extract_and_preview(
                interaction=interaction,
                contact_id=contact_id,
                contact_name=contact_name,
                attachment_id=attachment_id,
                filename=filename,
                link_member=link_user,
            )
        except EspoAPIError as e:
            logger.error(f"Failed to upload file to EspoCRM: {e}")
            self._audit_command(
                interaction=interaction,
                action="crm.upload_resume",
                result="error",
                metadata={
                    "search_term": search_term,
                    "filename": filename,
                    "error": str(e),
                },
                resource_type="crm_contact",
                resource_id=str(contact_id),
            )
            await interaction.followup.send(
                f"❌ Failed to upload file to CRM: {str(e)}"
            )

    @app_commands.command(
        name="upload-resume",
        description="Upload resume, extract profile fields, and preview CRM updates",
    )
    @app_commands.describe(
        file="Resume file to upload (PDF, DOC, DOCX, TXT)",
        search_term="Email, name, or contact ID (Steering Committee+ only). Omit to infer from resume.",
        overwrite="Replace existing resumes instead of appending",
        link_user="Discord user to link to this CRM contact (optional, Steering Committee+ for others)",
    )
    async def upload_resume(
        self,
        interaction: discord.Interaction,
        file: discord.Attachment,
        search_term: str | None = None,
        overwrite: bool = False,
        link_user: discord.Member | None = None,
    ) -> None:
        """Upload resume and run backend extraction to preview CRM updates."""
        try:
            await interaction.response.defer(ephemeral=True)

            if not settings.api_shared_secret:
                self._audit_command(
                    interaction=interaction,
                    action="crm.upload_resume",
                    result="error",
                    metadata={
                        "filename": file.filename,
                        "reason": "api_shared_secret_missing",
                    },
                )
                await interaction.followup.send(
                    "❌ API_SHARED_SECRET is not configured for backend API access."
                )
                return

            # Validate file type
            valid_extensions = {".pdf", ".doc", ".docx", ".txt"}
            file_extension = (
                "." + file.filename.split(".")[-1].lower()
                if "." in file.filename
                else ""
            )

            if file_extension not in valid_extensions:
                self._audit_command(
                    interaction=interaction,
                    action="crm.upload_resume",
                    result="denied",
                    metadata={
                        "filename": file.filename,
                        "reason": "invalid_file_type",
                    },
                )
                await interaction.followup.send(
                    f"❌ Invalid file type. Please upload a PDF, DOC, DOCX, or TXT file.\nYou uploaded: `{file.filename}`"
                )
                return

            # Validate file size (10MB limit)
            max_size = 10 * 1024 * 1024  # 10MB in bytes
            if file.size > max_size:
                self._audit_command(
                    interaction=interaction,
                    action="crm.upload_resume",
                    result="denied",
                    metadata={
                        "filename": file.filename,
                        "size_bytes": file.size,
                        "reason": "file_too_large",
                    },
                )
                await interaction.followup.send(
                    f"❌ File too large. Maximum size is 10MB.\nYour file: {file.size / (1024 * 1024):.1f}MB"
                )
                return

            is_steering = hasattr(
                interaction.user, "roles"
            ) and check_user_roles_with_hierarchy(
                interaction.user.roles, ["Steering Committee"]
            )
            if search_term and not is_steering:
                self._audit_command(
                    interaction=interaction,
                    action="crm.upload_resume",
                    result="denied",
                    metadata={
                        "search_term": search_term,
                        "filename": file.filename,
                        "target_scope": "other",
                        "reason": "missing_required_role_search",
                    },
                )
                await interaction.followup.send(
                    "❌ You must have Steering Committee role or higher to upload a resume for another contact."
                )
                return
            if (
                link_user is not None
                and link_user.id != interaction.user.id
                and not is_steering
            ):
                self._audit_command(
                    interaction=interaction,
                    action="crm.upload_resume",
                    result="denied",
                    metadata={
                        "filename": file.filename,
                        "target_scope": "other",
                        "reason": "missing_required_role_link_user",
                    },
                )
                await interaction.followup.send(
                    "❌ You must have Steering Committee role or higher to upload a resume for another Discord user."
                )
                return
            if not is_steering:
                link_user = None

            # Read attachment once for Steering Committee resume-based inference.
            file_content = await file.read()

            # Determine target contact
            target_contact = None
            target_scope = "self"
            inferred_contact_meta: dict[str, str] | None = None

            if search_term:
                contacts = await self._search_contact_for_linking(search_term)
                if not contacts:
                    self._audit_command(
                        interaction=interaction,
                        action="crm.upload_resume",
                        result="denied",
                        metadata={
                            "search_term": search_term,
                            "filename": file.filename,
                            "contact_found": False,
                            "target_scope": "other",
                        },
                    )
                    await interaction.followup.send(
                        f"❌ No contact found for: `{search_term}`"
                    )
                    return
                if len(contacts) > 1:
                    self._audit_command(
                        interaction=interaction,
                        action="crm.upload_resume",
                        result="denied",
                        metadata={
                            "search_term": search_term,
                            "filename": file.filename,
                            "contact_found": False,
                            "target_scope": "other",
                            "reason": "multiple_contacts",
                        },
                    )
                    await interaction.followup.send(
                        f"⚠️ Multiple contacts found for `{search_term}`. "
                        "Please be more specific or use the contact ID."
                    )
                    return
                target_contact = contacts[0]
                target_scope = "other"
            elif is_steering and link_user is not None:
                # Uploading for linked user (Steering Committee+ path)
                target_scope = "other"
                target_contact = await self._find_contact_by_discord_id(
                    str(link_user.id)
                )
                if not target_contact:
                    self._audit_command(
                        interaction=interaction,
                        action="crm.upload_resume",
                        result="denied",
                        metadata={
                            "filename": file.filename,
                            "target_scope": target_scope,
                            "reason": "discord_not_linked",
                        },
                    )
                    await interaction.followup.send(
                        "❌ The provided Discord user is not linked to a CRM contact. "
                        "Please link this user first."
                    )
                    return
            elif is_steering:
                # Uploading for contact inferred from resume content
                (
                    target_contact,
                    inferred_contact_meta,
                ) = await self._infer_contact_from_resume(file_content)
                if not target_contact:
                    inferred_method = (inferred_contact_meta or {}).get("method")
                    inferred_value = (inferred_contact_meta or {}).get("value")
                    inferred_reason = (inferred_contact_meta or {}).get(
                        "reason", "resume_contact_not_found"
                    )
                    inference_metadata = {
                        "filename": file.filename,
                        "target_scope": "resume_inferred",
                        "reason": inferred_reason,
                    }
                    if inferred_method:
                        inference_metadata["inferred_method"] = inferred_method
                    if inferred_value:
                        inference_metadata["inferred_value"] = inferred_value

                    if inferred_reason == "multiple_matches" and inferred_value:
                        self._audit_command(
                            interaction=interaction,
                            action="crm.upload_resume",
                            result="denied",
                            metadata=inference_metadata,
                        )
                        await interaction.followup.send(
                            f"⚠️ Multiple contacts match `{inferred_value}` from the resume. "
                            "Please provide `search_term` or `link_user`."
                        )
                    elif inferred_reason == "no_matching_contact":
                        self._audit_command(
                            interaction=interaction,
                            action="crm.upload_resume",
                            result="denied",
                            metadata=inference_metadata,
                        )
                        view = ResumeCreateContactView(
                            crm_cog=self,
                            interaction=interaction,
                            file_content=file_content,
                            filename=file.filename,
                            file_size=file.size,
                            search_term=search_term,
                            overwrite=overwrite,
                            link_user=link_user,
                            inferred_contact_meta=inferred_contact_meta,
                            target_scope="resume_inferred",
                        )
                        await interaction.followup.send(
                            "⚠️ Could not find a unique contact from this resume. "
                            "Would you like to create a new contact from the parsed details?",
                            view=view,
                        )
                    else:
                        self._audit_command(
                            interaction=interaction,
                            action="crm.upload_resume",
                            result="denied",
                            metadata=inference_metadata,
                        )
                        await interaction.followup.send(
                            "⚠️ Resume-based contact inference failed. "
                            "Please provide `search_term` or `link_user`."
                        )
                    return
                target_scope = "resume"
            else:
                # Uploading own resume - find contact by Discord user ID
                target_contact = await self._find_contact_by_discord_id(
                    str(interaction.user.id)
                )
                if not target_contact:
                    self._audit_command(
                        interaction=interaction,
                        action="crm.upload_resume",
                        result="denied",
                        metadata={
                            "filename": file.filename,
                            "target_scope": "self",
                            "reason": "discord_not_linked",
                        },
                    )
                    await interaction.followup.send(
                        "❌ Your Discord account is not linked to a CRM contact. "
                        "Please ask a Steering Committee member to link your account first."
                    )
                    return

            assert target_contact is not None
            await self._upload_resume_attachment_to_contact(
                interaction=interaction,
                file_content=file_content,
                filename=file.filename,
                file_size=file.size,
                contact=target_contact,
                target_scope=target_scope,
                search_term=search_term,
                overwrite=overwrite,
                link_user=link_user,
                inferred_contact_meta=inferred_contact_meta,
            )

        except Exception as e:
            logger.error(f"Unexpected error in upload_resume: {e}")
            self._audit_command(
                interaction=interaction,
                action="crm.upload_resume",
                result="error",
                metadata={
                    "search_term": search_term,
                    "filename": file.filename,
                    "error": str(e),
                },
            )
            await interaction.followup.send(
                "❌ An unexpected error occurred while uploading the resume."
            )


async def setup(bot: commands.Bot) -> None:
    """Add the CRM cog to the bot."""
    cog = CRMCog(bot)
    await bot.add_cog(cog)
    # Slash commands will be synced automatically in bot.py
