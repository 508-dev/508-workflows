"""Discord gateway for the backend agent orchestrator."""

from __future__ import annotations

import asyncio
import difflib
import logging
import re
import time
from collections.abc import Mapping
from typing import Any, Awaitable, Callable, Literal, cast
from uuid import uuid4

import discord
import requests
from discord import app_commands
from discord.ext import commands

from five08.agent import AgentIdentityContext, PolicyEngine, ToolRuntimeConfig
from five08.discord_bot.config import settings
from five08.discord_bot.utils.audit import DiscordAuditCogMixin
from five08.discord_bot.utils.knowledge_context import (
    collect_discord_sources,
    collect_thread_context,
)
from five08.discord_bot.utils.memory_views import MemoryFactsView
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
_KNOWLEDGE_CAPTURE_RE = re.compile(
    r"\b(?:remember|save)\s+(?:this|the)\s+"
    r"(?:thread|conversation|answer|discussion)\b",
    re.I,
)
_KNOWLEDGE_QUESTION_RE = re.compile(
    r"^(?:i\s+(?:forgot|forget)[,;:]?\s*)?"
    r"(?:who|what|where|when|why|how|does|do|did|is|are|can|could|has|have)\b",
    re.I,
)
_KNOWLEDGE_RECALL_PREFIX_RE = re.compile(
    r"^(?:i\s+(?:forgot|forget)[,;:]?\s*|remind\s+me\s+)"
    r"(?:who|what|where|when|why|how|does|do|did|is|are|can|could|has|have)\b",
    re.I,
)
_AGENT_ACTION_RE = re.compile(
    r"\b(?:add|approve|assign|cancel|create|delete|forget|invite|post|reject|"
    r"remember|remove|save|send|submit|sync|update)\b",
    re.I,
)
_AGENT_LIVE_WORKFLOW_RE = re.compile(
    r"\b(?:github\s+(?:issues?|projects?|repositor(?:y|ies)|repos?)|tasks?|"
    r"onboarding\s+queue|unlinked\s+(?:discord\s+)?members?)\b",
    re.I,
)
_ORGANIZATION_AUDIENCE_ROLE_NAMES = frozenset(
    {
        "admin",
        "engineer",
        "member",
        "owner",
        "project manager",
        "project_manager",
        "steering committee",
        "workflows engineer",
    }
)
_KNOWLEDGE_CAPTURE_ACTIONS = ("remember", "save")
_KNOWLEDGE_CAPTURE_TARGETS = ("thread", "conversation", "answer", "discussion")
_KNOWLEDGE_CAPTURE_COMPONENT_RE = re.compile(
    r"^knowledge:capture:(?P<action>[cx]):"
    r"(?P<draft_id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}):"
    r"(?P<guild_id>\d+):(?P<channel_id>\d+)$"
)


def _knowledge_capture_component_id(
    *,
    action: Literal["c", "x"],
    draft_id: str,
    guild_id: str,
    channel_id: str,
) -> str:
    custom_id = f"knowledge:capture:{action}:{draft_id}:{guild_id}:{channel_id}"
    if len(custom_id) > 100:
        raise ValueError("Knowledge capture component ID exceeds Discord's limit")
    return custom_id


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
            transport_failed = bool(response.get("retryable"))
        except Exception as exc:
            logger.warning("Agent confirmation request failed: %s", exc)
            response = {
                "status": "failed",
                "message": "The agent service could not be reached. Try again.",
            }
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
            transport_failed = bool(response.get("retryable"))
        except Exception as exc:
            logger.warning("Agent cancellation request failed: %s", exc)
            response = {
                "status": "failed",
                "message": "The agent service could not be reached. Try again.",
            }
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
            if fresh_roles is None:
                raise RuntimeError("Discord role refresh unavailable")
            context["roles"] = fresh_roles
        original_message_id = self.context.get("message_id")
        if original_message_id:
            context["message_id"] = original_message_id
        original_operation_id = self.context.get("operation_id")
        if original_operation_id:
            context["operation_id"] = original_operation_id
        return context


class KnowledgeCaptureDynamicButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=_KNOWLEDGE_CAPTURE_COMPONENT_RE,
):
    """Restart-safe dispatcher for persisted knowledge capture controls."""

    def __init__(
        self,
        *,
        action: Literal["c", "x"],
        draft_id: str,
        guild_id: str,
        channel_id: str,
    ) -> None:
        self.action = action
        self.draft_id = draft_id
        self.guild_id = guild_id
        self.channel_id = channel_id
        super().__init__(
            discord.ui.Button(
                label="Remember" if action == "c" else "Cancel",
                style=(
                    discord.ButtonStyle.primary
                    if action == "c"
                    else discord.ButtonStyle.secondary
                ),
                custom_id=_knowledge_capture_component_id(
                    action=action,
                    draft_id=draft_id,
                    guild_id=guild_id,
                    channel_id=channel_id,
                ),
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> "KnowledgeCaptureDynamicButton":
        del interaction, item
        return cls(
            action=cast(Literal["c", "x"], match["action"]),
            draft_id=match["draft_id"],
            guild_id=match["guild_id"],
            channel_id=match["channel_id"],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        get_cog = getattr(interaction.client, "get_cog", None)
        cog = get_cog("AgentCog") if callable(get_cog) else None
        if not isinstance(cog, AgentCog):
            await interaction.response.send_message(
                "Knowledge confirmation is temporarily unavailable. Try again.",
                ephemeral=True,
            )
            return
        view = KnowledgeCaptureView(
            cog=cog,
            requester_id=interaction.user.id,
            draft_id=self.draft_id,
            context={
                "organization_id": self.guild_id,
                "guild_id": self.guild_id,
                "channel_id": self.channel_id,
            },
        )
        await view._finish(interaction, confirm=self.action == "c")


class KnowledgeCaptureView(discord.ui.View):
    """Confirmation controls for one frozen backend knowledge draft."""

    def __init__(
        self,
        *,
        cog: "AgentCog",
        requester_id: int,
        draft_id: str,
        context: dict[str, Any],
    ) -> None:
        super().__init__(timeout=settings.knowledge_capture_draft_ttl_seconds)
        self.cog = cog
        self.requester_id = requester_id
        self.draft_id = draft_id
        self.context = context
        guild_id = str(context.get("guild_id") or context.get("organization_id") or "0")
        channel_id = str(context.get("channel_id") or "0")
        for item in self.children:
            if not isinstance(item, discord.ui.Button):
                continue
            action: Literal["c", "x"] = "c" if item.label == "Remember" else "x"
            item.custom_id = _knowledge_capture_component_id(
                action=action,
                draft_id=draft_id,
                guild_id=guild_id,
                channel_id=channel_id,
            )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.requester_id:
            return True
        await interaction.response.send_message(
            "Only the requester can confirm this knowledge capture.",
            ephemeral=True,
        )
        return False

    def _disable(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        self.stop()

    @discord.ui.button(
        label="Remember",
        style=discord.ButtonStyle.primary,
        custom_id="knowledge:capture:c:00000000-0000-0000-0000-000000000000:0:0",
    )
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["KnowledgeCaptureView"],
    ) -> None:
        await self._finish(interaction, confirm=True)

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.secondary,
        custom_id="knowledge:capture:x:00000000-0000-0000-0000-000000000000:0:0",
    )
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["KnowledgeCaptureView"],
    ) -> None:
        await self._finish(interaction, confirm=False)

    async def _finish(
        self,
        interaction: discord.Interaction,
        *,
        confirm: bool,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            context = await self._confirmation_context(interaction)
            response = await self.cog._post_knowledge_confirmation(
                draft_id=self.draft_id,
                context=context,
                confirm=confirm,
            )
            http_status = response.get("http_status")
            transport_failed = isinstance(http_status, int) and http_status >= 500
        except Exception:
            logger.warning("Knowledge capture confirmation failed", exc_info=True)
            response = {
                "status": "failed",
                "message": "The knowledge service could not be reached. Try again.",
            }
            transport_failed = True
        self.cog._audit_command_safe(
            interaction=interaction,
            action=(
                "knowledge.capture.confirm" if confirm else "knowledge.capture.cancel"
            ),
            result=AgentCog._audit_result_for_agent_response(response),
            metadata={
                "draft_id": self.draft_id,
                "status": response.get("status"),
                "error": response.get("error"),
            },
        )
        if not transport_failed:
            self._disable()
        await interaction.followup.send(
            self.cog._format_knowledge_capture_response(response),
            ephemeral=True,
        )
        if not transport_failed and interaction.message is not None:
            try:
                await interaction.message.edit(view=self)
            except discord.HTTPException:
                logger.warning(
                    "Failed disabling knowledge confirmation view",
                    exc_info=True,
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
            context["thread_id"] = self.context.get("thread_id")
            fresh_roles = await self.cog._guild_role_names(
                guild_id=str(original_guild_id),
                user_id=interaction.user.id,
            )
            if fresh_roles is None:
                raise RuntimeError("Discord role refresh unavailable")
            context["roles"] = fresh_roles
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

        view: discord.ui.View | None = self._memory_view(
            response, interaction.user.id, context
        )
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

    @app_commands.command(
        name="ask",
        description="Ask Outline, selected Discord channels, and remembered knowledge",
    )
    @app_commands.describe(question="The organizational question to answer")
    async def ask_command(
        self,
        interaction: discord.Interaction,
        question: str,
    ) -> None:
        """Ask the backend knowledge service and keep the result private."""
        await interaction.response.defer(ephemeral=True)
        context = self._build_agent_context(interaction)
        try:
            response = await self._post_knowledge_query(
                question=question,
                context=context,
            )
        except Exception:
            logger.warning("Knowledge query request failed", exc_info=True)
            self._audit_command_safe(
                interaction=interaction,
                action="knowledge.query",
                result="error",
                metadata={"reason": "transport_failed"},
            )
            await interaction.followup.send(
                "The knowledge service could not be reached. Try again shortly.",
                ephemeral=True,
            )
            return

        self._audit_command_safe(
            interaction=interaction,
            action="knowledge.query",
            result=self._audit_result_for_agent_response(response),
            metadata={
                "status": response.get("status"),
                "citation_count": len(response.get("citations") or []),
                "error": response.get("error"),
            },
        )
        await interaction.followup.send(
            self._format_knowledge_query_response(response),
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

        if self._is_knowledge_capture_request(request):
            await self._handle_knowledge_capture_mention(
                message=message,
                request=request,
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
        if self._is_knowledge_question(request):
            await self._handle_knowledge_question_mention(
                message=message,
                question=request,
                context=context,
            )
            return
        context["context_snippets"] = await collect_thread_context(message)
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

        view: discord.ui.View | None = self._memory_view(
            response, message.author.id, context
        )
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
    def _is_knowledge_capture_request(request: str) -> bool:
        if re.match(
            r"(?i)^(?:do|did|what|how|why|where|when|can|could)\b", request.strip()
        ):
            return False
        if re.fullmatch(
            r"(?i)suggest (?:facts|memories)(?: worth saving)? from (?:this|the) (?:thread|conversation)",
            request.strip().rstrip(".!"),
        ):
            return True
        if _KNOWLEDGE_CAPTURE_RE.search(request) is not None:
            return True
        tokens = re.findall(r"[a-z]+", request.casefold())
        if len(tokens) < 2:
            return False
        has_action = any(
            difflib.get_close_matches(
                token,
                _KNOWLEDGE_CAPTURE_ACTIONS,
                n=1,
                cutoff=0.78,
            )
            for token in tokens[:4]
        )
        has_target = any(
            difflib.get_close_matches(
                token,
                _KNOWLEDGE_CAPTURE_TARGETS,
                n=1,
                cutoff=0.72,
            )
            for token in tokens
        )
        return has_action and has_target

    @staticmethod
    def _is_knowledge_question(request: str) -> bool:
        normalized = re.sub(r"\s+", " ", request).strip()
        if not normalized:
            return False
        if re.search(r"\bremember\b", normalized, re.I) and re.search(
            r"\b(?:me|my|mine)\b",
            normalized,
            re.I,
        ):
            return False
        if _AGENT_LIVE_WORKFLOW_RE.search(normalized) is not None:
            return False
        if _KNOWLEDGE_RECALL_PREFIX_RE.search(normalized) is not None:
            return True
        if re.match(r"(?i)^(?:do|did|what|how|where|when)\b.*\bremember\b", normalized):
            return True
        if _AGENT_ACTION_RE.search(normalized) is not None:
            return False
        return (
            normalized.endswith("?")
            or _KNOWLEDGE_QUESTION_RE.search(normalized) is not None
        )

    async def _handle_knowledge_capture_mention(
        self,
        *,
        message: discord.Message,
        request: str,
    ) -> None:
        context = self._build_agent_context_from_message(message)
        messages = await self._knowledge_capture_messages(message)
        if not messages:
            self._audit_message_safe(
                message=message,
                action="knowledge.capture",
                result="error",
                metadata={"reason": "no_reply_or_thread_context"},
            )
            await message.reply(
                "Reply to the answer you want saved, or use this inside its thread.",
                mention_author=False,
            )
            return
        payload = {
            "context": context,
            "source": self._knowledge_source_payload(message),
            "messages": messages,
            "requested_visibility": (
                "project"
                if re.search(r"\bfor (?:the )?project\b", request, re.I)
                else "org"
                if re.search(
                    r"\bfor (?:the )?(?:team|everyone|organization)\b", request, re.I
                )
                else "private"
            ),
        }
        try:
            async with message.channel.typing():
                response = await self._post_knowledge_capture(payload)
        except Exception:
            logger.warning("Knowledge capture request failed", exc_info=True)
            self._audit_message_safe(
                message=message,
                action="knowledge.capture",
                result="error",
                metadata={"reason": "transport_failed"},
            )
            await message.reply(
                "The knowledge service could not be reached. Try again shortly.",
                mention_author=False,
            )
            return

        result = self._audit_result_for_agent_response(response)
        if result != "success":
            self._audit_message_safe(
                message=message,
                action="knowledge.capture",
                result=result,
                metadata={
                    "status": response.get("status"),
                    "error": response.get("error"),
                },
            )
        view: KnowledgeCaptureView | None = None
        draft_id = str(response.get("draft_id") or "")
        if response.get("status") == "requires_confirmation" and draft_id:
            view = KnowledgeCaptureView(
                cog=self,
                requester_id=message.author.id,
                draft_id=draft_id,
                context=context,
            )

        try:
            preview_parts = self._format_knowledge_capture_preview_parts(response)
            for preview_part in preview_parts[:-1]:
                await message.author.send(preview_part)
            final_part = preview_parts[-1]
            if view is None:
                await message.author.send(final_part)
            else:
                await message.author.send(final_part, view=view)
        except discord.HTTPException:
            logger.warning(
                "Failed sending knowledge capture preview by DM user=%s",
                getattr(message.author, "id", None),
                exc_info=True,
            )
            await message.reply(
                "I couldn't DM the private capture preview. Enable DMs and try again.",
                mention_author=False,
            )
            return

        if view is not None:
            acknowledgement = (
                "I sent a capture preview by DM. Nothing is saved until you confirm."
            )
        else:
            acknowledgement = "I sent the knowledge capture result by DM."
        await message.reply(acknowledgement, mention_author=False)

    async def _handle_knowledge_question_mention(
        self,
        *,
        message: discord.Message,
        question: str,
        context: dict[str, Any],
    ) -> None:
        try:
            async with message.channel.typing():
                response = await self._post_knowledge_query(
                    question=question,
                    context=context,
                )
        except Exception:
            logger.warning("Knowledge query request failed", exc_info=True)
            self._audit_message_safe(
                message=message,
                action="knowledge.query",
                result="error",
                metadata={"reason": "transport_failed"},
            )
            await message.reply(
                "The knowledge service could not be reached. Try again shortly.",
                mention_author=False,
            )
            return

        result = self._audit_result_for_agent_response(response)
        if result != "success":
            self._audit_message_safe(
                message=message,
                action="knowledge.query",
                result=result,
                metadata={
                    "status": response.get("status"),
                    "error": response.get("error"),
                },
            )
        formatted = self._format_knowledge_query_response(response)
        if (
            response.get("status") == "answered"
            and response.get("public_safe")
            and self._discord_destination_is_org_only(message)
        ):
            await self._send_mention_public_response(
                message=message,
                request=question,
                content=formatted,
            )
            return
        try:
            await message.author.send(formatted)
        except discord.HTTPException:
            logger.warning(
                "Failed sending knowledge answer by DM user=%s",
                getattr(message.author, "id", None),
                exc_info=True,
            )
            await message.reply(
                "I found a private result but couldn't DM you. Use `/ask` instead.",
                mention_author=False,
            )
            return
        await message.reply(
            "I sent the knowledge answer by DM.",
            mention_author=False,
        )

    async def _knowledge_capture_messages(
        self,
        trigger: discord.Message,
    ) -> list[dict[str, Any]]:
        source_messages: list[Any] = []
        if isinstance(trigger.channel, discord.Thread):
            try:
                async for source_message in trigger.channel.history(
                    limit=settings.knowledge_capture_max_messages,
                    oldest_first=False,
                ):
                    source_messages.append(source_message)
            except (discord.Forbidden, discord.HTTPException):
                logger.warning(
                    "Failed reading Discord thread for capture", exc_info=True
                )
                return []
        else:
            answer = await self._referenced_message(trigger)
            if answer is None:
                return []
            question = await self._referenced_message(answer)
            if question is not None:
                source_messages.append(question)
            source_messages.append(answer)

        trigger_id = str(trigger.id)
        serialized = [
            payload
            for source_message in source_messages
            if str(getattr(source_message, "id", "")) != trigger_id
            and (payload := self._serialize_knowledge_message(source_message))
            is not None
        ]
        return serialized[: settings.knowledge_capture_max_messages]

    async def _referenced_message(self, message: Any) -> Any | None:
        reference = getattr(message, "reference", None)
        if reference is None:
            return None
        resolved = getattr(reference, "resolved", None)
        if resolved is not None and hasattr(resolved, "content"):
            return resolved
        message_id = getattr(reference, "message_id", None)
        fetch_message = getattr(message.channel, "fetch_message", None)
        if message_id is None or not callable(fetch_message):
            return None
        fetch_message = cast(Callable[[int], Awaitable[Any]], fetch_message)
        try:
            return await fetch_message(message_id)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            return None

    @staticmethod
    def _serialize_knowledge_message(message: Any) -> dict[str, Any] | None:
        content = str(getattr(message, "content", "") or "").strip()
        author = getattr(message, "author", None)
        created_at = getattr(message, "created_at", None)
        if not content or author is None or created_at is None:
            return None
        author_name = str(
            getattr(author, "display_name", None)
            or getattr(author, "name", None)
            or getattr(author, "id", "Unknown")
        )
        return {
            "message_id": str(message.id),
            "author_id": str(author.id),
            "author_name": author_name[:128],
            "content": content[:4096],
            "created_at": created_at.isoformat(),
            "jump_url": str(getattr(message, "jump_url", "") or "") or None,
            "author_is_bot": bool(getattr(author, "bot", False)),
        }

    def _knowledge_source_payload(self, message: discord.Message) -> dict[str, Any]:
        guild_id = str(message.guild.id) if message.guild is not None else ""
        channel_id = str(getattr(message.channel, "id", ""))
        is_thread = isinstance(message.channel, discord.Thread)
        source_ref = str(getattr(message, "jump_url", "") or "")
        if not source_ref:
            source_ref = f"discord:{guild_id}:{channel_id}:{message.id}"
        title = str(getattr(message.channel, "name", "Discord conversation") or "")
        return {
            "source_type": "discord_thread" if is_thread else "discord_message",
            "source_ref": source_ref,
            "title": title[:256] or "Discord conversation",
            "guild_id": guild_id,
            "channel_id": channel_id,
            "thread_id": channel_id if is_thread else None,
            "source_visibility": self._discord_source_visibility(message),
        }

    @staticmethod
    def _discord_source_visibility(message: discord.Message) -> str:
        return (
            "org" if AgentCog._discord_destination_is_org_only(message) else "private"
        )

    @staticmethod
    def _discord_destination_is_org_only(message: discord.Message) -> bool:
        """Return whether every role that can view the channel is organizational."""
        guild = message.guild
        channel = message.channel
        if guild is None:
            return False
        is_private = getattr(channel, "is_private", None)
        if callable(is_private) and is_private():
            return False
        permissions_for = getattr(channel, "permissions_for", None)
        if not callable(permissions_for):
            return False

        # Role checks do not reveal a guest granted access through an explicit
        # member overwrite. Treat any such grant as non-organizational.
        overwrite_source = getattr(channel, "parent", None) or channel
        overwrites = getattr(overwrite_source, "overwrites", None)
        if isinstance(overwrites, Mapping):
            role_ids = {
                getattr(role, "id", None) for role in getattr(guild, "roles", [])
            }
            bot_member_id = getattr(getattr(guild, "me", None), "id", None)
            for target, overwrite in overwrites.items():
                if getattr(overwrite, "view_channel", None) is not True:
                    continue
                target_id = getattr(target, "id", None)
                is_bot_member = bot_member_id is not None and target_id == bot_member_id
                if target_id not in role_ids and not is_bot_member:
                    return False

        member_can_view = False
        for role in getattr(guild, "roles", []):
            role_name = str(getattr(role, "name", "") or "").strip().casefold()
            try:
                can_view = bool(getattr(permissions_for(role), "view_channel", False))
            except (AttributeError, TypeError):
                return False
            if not can_view:
                continue
            if role_name == "member":
                member_can_view = True
            if role_name not in _ORGANIZATION_AUDIENCE_ROLE_NAMES:
                return False
        return member_can_view

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
        policy = PolicyEngine.from_runtime_config(
            ToolRuntimeConfig.from_settings(settings)
        )
        scopes = policy.scopes_for_context(
            AgentIdentityContext(
                discord_user_id="capability-preview",
                organization_id="capability-preview",
                roles=roles,
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
        if "knowledge:read_org" in scopes:
            capabilities.append(
                "- Knowledge: ask source-grounded questions across remembered answers and the wiki."
            )
        if "knowledge:capture_org" in scopes:
            capabilities.append(
                "- Capture: tag me with `remember this thread` to review and save an answer."
            )
        if {
            "github:repository:member:read",
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
        view: discord.ui.View | None,
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
        return _AGENT_RESPONSE_THREAD_NAME

    async def _send_mention_response_dm(
        self,
        message: discord.Message,
        response: dict[str, Any],
        view: discord.ui.View | None,
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

    async def _guild_role_names(
        self, *, guild_id: str, user_id: int
    ) -> list[str] | None:
        try:
            guild = self.bot.get_guild(int(guild_id))
        except (TypeError, ValueError):
            return None
        if guild is None:
            return None
        try:
            async with asyncio.timeout(3):
                member = await guild.fetch_member(user_id)
        except discord.NotFound:
            return []
        except (TimeoutError, discord.HTTPException):
            return None
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

    async def _post_knowledge_capture(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._post_backend_json,
            "/knowledge/captures",
            payload,
            settings.knowledge_api_timeout_seconds,
        )

    async def _post_knowledge_confirmation(
        self,
        *,
        draft_id: str,
        context: dict[str, Any],
        confirm: bool,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._post_backend_json,
            f"/knowledge/captures/{draft_id}/confirmation",
            {"context": context, "confirm": confirm},
            settings.knowledge_api_timeout_seconds,
        )

    async def _post_knowledge_query(
        self,
        *,
        question: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        sources: list[dict[str, Any]] = []
        source_errors: list[str] = []
        if settings.knowledge_enabled:
            sources, source_errors = await collect_discord_sources(
                self.bot, settings, context, question
            )
        payload: dict[str, Any] = {"question": question, "context": context}
        if sources:
            payload["discord_sources"] = sources
        if source_errors:
            payload["source_errors"] = source_errors
        return await asyncio.to_thread(
            self._post_backend_json,
            "/knowledge/queries",
            payload,
            max(
                settings.knowledge_api_timeout_seconds,
                settings.knowledge_source_timeout_seconds
                + settings.knowledge_model_timeout_seconds
                + 1.0,
            ),
        )

    def _post_backend_json(
        self,
        path: str,
        payload: dict[str, Any],
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        base_url = settings.backend_api_base_url.rstrip("/")
        secret = str(settings.api_shared_secret or "").strip()
        if not base_url or not secret:
            raise RuntimeError("Backend API URL or API_SHARED_SECRET is not configured")

        response = requests.post(
            f"{base_url}{path}",
            headers={"X-API-Secret": secret},
            json=payload,
            timeout=timeout_seconds or settings.agent_api_timeout_seconds,
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

    @staticmethod
    def _format_knowledge_capture_preview_parts(
        response: dict[str, Any],
    ) -> list[str]:
        status = str(response.get("status") or "failed")
        message = str(response.get("message") or "").strip()
        if status != "requires_confirmation":
            return [(message or f"Knowledge capture status: {status}")[:1900]]

        visibility = AgentCog._safe_discord_text(
            str(response.get("visibility") or "private")
        )
        sections = [
            f"Review this knowledge capture\nVisibility: {visibility}",
        ]
        candidates = response.get("candidates")
        if isinstance(candidates, list):
            for index, candidate in enumerate(candidates[:3], start=1):
                if not isinstance(candidate, dict):
                    continue
                question = AgentCog._safe_discord_text(
                    str(candidate.get("question") or "")
                )
                answer = AgentCog._safe_discord_text(str(candidate.get("answer") or ""))
                sections.append(f"{index}. Q: {question}\n   A: {answer}")
        sections.append("Choose **Remember** to save exactly this preview, or cancel.")

        parts: list[str] = []
        for section in sections:
            remaining = section
            while remaining:
                chunk = remaining[:1900]
                remaining = remaining[1900:]
                if parts and len(parts[-1]) + len(chunk) + 2 <= 1900:
                    parts[-1] += "\n\n" + chunk
                else:
                    parts.append(chunk)
        return parts or ["I could not render that knowledge capture."]

    @staticmethod
    def _format_knowledge_capture_response(response: dict[str, Any]) -> str:
        parts = AgentCog._format_knowledge_capture_preview_parts(response)
        if len(parts) == 1:
            return parts[0]
        return "The capture preview was sent in multiple messages."

    @staticmethod
    def _format_knowledge_query_response(response: dict[str, Any]) -> str:
        answer = str(
            response.get("answer")
            or response.get("message")
            or response.get("error")
            or "I could not answer that question."
        ).strip()
        safe_answer = AgentCog._safe_discord_text(answer)
        source_lines: list[str] = []
        citations = response.get("citations")
        if isinstance(citations, list) and citations:
            for index, citation in enumerate(citations[:8], start=1):
                if not isinstance(citation, dict):
                    continue
                title = AgentCog._safe_discord_text(
                    str(
                        citation.get("title") or citation.get("source_type") or "Source"
                    )
                )
                url = str(citation.get("url") or "").strip()
                stale = " (review due)" if citation.get("stale") else ""
                if re.match(r"^https?://", url, flags=re.I) and len(url) <= 500:
                    source_lines.append(f"{index}. {title[:300]}{stale} — <{url}>")
                else:
                    source_type = AgentCog._safe_discord_text(
                        str(citation.get("source_type") or "source")
                    )
                    source_lines.append(
                        f"{index}. {title[:300]}{stale} ({source_type[:80]})"
                    )

        suffix_parts: list[str] = []
        if source_lines:
            included_sources: list[str] = []
            for source_line in source_lines:
                candidate = "\n".join([*included_sources, source_line])
                if len(candidate) > 850 and included_sources:
                    break
                included_sources.append(source_line[:850])
            suffix_parts.append("Sources:\n" + "\n".join(included_sources))
        if response.get("source_errors"):
            suffix_parts.append("Some knowledge sources were temporarily unavailable.")
        suffix = "\n\n".join(suffix_parts)
        answer_limit = 1900 - (len(suffix) + 2 if suffix else 0)
        rendered_answer = safe_answer[: max(1, answer_limit)].rstrip()
        return (f"{rendered_answer}\n\n{suffix}" if suffix else rendered_answer)[:1900]

    @staticmethod
    def _safe_discord_text(value: str) -> str:
        return discord.utils.escape_markdown(discord.utils.escape_mentions(value))

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
                elif isinstance(result_payload, dict) and isinstance(
                    result_payload.get("fact"), dict
                ):
                    lines.extend(
                        self._format_memory_fact_result_lines(
                            tool_name, {"facts": [result_payload["fact"]]}
                        )
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

    def _memory_view(
        self, response: dict[str, Any], user_id: int, context: dict[str, Any]
    ) -> MemoryFactsView | None:
        for result in response.get("results") or []:
            if (
                result.get("tool_name") != "memory_read.get_user_facts"
                or result.get("status") != "succeeded"
            ):
                continue
            facts = [
                fact
                for fact in (result.get("result") or {}).get("facts") or []
                if fact.get("id")
                and fact.get("visibility") == "private"
                and fact.get("scope_id") == str(user_id)
            ]
            if facts:
                return MemoryFactsView(
                    cog=self, requester_id=user_id, context=context, facts=facts
                )
        return None

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
                lines.append(
                    f"  - {AgentCog._safe_discord_text(key)}: {AgentCog._safe_discord_text(value)}"
                )
            else:
                lines.append(f"  - {key}")
            if fact.get("id"):
                lines.append(f"    ID: {fact['id']}")
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
    bot.add_dynamic_items(KnowledgeCaptureDynamicButton)
    await bot.add_cog(AgentCog(bot))
