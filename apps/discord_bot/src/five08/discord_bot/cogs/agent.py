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

from five08.agent import AgentIdentityContext, PolicyEngine, ToolRuntimeConfig
from five08.discord_bot.config import settings
from five08.discord_bot.utils.audit import DiscordAuditCogMixin
from five08.tls import default_ca_bundle_path

logger = logging.getLogger(__name__)
_MENTION_RATE_LIMIT_WINDOW_SECONDS = 60.0
_MENTION_RATE_LIMIT_MAX_REQUESTS = 5
_PUBLIC_SAFE_CLARIFICATION_MESSAGES = frozenset(
    {
        "I could not map that to a supported workflow.",
        "Which project should I search?",
        "What should the task be?",
    }
)
_GENERIC_UNSUPPORTED_AGENT_MESSAGE = "I could not map that to a supported workflow."
_AGENT_RESPONSE_THREAD_NAME = "Agent response"
NO_MENTIONS = discord.AllowedMentions.none()
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
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Only the requester can confirm this agent plan.",
                ephemeral=True,
            )
            return False

        original_guild_id = str(
            self.context.get("guild_id") or self.context.get("organization_id") or ""
        ).strip()
        interaction_guild_id = getattr(interaction, "guild_id", None)
        if (
            not original_guild_id
            or (
                interaction_guild_id is not None
                and str(interaction_guild_id) != original_guild_id
            )
            or not self.cog._agent_guild_is_allowed(original_guild_id)
        ):
            await interaction.response.send_message(
                "This agent plan is not available in this Discord server.",
                ephemeral=True,
            )
            return False
        return True

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
            transport_failed = response.get("retryable_confirmation") is True
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
            transport_failed = response.get("retryable_confirmation") is True
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
        original_guild_id = str(
            self.context.get("guild_id") or self.context.get("organization_id") or ""
        ).strip()
        if original_guild_id:
            # Bind the confirmation to the tenant that produced the plan.  More
            # importantly, reload the guild member rather than trusting either
            # the interaction object or Discord.py's member cache: a role may
            # have been revoked during the confirmation window.
            context["organization_id"] = self.context.get("organization_id")
            context["guild_id"] = original_guild_id
            context["channel_id"] = self.context.get("channel_id")
            fresh_roles, fresh_role_ids = await self.cog._guild_role_snapshot(
                guild_id=original_guild_id,
                user_id=interaction.user.id,
                require_fresh=True,
            )
            # A confirmation may execute a write. If Discord cannot refresh
            # membership, fail closed rather than relying on roles captured
            # when the plan was first proposed.
            context["roles"] = fresh_roles
            context["role_ids"] = fresh_role_ids
        original_message_id = self.context.get("message_id")
        if original_message_id:
            context["message_id"] = original_message_id
        original_operation_id = self.context.get("operation_id")
        if original_operation_id:
            context["operation_id"] = original_operation_id
        return context


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

        guild_id = getattr(interaction, "guild_id", None)
        if not self._agent_guild_is_allowed(guild_id):
            await interaction.followup.send(
                "Agent workflows are not enabled for this Discord server.",
                ephemeral=True,
            )
            return

        local_response = self._local_agent_response(
            request=request,
            roles=self._role_names_from_user(interaction.user),
            role_ids=self._role_ids_from_user(interaction.user),
            guild_id=str(guild_id) if guild_id is not None else None,
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
        if not self._agent_guild_is_allowed(message.guild.id):
            self._audit_message_safe(
                message=message,
                action="agent.mention",
                result="denied",
                metadata={"reason": "guild_not_allowed"},
            )
            await message.reply(
                "Agent workflows are not enabled for this Discord server.",
                mention_author=False,
            )
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
            role_ids=self._role_ids_from_user(message.author),
            guild_id=str(message.guild.id),
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
        if getattr(channel, "owner_id", None) != bot_user_id:
            return False
        thread_name = str(getattr(channel, "name", "") or "").strip()
        return thread_name == _AGENT_RESPONSE_THREAD_NAME

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

    @staticmethod
    def _agent_guild_is_allowed(guild_id: object | None) -> bool:
        """Apply the configured Discord-guild boundary before dispatch."""

        normalized_guild_id = str(guild_id).strip() if guild_id is not None else None
        return PolicyEngine.from_settings(settings).guild_is_allowed(
            normalized_guild_id
        )

    def _local_agent_response(
        self,
        *,
        request: str,
        roles: list[str],
        role_ids: list[str],
        guild_id: str | None,
        transport: Literal["slash", "mention"],
    ) -> str | None:
        if self._is_agent_help_request(request):
            return self._agent_capabilities_message(
                roles=roles,
                role_ids=role_ids,
                guild_id=guild_id,
                transport=transport,
            )
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
        role_ids: list[str],
        guild_id: str | None,
        transport: Literal["slash", "mention"] = "mention",
    ) -> str:
        policy = PolicyEngine.from_settings(
            settings,
            runtime_config=ToolRuntimeConfig.from_settings(settings),
        )
        scopes = policy.scopes_for_context(
            AgentIdentityContext(
                discord_user_id="capability-preview",
                organization_id=guild_id,
                guild_id=guild_id,
                roles=roles,
                role_ids=role_ids,
            )
        )
        capabilities: list[str] = []
        if "project:read" in scopes:
            capabilities.append("- Tasks: search a project.")
        task_writes: list[str] = []
        if "task:create" in scopes:
            task_writes.append("create tasks")
        if "task:update_own" in scopes:
            task_writes.append("update your own tasks")
        if task_writes:
            capabilities.append(f"- Task writes: {', and '.join(task_writes)}.")
        if {"memory:read_self", "memory:write_self"} & scopes:
            capabilities.append(
                "- Memory: remember and review your private preferences."
            )
        if "agent:chat" in scopes:
            capabilities.append(
                "- Agent chat: answer general questions and plan approved work."
            )
        if "web:research" in scopes:
            capabilities.append(
                "- Public web: research current information and read public pages."
            )
        if "billing:invoice:read" in scopes:
            capabilities.append(
                "- Billing: search Sales/Purchase invoices and suppliers (read-only)."
            )
        if "erp:project:read" in scopes:
            capabilities.append(
                "- ERP projects: search projects and view read-only summaries."
            )
        if {
            "github:issue:read",
            "github:repository:configured:read",
            "github:repository:all:read",
        } & scopes:
            capabilities.append(
                "- GitHub issues: look up, create, update, and comment on todos."
            )
        if "github:project:read" in scopes:
            capabilities.append(
                "- GitHub Projects: inspect boards and manage their items."
            )
        if "crm:contact:read" in scopes:
            capabilities.extend(
                [
                    "- CRM: search contacts, approve/reject onboarding, and submit member agreements.",
                ]
            )
        if "user:manage" in scopes or "mailbox:create" in scopes:
            capabilities.append(
                "- Ops: create 508 accounts, Authentik SSO users, Outline invites, and mailboxes."
            )
        if "agent:schedule:manage" in scopes:
            capabilities.append(
                "- Recurring reports: ask me to schedule a read-only report in this channel; I will show the exact timing before creating it."
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
            await thread.send(content[:1900], allowed_mentions=NO_MENTIONS)
            return
        await message.reply(
            content[:1900],
            mention_author=False,
            allowed_mentions=NO_MENTIONS,
        )

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
        return _AGENT_RESPONSE_THREAD_NAME

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
        role_ids = self._role_ids_from_user(interaction.user)

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
            "role_ids": role_ids,
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
            "role_ids": self._role_ids_from_user(message.author),
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

    @staticmethod
    def _role_ids_from_user(user: discord.abc.User) -> list[str]:
        """Return de-duplicated Discord role snowflakes from a member object."""

        role_ids: list[str] = []
        for role in getattr(user, "roles", []):
            role_id = str(getattr(role, "id", "")).strip()
            if not role_id or not role_id.isdecimal() or int(role_id) <= 0:
                continue
            if role_id not in role_ids:
                role_ids.append(role_id)
        return role_ids

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
        role_names, _role_ids = await self._guild_role_snapshot(
            guild_id=guild_id,
            user_id=user_id,
        )
        return role_names

    async def _guild_role_snapshot(
        self,
        *,
        guild_id: str,
        user_id: int,
        require_fresh: bool = False,
    ) -> tuple[list[str], list[str]]:
        """Return role names and IDs from a guild member snapshot.

        Confirmation reauthorization requests a REST snapshot explicitly, so
        revocations cannot be masked by Discord.py's member cache.
        """

        try:
            guild = self.bot.get_guild(int(guild_id))
        except (TypeError, ValueError):
            return [], []
        if guild is None:
            return [], []
        fetch_member = getattr(guild, "fetch_member", None)
        if require_fresh:
            if not callable(fetch_member):
                logger.warning(
                    "Cannot refresh Discord member roles for agent confirmation: "
                    "guild does not support fetch_member"
                )
                return [], []
            fetch_member_call = cast(
                Callable[[int], Awaitable[discord.Member]], fetch_member
            )
            try:
                member = await fetch_member_call(user_id)
            except discord.NotFound:
                # A 404 is definitive evidence that the requester is no
                # longer a member, so submit an empty snapshot and let the
                # backend deny and consume the confirmation.
                return [], []
            except Exception as exc:
                logger.warning(
                    "Failed refreshing Discord member roles for agent confirmation",
                    exc_info=True,
                )
                # HTTP 5xx/429/permission and network failures cannot prove
                # that access was revoked. Abort before the backend atomically
                # consumes the pending confirmation so the requester can retry.
                raise RuntimeError(
                    "Discord membership could not be refreshed; try again."
                ) from exc
            return self._role_names_from_user(member), self._role_ids_from_user(member)

        member = guild.get_member(user_id)
        if member is None and callable(fetch_member):
            fetch_member_call = cast(
                Callable[[int], Awaitable[discord.Member]], fetch_member
            )
            try:
                member = await fetch_member_call(user_id)
            except (discord.HTTPException, discord.NotFound, discord.Forbidden):
                member = None
        if member is None:
            return [], []
        return self._role_names_from_user(member), self._role_ids_from_user(member)

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
                elif tool_name in {
                    "billing_read.search_invoices",
                    "billing_read.get_invoice_summary",
                    "billing_read.search_suppliers",
                    "erp_read.search_projects",
                    "erp_read.get_project_summary",
                } and isinstance(result_payload, dict):
                    lines.extend(
                        self._format_erp_read_result_lines(tool_name, result_payload)
                    )
                elif isinstance(result_payload, dict) and "facts" in result_payload:
                    lines.extend(
                        self._format_memory_fact_result_lines(tool_name, result_payload)
                    )
                elif (
                    tool_name == "agent_schedule.create"
                    and isinstance(result_payload, dict)
                    and result_payload.get("schedule_id")
                ):
                    schedule_id = str(result_payload["schedule_id"])
                    next_run = str(result_payload.get("next_run_at") or "unknown")
                    lines.append(
                        f"- agent_schedule.create: created `{schedule_id}` (next: {next_run})"
                    )
                elif (
                    tool_name == "web_read.search"
                    and isinstance(result_payload, dict)
                    and "results" in result_payload
                ):
                    lines.extend(self._format_web_search_result_lines(result_payload))
                elif (
                    tool_name == "web_read.extract"
                    and isinstance(result_payload, dict)
                    and "content" in result_payload
                ):
                    lines.extend(self._format_web_extract_result_lines(result_payload))
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
    def _format_web_search_result_lines(payload: dict[str, Any]) -> list[str]:
        items = payload.get("results")
        results = items if isinstance(items, list) else []
        provider = str(payload.get("provider") or "web").strip()
        lines = [f"- web_read.search ({provider}): {len(results)} results"]
        for item in results[:3]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "Untitled result").strip()
            url = str(item.get("url") or "").strip()
            snippet = " ".join(str(item.get("snippet") or "").split())
            lines.append(f"  - {title} {url}".strip())
            if snippet:
                lines.append(f"    {snippet[:240]}")
        return lines

    @staticmethod
    def _format_web_extract_result_lines(payload: dict[str, Any]) -> list[str]:
        provider = str(payload.get("provider") or "firecrawl").strip()
        title = str(payload.get("title") or "Public web page").strip()
        url = str(payload.get("url") or "").strip()
        content = " ".join(str(payload.get("content") or "").split())
        lines = [f"- web_read.extract ({provider}): {title} {url}".strip()]
        if content:
            lines.append(f"  {content[:600]}")
        return lines

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

    @staticmethod
    def _format_erp_read_result_lines(
        tool_name: object,
        payload: dict[str, Any],
    ) -> list[str]:
        if "invoices" in payload:
            raw_invoices = payload.get("invoices")
            invoices = raw_invoices if isinstance(raw_invoices, list) else []
            lines = [f"- {tool_name}: {len(invoices)} invoices"]
            for invoice in invoices[:5]:
                if not isinstance(invoice, dict):
                    continue
                invoice_id = str(invoice.get("invoice_id") or "Unknown invoice").strip()
                status = str(invoice.get("status") or "unknown").strip()
                posting_date = str(invoice.get("posting_date") or "").strip()
                lines.append(
                    "  - "
                    + " · ".join(
                        part for part in [invoice_id, status, posting_date] if part
                    )
                )
            return lines

        if "invoice" in payload:
            invoice = payload.get("invoice")
            if not isinstance(invoice, dict):
                return [f"- {tool_name}: no matching invoice"]
            invoice_id = str(invoice.get("invoice_id") or "Unknown invoice").strip()
            status = str(invoice.get("status") or "unknown").strip()
            party = str(
                invoice.get("customer") or invoice.get("supplier") or ""
            ).strip()
            currency = str(invoice.get("currency") or "").strip()
            total = invoice.get("grand_total")
            suffix = " ".join(
                str(part).strip()
                for part in (currency, total)
                if part is not None and str(part).strip()
            )
            lines = [
                "  - "
                + " · ".join(
                    part for part in [invoice_id, status, party, suffix] if part
                )
            ]
            return [f"- {tool_name}: invoice summary", *lines]

        if "suppliers" in payload:
            raw_suppliers = payload.get("suppliers")
            suppliers = raw_suppliers if isinstance(raw_suppliers, list) else []
            lines = [f"- {tool_name}: {len(suppliers)} suppliers"]
            for supplier in suppliers[:5]:
                if not isinstance(supplier, dict):
                    continue
                supplier_id = str(supplier.get("supplier_id") or "").strip()
                name = str(
                    supplier.get("supplier_name") or supplier_id or "Unknown supplier"
                ).strip()
                email = str(supplier.get("email") or "").strip()
                lines.append(
                    "  - " + " · ".join(part for part in [name, email] if part)
                )
            return lines

        if "projects" in payload:
            raw_projects = payload.get("projects")
            projects = raw_projects if isinstance(raw_projects, list) else []
            lines = [f"- {tool_name}: {len(projects)} ERP projects"]
            for project in projects[:5]:
                if not isinstance(project, dict):
                    continue
                project_id = str(project.get("project_id") or "Unknown project").strip()
                name = str(project.get("project_name") or "").strip()
                status = str(project.get("status") or "").strip()
                lines.append(
                    "  - "
                    + " · ".join(part for part in [project_id, name, status] if part)
                )
            return lines

        if "project" in payload:
            project = payload.get("project")
            if not isinstance(project, dict):
                return [f"- {tool_name}: no matching ERP project"]
            project_id = str(project.get("project_id") or "Unknown project").strip()
            name = str(project.get("project_name") or "").strip()
            status = str(project.get("status") or "").strip()
            customer = str(project.get("customer") or "").strip()
            return [
                f"- {tool_name}: ERP project summary",
                "  - "
                + " · ".join(
                    part for part in [project_id, name, status, customer] if part
                ),
            ]

        return [f"- {tool_name}: no read-only ERP results"]


async def setup(bot: commands.Bot) -> None:
    """Load the agent cog."""
    await bot.add_cog(AgentCog(bot))
