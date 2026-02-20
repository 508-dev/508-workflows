"""
CRM integration cog for the 508.dev Discord bot.

This cog provides commands for interacting with EspoCRM through Discord slash commands.
It allows team members to quickly access CRM data without leaving Discord.
"""

import logging
import io
from typing import Any
from discord.ext import commands
from discord import app_commands
import discord

from five08.discord_bot.config import settings
from five08.clients import espo
from five08.discord_bot.utils.role_decorators import (
    require_role,
    check_user_roles_with_hierarchy,
)

logger = logging.getLogger(__name__)

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
                await interaction.response.send_message(
                    "❌ You must have the Member role to download resumes.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer(ephemeral=True)

            # Use shared download method
            await cog._download_and_send_resume(
                interaction, self.contact_name, self.resume_id
            )

        except Exception as e:
            logger.error(f"Unexpected error in resume button callback: {e}")
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


class CRMCog(commands.Cog):
    """CRM integration cog for EspoCRM operations."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Construct API URL from base URL
        api_url = settings.espo_base_url.rstrip("/") + "/api/v1"
        self.espo_api = EspoAPI(api_url, settings.espo_api_key)
        # Store base URL for profile links
        self.base_url = settings.espo_base_url.rstrip("/")

    async def _download_and_send_resume(
        self, interaction: discord.Interaction, contact_name: str, resume_id: str
    ) -> None:
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

        except EspoAPIError as e:
            logger.error(f"Failed to download resume {resume_id}: {e}")
            await interaction.followup.send(f"❌ Failed to download resume: {str(e)}")

    def _check_member_role(self, interaction: discord.Interaction) -> bool:
        """Check if user has Member role or higher for resume access."""
        if not hasattr(interaction.user, "roles"):
            return False
        return check_user_roles_with_hierarchy(interaction.user.roles, ["Member"])

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
            skills_list = (
                [skill.strip() for skill in skills.split(",") if skill.strip()]
                if skills
                else []
            )

            if not query_value and not skills_list:
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
                "select": "id,name,emailAddress,c508Email,cDiscordUsername,cDiscordUserID,phoneNumber,type,resumeIds,resumeNames,resumeTypes",
            }

            response = self.espo_api.request("GET", "Contact", search_params)
            contacts = response.get("list", [])

            if not contacts:
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

        except EspoAPIError as e:
            logger.error(f"EspoCRM API error: {e}")
            await interaction.followup.send(f"❌ CRM API error: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error in CRM search: {e}")
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

        except EspoAPIError as e:
            logger.error(f"EspoCRM API error: {e}")
            embed = discord.Embed(
                title="❌ CRM Status",
                description=f"Failed to connect to EspoCRM: {str(e)}",
                color=0xFF0000,
            )
            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error(f"Unexpected error in CRM status: {e}")
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
                await interaction.followup.send(f"❌ No contact found for: `{query}`")
                return

            contact = contacts[0]
            contact_name = contact.get("name", "Unknown")

            # Get resume data directly from search results
            resume_ids = contact.get("resumeIds", [])
            resume_names = contact.get("resumeNames", {})

            if not resume_ids or len(resume_ids) == 0:
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
            await self._download_and_send_resume(interaction, contact_name, resume_id)

        except EspoAPIError as e:
            logger.error(f"EspoCRM API error in get_resume: {e}")
            await interaction.followup.send(f"❌ CRM API error: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error in get_resume: {e}")
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
                return True
            else:
                await interaction.followup.send(
                    "❌ Failed to update contact in CRM. Please try again."
                )
                return False

        except EspoAPIError as e:
            logger.error(f"EspoCRM API error in _perform_discord_linking: {e}")
            await interaction.followup.send(f"❌ CRM API error: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error in _perform_discord_linking: {e}")
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
                await interaction.followup.send(
                    f"❌ No contact found for: `{search_term}`"
                )
                return

            # Handle multiple results - show choices
            if len(contacts) > 1:
                await self._show_contact_choices(
                    interaction, user, search_term, contacts
                )
                return

            # Single result - proceed with linking
            contact = contacts[0]
            await self._perform_discord_linking(interaction, user, contact)

        except EspoAPIError as e:
            logger.error(f"EspoCRM API error in link_discord_user: {e}")
            await interaction.followup.send(f"❌ CRM API error: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error in link_discord_user: {e}")
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
                await interaction.followup.send(
                    "✅ **All Members Linked**\nAll Discord users with Member role are linked in the CRM!"
                )
                return

            # Send list of unlinked users
            await self._send_unlinked_users_list(interaction, unlinked_users)

        except EspoAPIError as e:
            logger.error(f"EspoCRM API error in unlinked_discord_users: {e}")
            await interaction.followup.send(f"❌ CRM API error: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error in unlinked_discord_users: {e}")
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
                    await interaction.followup.send(
                        "❌ You must have Steering Committee role or higher to set GitHub usernames for other people."
                    )
                    return

                # Search for target contact
                contacts = await self._search_contact_for_linking(search_term)
                if not contacts:
                    await interaction.followup.send(
                        f"❌ No contact found for: `{search_term}`"
                    )
                    return
                elif len(contacts) > 1:
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
                    await interaction.followup.send(
                        "❌ Your Discord account is not linked to a CRM contact. "
                        "Please ask a Steering Committee member to link your account first."
                    )
                    return

            contact_id = target_contact.get("id")
            contact_name = target_contact.get("name", "Unknown")

            if not contact_id:
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
            else:
                await interaction.followup.send(
                    "❌ Failed to update contact in CRM. Please try again."
                )

        except EspoAPIError as e:
            logger.error(f"EspoCRM API error in set_github_username: {e}")
            await interaction.followup.send(f"❌ CRM API error: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error in set_github_username: {e}")
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

    @app_commands.command(
        name="upload-resume",
        description="Upload a resume file to CRM (your own or for someone else if Steering Committee+)",
    )
    @app_commands.describe(
        file="Resume file to upload (PDF, DOC, DOCX)",
        search_term="Email, name, or contact ID to find contact (optional - if not provided, uploads to your own profile)",
        overwrite="Whether to replace all existing resumes instead of adding (default: False)",
    )
    async def upload_resume(
        self,
        interaction: discord.Interaction,
        file: discord.Attachment,
        search_term: str | None = None,
        overwrite: bool = False,
    ) -> None:
        """Upload a resume file to the CRM.

        By default, new resumes are added alongside existing ones.
        Use overwrite=True to replace all existing resumes with this one.
        """
        try:
            await interaction.response.defer(ephemeral=True)

            # Validate file type
            valid_extensions = {".pdf", ".doc", ".docx"}
            file_extension = (
                "." + file.filename.split(".")[-1].lower()
                if "." in file.filename
                else ""
            )

            if file_extension not in valid_extensions:
                await interaction.followup.send(
                    f"❌ Invalid file type. Please upload a PDF, DOC, or DOCX file.\nYou uploaded: `{file.filename}`"
                )
                return

            # Validate file size (10MB limit)
            max_size = 10 * 1024 * 1024  # 10MB in bytes
            if file.size > max_size:
                await interaction.followup.send(
                    f"❌ File too large. Maximum size is 10MB.\nYour file: {file.size / (1024 * 1024):.1f}MB"
                )
                return

            # Determine target contact
            target_contact = None

            if search_term:
                # Uploading for someone else - requires Steering Committee+ role
                if not hasattr(
                    interaction.user, "roles"
                ) or not check_user_roles_with_hierarchy(
                    interaction.user.roles, ["Steering Committee"]
                ):
                    await interaction.followup.send(
                        "❌ You must have Steering Committee role or higher to upload resumes for other people."
                    )
                    return

                # Search for target contact
                contacts = await self._search_contact_for_linking(search_term)
                if not contacts:
                    await interaction.followup.send(
                        f"❌ No contact found for: `{search_term}`"
                    )
                    return
                elif len(contacts) > 1:
                    await interaction.followup.send(
                        f"❌ Multiple contacts found for `{search_term}`. Please be more specific or use the contact ID."
                    )
                    return

                target_contact = contacts[0]
            else:
                # Uploading own resume - find contact by Discord user ID
                target_contact = await self._find_contact_by_discord_id(
                    str(interaction.user.id)
                )
                if not target_contact:
                    await interaction.followup.send(
                        "❌ Your Discord account is not linked to a CRM contact. "
                        "Please ask a Steering Committee member to link your account first."
                    )
                    return

            contact_id = target_contact.get("id")
            contact_name = target_contact.get("name", "Unknown")

            if not contact_id:
                await interaction.followup.send("❌ Contact ID not found.")
                return

            # Check for existing resume with same name and size
            has_duplicate, existing_resume_id = await self._check_existing_resume(
                contact_id, file.filename, file.size
            )

            if has_duplicate:
                # Show confirmation dialog
                embed = discord.Embed(
                    title="⚠️ Duplicate Resume Detected",
                    description=f"A resume with the same name and file size already exists for **{contact_name}**.",
                    color=0xFFA500,
                )
                embed.add_field(name="📄 File", value=file.filename, inline=True)
                embed.add_field(
                    name="📁 Size", value=f"{file.size / 1024:.1f} KB", inline=True
                )
                embed.add_field(
                    name="❓ Question",
                    value="Do you want to upload this resume anyway? This will add it as a new resume without replacing the existing one.",
                    inline=False,
                )

                view = ResumeConfirmationView(
                    self,
                    interaction,
                    file,
                    contact_id,
                    contact_name,
                    existing_resume_id or "",
                    overwrite,
                )
                await interaction.followup.send(embed=embed, view=view)
                return

            # Download file content from Discord
            file_content = await file.read()

            # Upload file to EspoCRM
            try:
                attachment = self.espo_api.upload_file(
                    file_content=file_content,
                    filename=file.filename,
                    related_type="Contact",
                    related_id=contact_id,
                    field="resume",
                )

                attachment_id = attachment.get("id")
                if not attachment_id:
                    await interaction.followup.send("❌ Failed to upload file to CRM.")
                    return

                # Update contact's resume field
                if await self._update_contact_resume(
                    contact_id, attachment_id, overwrite
                ):
                    # Create success embed
                    embed = discord.Embed(
                        title="✅ Resume Uploaded Successfully",
                        description="Resume has been uploaded and linked to the contact.",
                        color=0x00FF00,
                    )
                    embed.add_field(name="👤 Contact", value=contact_name, inline=True)
                    embed.add_field(name="📄 File", value=file.filename, inline=True)
                    embed.add_field(
                        name="📁 Size", value=f"{file.size / 1024:.1f} KB", inline=True
                    )

                    # Add CRM link
                    profile_url = f"{self.base_url}/#Contact/view/{contact_id}"
                    embed.add_field(
                        name="🔗 CRM Profile",
                        value=f"[View in CRM]({profile_url})",
                        inline=False,
                    )

                    await interaction.followup.send(embed=embed)

                    logger.info(
                        f"Resume uploaded for {contact_name} (ID: {contact_id}) "
                        f"by {interaction.user.name}: {file.filename}"
                    )
                else:
                    await interaction.followup.send(
                        "⚠️ File uploaded but failed to link to contact. Please check CRM manually."
                    )

            except EspoAPIError as e:
                logger.error(f"Failed to upload file to EspoCRM: {e}")
                await interaction.followup.send(
                    f"❌ Failed to upload file to CRM: {str(e)}"
                )

        except Exception as e:
            logger.error(f"Unexpected error in upload_resume: {e}")
            await interaction.followup.send(
                "❌ An unexpected error occurred while uploading the resume."
            )


async def setup(bot: commands.Bot) -> None:
    """Add the CRM cog to the bot."""
    cog = CRMCog(bot)
    await bot.add_cog(cog)
    # Slash commands will be synced automatically in bot.py
