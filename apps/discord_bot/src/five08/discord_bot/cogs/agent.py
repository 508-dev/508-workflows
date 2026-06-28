"""Discord gateway for the backend agent orchestrator."""

from __future__ import annotations

import asyncio
import difflib
import logging
import re
import time
from typing import Any, Awaitable, Callable, Literal, cast
from uuid import uuid4

import discord
import requests
from discord import app_commands
from discord.ext import commands

from five08.discord_bot.config import settings
from five08.discord_bot.utils.audit import DiscordAuditCogMixin
from five08.tls import default_ca_bundle_path

logger = logging.getLogger(__name__)
_MENTION_RATE_LIMIT_WINDOW_SECONDS = 60.0
_MENTION_RATE_LIMIT_MAX_REQUESTS = 5
_PUBLIC_SAFE_CLARIFICATION_MESSAGES = frozenset(
    {
        "I could not turn that into a supported task action.",
        "Which project should I search?",
        "What should the task be?",
    }
)
_GENERIC_UNSUPPORTED_AGENT_MESSAGE = (
    "I could not turn that into a supported task action."
)
_AGENT_HELP_REQUESTS = frozenset(
    {
        "help",
        "what can you do",
        "what kind of things can you do",
        "what things can you do",
        "what can the agent do",
        "what can you help with",
    }
)
_AGENT_PRESENCE_CHECKS = frozenset(
    {
        "hello",
        "hi",
        "hey",
        "do you see this",
        "can you see this",
        "are you there",
        "are you here",
        "you there",
        "ping",
        "test",
    }
)
_AGENT_ACKNOWLEDGEMENTS = frozenset(
    {
        "thanks",
        "thank you",
        "thx",
        "ok",
        "okay",
        "got it",
        "cool",
        "nevermind",
        "never mind",
        "cancel",
    }
)


class AgentConfirmationView(discord.ui.View):
    """Confirmation controls for one frozen backend agent plan."""

    def __init__(
        self,
        *,
        cog: "AgentCog",
        requester_id: int,
        plan_id: str,
        context: dict[str, Any],
    ) -> None:
        super().__init__(timeout=600)
        self.cog = cog
        self.requester_id = requester_id
        self.plan_id = plan_id
        self.context = context

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.requester_id:
            return True
        await interaction.response.send_message(
            "Only the requester can confirm this agent plan.",
            ephemeral=True,
        )
        return False

    def _disable(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        self.stop()

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.primary)
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["AgentConfirmationView"],
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            confirmation_context = await self._confirmation_context(interaction)
            response = await self.cog._post_agent_confirmation(
                plan_id=self.plan_id,
                context=confirmation_context,
                confirm=True,
            )
            transport_failed = False
        except Exception as exc:
            logger.warning("Agent confirmation request failed: %s", exc)
            response = {"status": "failed", "message": str(exc)}
            transport_failed = True
        self.cog._audit_command_safe(
            interaction=interaction,
            action="agent.confirm",
            result=AgentCog._audit_result_for_agent_response(response),
            metadata={
                "plan_id": self.plan_id,
                "status": response.get("status"),
                "error": response.get("error"),
            },
        )
        if not transport_failed:
            self._disable()
        await interaction.followup.send(
            self.cog._format_agent_response(response),
            ephemeral=True,
        )
        if not transport_failed and interaction.message is not None:
            try:
                await interaction.message.edit(view=self)
            except discord.HTTPException:
                logger.warning(
                    "Failed disabling agent confirmation view", exc_info=True
                )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["AgentConfirmationView"],
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            confirmation_context = await self._confirmation_context(interaction)
            response = await self.cog._post_agent_confirmation(
                plan_id=self.plan_id,
                context=confirmation_context,
                confirm=False,
            )
            transport_failed = False
        except Exception as exc:
            logger.warning("Agent cancellation request failed: %s", exc)
            response = {"status": "failed", "message": str(exc)}
            transport_failed = True
        self.cog._audit_command_safe(
            interaction=interaction,
            action="agent.cancel",
            result=AgentCog._audit_result_for_agent_response(response),
            metadata={
                "plan_id": self.plan_id,
                "status": response.get("status"),
                "error": response.get("error"),
            },
        )
        if not transport_failed:
            self._disable()
        await interaction.followup.send(
            self.cog._format_agent_response(response),
            ephemeral=True,
        )
        if not transport_failed and interaction.message is not None:
            try:
                await interaction.message.edit(view=self)
            except discord.HTTPException:
                logger.warning(
                    "Failed disabling agent confirmation view", exc_info=True
                )

    async def _confirmation_context(
        self,
        interaction: discord.Interaction,
    ) -> dict[str, Any]:
        context = self.cog._build_agent_context(interaction)
        original_guild_id = self.context.get("guild_id")
        if context.get("organization_id") is None and original_guild_id:
            context["organization_id"] = self.context.get("organization_id")
            context["guild_id"] = original_guild_id
            context["channel_id"] = self.context.get("channel_id")
            fresh_roles = await self.cog._guild_role_names(
                guild_id=str(original_guild_id),
                user_id=interaction.user.id,
            )
            context["roles"] = fresh_roles or self._original_roles()
        original_message_id = self.context.get("message_id")
        if original_message_id:
            context["message_id"] = original_message_id
        original_operation_id = self.context.get("operation_id")
        if original_operation_id:
            context["operation_id"] = original_operation_id
        return context

    def _original_roles(self) -> list[str]:
        roles = self.context.get("roles")
        if not isinstance(roles, list):
            return []
        return [
            str(role).strip()
            for role in roles
            if isinstance(role, str) and str(role).strip()
        ]


class AgentCog(DiscordAuditCogMixin, commands.Cog):
    """Thin Discord client for backend-owned agent orchestration."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._init_audit_logger()
        self._mention_request_timestamps: dict[int, list[float]] = {}

    @app_commands.command(
        name="agent",
        description="Run an approved English workflow through the agent gateway",
    )
    @app_commands.describe(request="The workflow to plan or execute")
    async def agent_command(
        self,
        interaction: discord.Interaction,
        request: str,
    ) -> None:
        """Send a natural-language request to the backend agent gateway."""
        await interaction.response.defer(ephemeral=True)

        local_response = self._local_agent_response(
            request=request,
            roles=self._role_names_from_user(interaction.user),
            transport="slash",
        )
        if local_response is not None:
            await interaction.followup.send(local_response, ephemeral=True)
            return

        context = self._build_agent_context(interaction)
        try:
            response = await self._post_agent_request(
                message=request,
                context=context,
            )
        except Exception as exc:
            logger.warning("Agent gateway request failed: %s", exc)
            self._audit_command_safe(
                interaction=interaction,
                action="agent.request",
                result="error",
                metadata={"error": str(exc)},
            )
            await interaction.followup.send(
                "Agent gateway request failed. Check backend API configuration.",
                ephemeral=True,
            )
            return

        self._audit_command_safe(
            interaction=interaction,
            action="agent.request",
            result=self._audit_result_for_agent_response(response),
            metadata={
                "status": response.get("status"),
                "error": response.get("error"),
                "plan_id": (response.get("plan") or {}).get("plan_id"),
            },
        )

        view: AgentConfirmationView | None = None
        plan = response.get("plan") if isinstance(response.get("plan"), dict) else None
        if response.get("status") == "requires_confirmation" and plan is not None:
            plan_id = str(plan.get("plan_id") or "")
            if plan_id:
                view = AgentConfirmationView(
                    cog=self,
                    requester_id=interaction.user.id,
                    plan_id=plan_id,
                    context=context,
                )

        if view is None:
            await interaction.followup.send(
                self._format_agent_response(response),
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            self._format_agent_response(response),
            view=view,
            ephemeral=True,
        )

    @commands.Cog.listener("on_message")
    async def agent_mention(self, message: discord.Message) -> None:
        """Handle natural-language agent requests when the bot is mentioned."""
        bot_user = self.bot.user
        if bot_user is None or message.author.bot:
            return
        if message.guild is None:
            await message.reply(
                "Agent mentions only work in servers.",
                mention_author=False,
            )
            return
        bot_mentioned = any(user.id == bot_user.id for user in message.mentions)
        agent_thread = self._is_agent_thread(message.channel, bot_user.id)
        if not bot_mentioned and not agent_thread:
            return

        request = (
            self._extract_mention_request(message.content, bot_user.id)
            if bot_mentioned
            else re.sub(r"\s+", " ", message.content).strip()
        )
        if not request:
            return
        if self._mention_rate_limited(message.author.id):
            self._audit_message_safe(
                message=message,
                action="agent.mention",
                result="denied",
                metadata={"reason": "rate_limited"},
            )
            await message.reply(
                "Too many agent mentions. Try again in a minute.",
                mention_author=False,
            )
            return

        local_response = self._local_agent_response(
            request=request,
            roles=self._role_names_from_user(message.author),
            transport="mention",
        )
        if local_response is not None:
            await self._send_mention_public_response(
                message=message,
                request=request,
                content=local_response,
            )
            return

        context = self._build_agent_context_from_message(message)
        try:
            async with message.channel.typing():
                response = await self._post_agent_request(
                    message=request,
                    context=context,
                )
        except Exception as exc:
            logger.warning("Agent mention request failed: %s", exc)
            self._audit_message_safe(
                message=message,
                action="agent.mention",
                result="error",
                metadata={"error": str(exc)},
            )
            await message.reply(
                "Agent gateway request failed. Check backend API configuration.",
                mention_author=False,
            )
            return

        self._audit_agent_mention_response_safe(
            message=message,
            response=response,
            metadata={
                "status": response.get("status"),
                "error": response.get("error"),
                "plan_id": (response.get("plan") or {}).get("plan_id"),
            },
        )

        view: AgentConfirmationView | None = None
        plan = response.get("plan") if isinstance(response.get("plan"), dict) else None
        if response.get("status") == "requires_confirmation" and plan is not None:
            plan_id = str(plan.get("plan_id") or "")
            if plan_id:
                view = AgentConfirmationView(
                    cog=self,
                    requester_id=message.author.id,
                    plan_id=plan_id,
                    context=context,
                )

        if self._should_reply_publicly_to_mention(response=response, view=view):
            await self._send_mention_public_response(
                message=message,
                request=request,
                content=self._format_agent_response(response),
            )
            return

        sent_dm = await self._send_mention_response_dm(message, response, view)
        if sent_dm:
            await message.reply(
                "I sent the agent response by DM.",
                mention_author=False,
            )
        else:
            await message.reply(
                "I couldn't send you a DM. Use `/agent` for a private response.",
                mention_author=False,
            )

    @staticmethod
    def _extract_mention_request(content: str, bot_user_id: int) -> str:
        mention_pattern = rf"<@!?{bot_user_id}>"
        request = re.sub(mention_pattern, "", content).strip()
        return re.sub(r"\s+", " ", request)

    def _mention_rate_limited(self, user_id: int) -> bool:
        now = time.monotonic()
        window_start = now - _MENTION_RATE_LIMIT_WINDOW_SECONDS
        if not hasattr(self, "_mention_request_timestamps"):
            self._mention_request_timestamps = {}
        for stored_user_id, stored_timestamps in list(
            self._mention_request_timestamps.items()
        ):
            active_timestamps = [
                timestamp
                for timestamp in stored_timestamps
                if timestamp >= window_start
            ]
            if active_timestamps:
                self._mention_request_timestamps[stored_user_id] = active_timestamps
            else:
                del self._mention_request_timestamps[stored_user_id]
        timestamps = self._mention_request_timestamps.get(user_id, [])
        if len(timestamps) >= _MENTION_RATE_LIMIT_MAX_REQUESTS:
            self._mention_request_timestamps[user_id] = timestamps
            return True
        timestamps.append(now)
        self._mention_request_timestamps[user_id] = timestamps
        return False

    def _audit_agent_mention_response_safe(
        self,
        *,
        message: discord.Message,
        response: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        result = self._audit_result_for_agent_response(response)
        if result == "success":
            return
        self._audit_message_safe(
            message=message,
            action="agent.mention",
            result=result,
            metadata=metadata,
        )

    @staticmethod
    def _is_agent_thread(channel: object, bot_user_id: int) -> bool:
        if not isinstance(channel, discord.Thread):
            return False
        return getattr(channel, "owner_id", None) == bot_user_id

    @staticmethod
    def _is_agent_help_request(request: str) -> bool:
        normalized = request.casefold().strip(" ?!.")
        return AgentCog._matches_smalltalk(normalized, _AGENT_HELP_REQUESTS)

    @staticmethod
    def _is_agent_presence_check(request: str) -> bool:
        normalized = request.casefold().strip(" ?!.")
        return AgentCog._matches_smalltalk(normalized, _AGENT_PRESENCE_CHECKS)

    @staticmethod
    def _is_agent_acknowledgement(request: str) -> bool:
        normalized = request.casefold().strip(" ?!.")
        return AgentCog._matches_smalltalk(normalized, _AGENT_ACKNOWLEDGEMENTS)

    def _local_agent_response(
        self,
        *,
        request: str,
        roles: list[str],
        transport: Literal["slash", "mention"],
    ) -> str | None:
        if self._is_agent_help_request(request):
            return self._agent_capabilities_message(roles=roles, transport=transport)
        if self._is_agent_presence_check(request):
            return (
                "Yes, I can see this. Ask for a supported workflow, or ask "
                "`what can you do?` for examples."
            )
        if self._is_agent_acknowledgement(request):
            return "Got it."
        if self._is_unlinked_discord_members_request(request):
            if transport == "slash":
                return (
                    "That report includes member identity/linkage data, so use "
                    "`/unlinked-discord-users` for the dedicated report."
                )
            return (
                "That report includes member identity/linkage data, so use "
                "`/unlinked-discord-users` for the private ephemeral response."
            )
        if self._is_onboarding_people_request(request):
            if transport == "slash":
                return (
                    "That is CRM people/onboarding data, so use "
                    "`/view-onboarding-queue` for the dedicated queue view. "
                    "For targeted lookup, keep using `/agent`."
                )
            return (
                "That is CRM people/onboarding data, so use "
                "`/view-onboarding-queue` for the private ephemeral queue view. "
                "For targeted lookup, use `/search-members`."
            )
        if transport == "slash":
            return None
        member_lookup_target = self._member_info_lookup_target(request)
        if member_lookup_target is not None:
            return self._member_lookup_command_message(member_lookup_target)
        return None

    @staticmethod
    def _matches_smalltalk(normalized: str, phrases: frozenset[str]) -> bool:
        if normalized in phrases:
            return True
        if len(normalized) < 5 or len(normalized) > 40:
            return False
        return any(
            difflib.SequenceMatcher(None, normalized, phrase).ratio() >= 0.9
            for phrase in phrases
        )

    @staticmethod
    def _agent_capabilities_message(
        *,
        roles: list[str],
        transport: Literal["slash", "mention"] = "mention",
    ) -> str:
        normalized_roles = {role.strip().casefold() for role in roles}
        is_admin = bool(normalized_roles & {"admin", "owner", "steering committee"})
        is_engineer = (
            "engineer" in normalized_roles
            or "workflows engineer" in normalized_roles
            or is_admin
        )
        capabilities: list[str] = []
        if is_engineer:
            capabilities.append(
                "- GitHub issues: search issues or create new code-task issues."
            )
        if is_admin:
            capabilities.extend(
                [
                    "- CRM: search contacts, approve/reject onboarding, and submit member agreements.",
                    "- Ops: create 508 accounts, Authentik SSO users, Outline invites, and mailboxes.",
                ]
            )
        if not capabilities:
            lines = [
                "I do not see any agent workflows available for your current Discord roles."
            ]
            if transport == "mention":
                lines.extend(
                    [
                        "",
                        "Use `/agent` when you want the response kept private.",
                    ]
                )
            return "\n".join(lines)
        lines = [
            "I can help with:",
            *capabilities,
        ]
        if transport == "mention":
            lines.extend(
                [
                    "",
                    "Use `/agent` when you want the response kept private.",
                ]
            )
        return "\n".join(lines)

    @staticmethod
    def _is_unlinked_discord_members_request(request: str) -> bool:
        normalized = request.casefold()
        has_unlinked_discord = (
            "unlinked" in normalized
            or "no discord linked" in normalized
            or "not linked" in normalized
            or "without discord" in normalized
        )
        return has_unlinked_discord and "member" in normalized

    @staticmethod
    def _is_onboarding_people_request(request: str) -> bool:
        normalized = request.casefold()
        people_target = any(
            term in normalized
            for term in ["people", "person", "contacts", "candidates", "prospects"]
        )
        onboarding_target = (
            "onboarding queue" in normalized
            or "onboarding" in normalized
            or "prospect" in normalized
        )
        lookup_intent = any(
            term in normalized
            for term in ["find", "look up", "lookup", "show", "list", "who"]
        )
        return people_target and onboarding_target and lookup_intent

    @staticmethod
    def _member_info_lookup_target(request: str) -> str | None:
        match = re.search(
            r"\b(?:look\s*up|lookup|find|show)\s+"
            r"(?:(?:info|information|profile)\s+)?"
            r"(?:on|for|about)\s+(.+)$",
            request,
            re.IGNORECASE,
        )
        if match is None:
            return None
        target = re.sub(r"\s+", " ", match.group(1)).strip(" ?!.")
        if not target:
            return None
        return target[:80]

    @staticmethod
    def _member_lookup_command_message(target: str) -> str:
        normalized = target.casefold()
        if normalized in {"me", "myself", "self"}:
            return (
                "Use `/search-members query:me show_skills:true` for your private "
                "CRM profile view."
            )
        return (
            "Use `/search-members query:"
            f"{target}` for the private CRM member search result."
        )

    @staticmethod
    def _should_reply_publicly_to_mention(
        *,
        response: dict[str, Any],
        view: AgentConfirmationView | None,
    ) -> bool:
        if view is not None:
            return False
        status = str(response.get("status") or "").casefold()
        if status == "canceled":
            return True
        if status != "needs_clarification":
            return False
        return AgentCog._is_public_safe_clarification(response)

    @staticmethod
    def _is_public_safe_clarification(response: dict[str, Any]) -> bool:
        if response.get("plan") or response.get("results"):
            return False
        if response.get("error") or response.get("detail"):
            return False
        message = str(response.get("message") or "").strip()
        return message in _PUBLIC_SAFE_CLARIFICATION_MESSAGES

    async def _send_mention_public_response(
        self,
        *,
        message: discord.Message,
        request: str,
        content: str,
    ) -> None:
        thread = await self._mention_response_thread(message=message, request=request)
        if thread is not None:
            await thread.send(content[:1900])
            return
        await message.reply(content[:1900], mention_author=False)

    async def _mention_response_thread(
        self,
        *,
        message: discord.Message,
        request: str,
    ) -> Any | None:
        channel = getattr(message, "channel", None)
        if isinstance(channel, discord.Thread):
            return channel
        create_thread = getattr(message, "create_thread", None)
        if not callable(create_thread):
            return None
        create_thread = cast(Callable[..., Awaitable[Any]], create_thread)
        try:
            return await create_thread(
                name=self._mention_thread_name(request),
                auto_archive_duration=60,
            )
        except discord.HTTPException:
            logger.warning(
                "Failed creating agent mention response thread", exc_info=True
            )
            return None

    @staticmethod
    def _mention_thread_name(_request: str) -> str:
        return "Agent response"

    async def _send_mention_response_dm(
        self,
        message: discord.Message,
        response: dict[str, Any],
        view: AgentConfirmationView | None,
    ) -> bool:
        try:
            formatted_response = self._format_agent_response(response)
            if view is None:
                await message.author.send(formatted_response)
            else:
                await message.author.send(formatted_response, view=view)
            return True
        except discord.HTTPException:
            logger.warning(
                "Failed sending agent mention response by DM user=%s",
                getattr(message.author, "id", None),
                exc_info=True,
            )
            self._audit_message_safe(
                message=message,
                action="agent.mention.dm",
                result="error",
                metadata={"reason": "dm_failed"},
            )
            return False

    def _build_agent_context(self, interaction: discord.Interaction) -> dict[str, Any]:
        role_names = self._role_names_from_user(interaction.user)

        # Slash commands do not have a Discord message id; button interactions do.
        # Keep message_id as the visible Discord message when present and use
        # interaction_id as the stable fallback correlation id.
        interaction_message = getattr(interaction, "message", None)
        message_id = (
            str(interaction_message.id)
            if interaction_message is not None
            and getattr(interaction_message, "id", None) is not None
            else None
        )

        return {
            "discord_user_id": str(interaction.user.id),
            "operation_id": str(uuid4()),
            "internal_user_id": None,
            "organization_id": str(interaction.guild_id)
            if interaction.guild_id
            else None,
            "guild_id": str(interaction.guild_id) if interaction.guild_id else None,
            "channel_id": (
                str(interaction.channel_id)
                if interaction.channel_id is not None
                else None
            ),
            "thread_id": self._thread_id_from_channel(
                getattr(interaction, "channel", None)
            ),
            "parent_message_id": self._parent_message_id_from_channel(
                getattr(interaction, "channel", None)
            ),
            "response_destination_visibility": "private",
            "roles": role_names,
            "scopes": [],
            "impersonation": False,
            "interaction_id": str(interaction.id),
            "message_id": message_id,
        }

    def _build_agent_context_from_message(
        self,
        message: discord.Message,
    ) -> dict[str, Any]:
        guild_id = message.guild.id if message.guild is not None else None
        channel_id = getattr(message.channel, "id", None)
        return {
            "discord_user_id": str(message.author.id),
            "operation_id": str(uuid4()),
            "internal_user_id": None,
            "organization_id": str(guild_id) if guild_id else None,
            "guild_id": str(guild_id) if guild_id else None,
            "channel_id": str(channel_id) if channel_id is not None else None,
            "thread_id": self._thread_id_from_channel(message.channel),
            "parent_message_id": self._parent_message_id_from_channel(message.channel),
            "response_destination_visibility": (
                self._response_destination_visibility_from_message(message)
            ),
            "roles": self._role_names_from_user(message.author),
            "scopes": [],
            "impersonation": False,
            "interaction_id": None,
            "message_id": str(message.id),
        }

    @staticmethod
    def _thread_id_from_channel(channel: object) -> str | None:
        if isinstance(channel, discord.Thread):
            return str(channel.id)
        return None

    @staticmethod
    def _response_destination_visibility_from_message(
        _message: discord.Message,
    ) -> str:
        # Gateway-backed mention responses are sent by DM unless they are
        # fixed public-safe clarifications with no result payload.
        return "private"

    @staticmethod
    def _parent_message_id_from_channel(channel: object) -> str | None:
        if isinstance(channel, discord.Thread):
            return str(channel.id)
        reference = getattr(channel, "reference", None)
        reference_message_id = getattr(reference, "message_id", None)
        return str(reference_message_id) if reference_message_id is not None else None

    @staticmethod
    def _role_names_from_user(user: discord.abc.User) -> list[str]:
        roles = getattr(user, "roles", [])
        return [
            str(getattr(role, "name", "")).strip()
            for role in roles
            if str(getattr(role, "name", "")).strip()
        ]

    def _cached_guild_role_names(self, *, guild_id: str, user_id: int) -> list[str]:
        try:
            guild = self.bot.get_guild(int(guild_id))
        except (TypeError, ValueError):
            return []
        if guild is None:
            return []
        member = guild.get_member(user_id)
        if member is None:
            return []
        return self._role_names_from_user(member)

    async def _guild_role_names(self, *, guild_id: str, user_id: int) -> list[str]:
        try:
            guild = self.bot.get_guild(int(guild_id))
        except (TypeError, ValueError):
            return []
        if guild is None:
            return []
        member = guild.get_member(user_id)
        if member is None and hasattr(guild, "fetch_member"):
            try:
                member = await guild.fetch_member(user_id)
            except (discord.HTTPException, discord.NotFound, discord.Forbidden):
                member = None
        if member is None:
            return []
        return self._role_names_from_user(member)

    def _audit_message_safe(
        self,
        *,
        message: discord.Message,
        action: str,
        result: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        try:
            self.audit_logger.log_message(
                message=message,
                action=action,
                result=result,
                metadata=metadata,
            )
        except Exception:
            logger.warning("Audit logging failed for action=%s", action, exc_info=True)

    async def _post_agent_request(
        self,
        *,
        message: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {"message": message, "context": context}
        return await asyncio.to_thread(
            self._post_backend_json,
            "/agent/requests",
            payload,
        )

    async def _post_agent_confirmation(
        self,
        *,
        plan_id: str,
        context: dict[str, Any],
        confirm: bool,
    ) -> dict[str, Any]:
        payload = {"context": context, "confirm": confirm}
        return await asyncio.to_thread(
            self._post_backend_json,
            f"/agent/confirmations/{plan_id}",
            payload,
        )

    def _post_backend_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        base_url = settings.backend_api_base_url.rstrip("/")
        secret = str(settings.api_shared_secret or "").strip()
        if not base_url or not secret:
            raise RuntimeError("Backend API URL or API_SHARED_SECRET is not configured")

        response = requests.post(
            f"{base_url}{path}",
            headers={"X-API-Secret": secret},
            json=payload,
            timeout=settings.agent_api_timeout_seconds,
            verify=default_ca_bundle_path(),
        )
        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"Backend returned non-JSON status={response.status_code}"
            ) from exc
        if isinstance(data, dict):
            data.setdefault("http_status", response.status_code)
        if not isinstance(data, dict):
            raise RuntimeError("Backend returned unexpected JSON payload")
        return data

    @staticmethod
    def _audit_result_for_agent_response(response: dict[str, Any]) -> str:
        status = str(response.get("status") or "").strip().lower()
        if status in {"needs_clarification", "canceled"}:
            return "success"
        if status == "denied":
            return "denied"
        if status == "failed" or response.get("error"):
            http_status = response.get("http_status")
            if http_status in {401, 403}:
                return "denied"
            return "error"
        http_status = response.get("http_status")
        if http_status in {401, 403}:
            return "denied"
        if isinstance(http_status, int) and http_status >= 400:
            return "error"
        return "success"

    def _format_agent_response(self, response: dict[str, Any]) -> str:
        http_status = response.get("http_status")
        status = str(
            response.get("status")
            or (
                "error"
                if response.get("error")
                or (isinstance(http_status, int) and http_status >= 400)
                else "unknown"
            )
        )
        message = str(response.get("message") or "").strip()
        if (
            status == "needs_clarification"
            and message == _GENERIC_UNSUPPORTED_AGENT_MESSAGE
        ):
            return (
                "I could not map that to a supported workflow yet. Ask "
                "`what can you do?` for examples."
            )
        plan = response.get("plan") if isinstance(response.get("plan"), dict) else {}
        lines: list[str] = [f"Agent status: {status}"]
        if plan:
            planner = plan.get("planner")
            if planner:
                lines.append(f"Planner: {planner}")
            summary = str(plan.get("human_summary") or "").strip()
            if summary:
                lines.append("")
                lines.append("Planned actions:")
                lines.append(summary)
        if message:
            lines.append("")
            lines.append(message)
        error = str(response.get("error") or "").strip()
        if error:
            lines.append("")
            lines.append(f"Error: {error}")
        detail = str(response.get("detail") or "").strip()
        if detail:
            lines.append(f"Detail: {detail}")

        results = response.get("results")
        if isinstance(results, list) and results:
            lines.append("")
            lines.append("Results:")
            for result in results[:5]:
                if not isinstance(result, dict):
                    continue
                tool_name = result.get("tool_name")
                result_status = result.get("status")
                result_payload = result.get("result")
                if isinstance(result_payload, dict) and result_payload.get("task_id"):
                    lines.append(
                        f"- {tool_name}: {result_status} {result_payload['task_id']}"
                    )
                elif isinstance(result_payload, dict) and "tasks" in result_payload:
                    lines.append(
                        f"- {tool_name}: {len(result_payload.get('tasks') or [])} matches"
                    )
                elif isinstance(result_payload, dict) and "issues" in result_payload:
                    lines.extend(
                        self._format_issue_result_lines(tool_name, result_payload)
                    )
                elif isinstance(result_payload, dict) and result_payload.get(
                    "html_url"
                ):
                    lines.extend(
                        self._format_issue_result_lines(
                            tool_name, {"issues": [result_payload]}
                        )
                    )
                elif isinstance(result_payload, dict) and "contacts" in result_payload:
                    lines.extend(
                        self._format_contact_result_lines(tool_name, result_payload)
                    )
                elif isinstance(result_payload, dict) and "facts" in result_payload:
                    lines.extend(
                        self._format_memory_fact_result_lines(tool_name, result_payload)
                    )
                else:
                    result_error = str(result.get("error") or "").strip()
                    if result_error:
                        lines.append(f"- {tool_name}: {result_status} ({result_error})")
                    else:
                        lines.append(f"- {tool_name}: {result_status}")
                    recovery_email_error = self._result_recovery_email_error(
                        result_payload
                    )
                    if recovery_email_error:
                        lines.append(f"  Recovery email failed: {recovery_email_error}")

        return "\n".join(lines)[:1900]

    @staticmethod
    def _format_memory_fact_result_lines(
        tool_name: object,
        payload: dict[str, Any],
    ) -> list[str]:
        facts = payload.get("facts")
        if not isinstance(facts, list) or not facts:
            return [f"- {tool_name}: no visible remembered facts"]
        lines = [f"- {tool_name}: {len(facts)} remembered facts"]
        for fact in facts[:5]:
            if not isinstance(fact, dict):
                continue
            key = str(fact.get("key") or "memory").strip()
            value = AgentCog._format_memory_fact_value(fact.get("value_json"))
            if value:
                lines.append(f"  - {key}: {value}")
            else:
                lines.append(f"  - {key}")
        return lines

    @staticmethod
    def _format_memory_fact_value(value: object) -> str:
        if isinstance(value, dict):
            text = str(value.get("text") or "").strip()
            if text:
                return text
            return ", ".join(
                f"{key}: {item}"
                for key, item in value.items()
                if isinstance(key, str) and isinstance(item, str) and item.strip()
            )
        if isinstance(value, str):
            return value.strip()
        return ""

    @staticmethod
    def _result_recovery_email_error(payload: object) -> str | None:
        if not isinstance(payload, dict):
            return None
        direct_error = str(payload.get("recovery_email_error") or "").strip()
        if direct_error:
            return direct_error
        sso_payload = payload.get("sso")
        if isinstance(sso_payload, dict):
            nested_error = str(sso_payload.get("recovery_email_error") or "").strip()
            if nested_error:
                return nested_error
        return None

    @staticmethod
    def _format_issue_result_lines(
        tool_name: object,
        payload: dict[str, Any],
    ) -> list[str]:
        issues = payload.get("issues")
        issue_items = issues if isinstance(issues, list) else []
        lines = [f"- {tool_name}: {len(issue_items)} issues"]
        for issue in issue_items[:3]:
            if not isinstance(issue, dict):
                continue
            number = issue.get("number")
            title = str(issue.get("title") or "").strip()
            url = str(issue.get("html_url") or "").strip()
            label = f"  - #{number} {title}".strip()
            lines.append(f"{label} {url}".strip())
        return lines

    @staticmethod
    def _format_contact_result_lines(
        tool_name: object,
        payload: dict[str, Any],
    ) -> list[str]:
        contacts = payload.get("contacts")
        contact_items = contacts if isinstance(contacts, list) else []
        lines = [f"- {tool_name}: {len(contact_items)} contacts"]
        for contact in contact_items[:3]:
            if not isinstance(contact, dict):
                continue
            name = str(contact.get("name") or contact.get("id") or "Unknown").strip()
            email = str(contact.get("emailAddress") or "").strip()
            contact_id = str(contact.get("id") or "").strip()
            suffix = " ".join(part for part in [email, contact_id] if part)
            lines.append(f"  - {name} {suffix}".strip())
        return lines


async def setup(bot: commands.Bot) -> None:
    """Load the agent cog."""
    await bot.add_cog(AgentCog(bot))
