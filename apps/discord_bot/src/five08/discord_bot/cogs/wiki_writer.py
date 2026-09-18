"""Permissioned Discord controls for backend-owned wiki editing proposals."""

from __future__ import annotations

import asyncio
import html
import io
import inspect
import logging
import re
from typing import Any, Literal, cast
from urllib.parse import urlparse
from uuid import UUID, uuid4

import discord
import requests
from discord import app_commands
from discord.ext import commands

from five08.discord_bot.config import settings
from five08.discord_bot.utils.audit import DiscordAuditCogMixin
from five08.discord_bot.utils.role_decorators import (
    check_user_roles_with_hierarchy,
    require_role,
)
from five08.tls import default_ca_bundle_path
from five08.wiki_editing.assertions import (
    WIKI_ASSERTION_HEADER,
    create_wiki_action_assertion,
)
from five08.wiki_editing.models import WikiEditReviewArtifact


logger = logging.getLogger(__name__)
NO_MENTIONS = discord.AllowedMentions.none()
WIKI_UPDATE_INSTRUCTION_MAX_LENGTH = 4_000
WIKI_TARGET_DOCUMENT_ID_MAX_LENGTH = 256
WIKI_THREAD_MESSAGE_LIMIT = 20
# Keep the bot-side snapshot inside the backend/OMP aggregate source budget:
# 4k explicit instruction + 16k target article + 12k selected thread = 32k.
WIKI_THREAD_CONTEXT_MAX_CHARS = 12_000
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WIKI_UPDATE_COMPONENT_RE = re.compile(
    r"^wiki:update:(?P<action>publish|revise|cancel|refresh):"
    r"(?P<proposal_id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}):(?P<guild_id>[1-9][0-9]{0,19}):"
    r"(?P<requester_id>[1-9][0-9]{0,19})$",
    re.IGNORECASE,
)
_WIKI_REVIEW_ACK_COMPONENT_RE = re.compile(
    r"^wiki:review:ack:(?P<proposal_id>[0-9a-f]{8}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}):"
    r"(?P<requester_id>[1-9][0-9]{0,19}):(?P<review_id>[0-9a-f]{16})$",
    re.IGNORECASE,
)

WikiUpdateAction = Literal["publish", "revise", "cancel", "refresh"]
WikiProposalAction = Literal["publish", "revise", "cancel", "refresh", "ack"]
_WIKI_UPDATE_ACTIONS: tuple[WikiUpdateAction, ...] = (
    "publish",
    "revise",
    "cancel",
    "refresh",
)
_BUTTON_LABELS: dict[WikiUpdateAction, str] = {
    "publish": "Publish",
    "revise": "Revise",
    "cancel": "Cancel",
    "refresh": "Refresh",
}
_BUTTON_STYLES: dict[WikiUpdateAction, discord.ButtonStyle] = {
    "publish": discord.ButtonStyle.primary,
    "revise": discord.ButtonStyle.secondary,
    "cancel": discord.ButtonStyle.danger,
    "refresh": discord.ButtonStyle.secondary,
}


class WikiWriterConfigurationError(RuntimeError):
    """Raised when the Discord-to-backend wiki update path is not configured."""


def _safe_display_text(value: object, *, max_length: int) -> str:
    """Return compact Discord-safe text from an untrusted backend field."""
    normalized = html.unescape(str(value or ""))
    normalized = _HTML_TAG_RE.sub("", normalized)
    normalized = " ".join(normalized.split())
    normalized = discord.utils.escape_mentions(
        discord.utils.escape_markdown(normalized)
    )
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[: max_length - 1].rstrip()}…"


def _safe_document_url(value: object) -> str | None:
    """Return a displayable HTTP(S) URL, never arbitrary Markdown link text."""
    candidate = str(value or "").strip()
    if not candidate or len(candidate) > 2_000:
        return None
    if any(character.isspace() for character in candidate) or any(
        character in candidate for character in "<>"
    ):
        return None
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return candidate


def _safe_count(value: object) -> int:
    """Normalize a response count without treating booleans as integers."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


def _proposal_uuid(value: object) -> str | None:
    """Return one canonical UUID suitable for a Discord component ID."""
    try:
        return str(UUID(str(value)))
    except (AttributeError, TypeError, ValueError):
        return None


def _review_id_from_response(response: dict[str, Any]) -> str | None:
    """Extract a bounded review binding only from a complete review payload."""
    review = response.get("review")
    if not isinstance(review, dict):
        return None
    candidate = str(review.get("review_id") or "").strip().lower()
    if len(candidate) != 16 or any(
        character not in "0123456789abcdef" for character in candidate
    ):
        return None
    return candidate


def _wiki_update_component_id(
    *,
    action: WikiUpdateAction,
    proposal_id: str,
    guild_id: str,
    requester_id: int,
) -> str:
    """Build a bounded, restart-safe component ID for one proposal action."""
    normalized_proposal_id = _proposal_uuid(proposal_id)
    if normalized_proposal_id is None:
        raise ValueError("Wiki proposal ID must be a UUID")
    if not guild_id.isdecimal() or int(guild_id) <= 0:
        raise ValueError("Wiki guild ID must be a positive integer")
    if requester_id <= 0:
        raise ValueError("Wiki requester ID must be a positive integer")
    custom_id = (
        f"wiki:update:{action}:{normalized_proposal_id}:{guild_id}:{requester_id}"
    )
    if len(custom_id) > 100:
        raise ValueError("Wiki update component ID exceeds Discord's limit")
    return custom_id


def _wiki_review_ack_component_id(
    *,
    proposal_id: str,
    requester_id: int,
    review_id: str,
) -> str:
    """Bind an acknowledgement control to an immutable review packet.

    This deliberately omits the guild from the ID to leave space for the
    review binding. The callback still checks the configured guild before it
    reconstructs an actor context.
    """
    normalized_proposal_id = _proposal_uuid(proposal_id)
    normalized_review_id = review_id.strip().lower()
    if normalized_proposal_id is None:
        raise ValueError("Wiki proposal ID must be a UUID")
    if requester_id <= 0:
        raise ValueError("Wiki requester ID must be a positive integer")
    if len(normalized_review_id) != 16 or any(
        character not in "0123456789abcdef" for character in normalized_review_id
    ):
        raise ValueError("Wiki review ID must be a 16-character hexadecimal binding")
    custom_id = f"wiki:review:ack:{normalized_proposal_id}:{requester_id}:{normalized_review_id}"
    if len(custom_id) > 100:
        raise ValueError("Wiki review acknowledgement ID exceeds Discord's limit")
    return custom_id


def _controls_for_response(
    response: dict[str, Any],
) -> tuple[WikiProposalAction, ...]:
    """Show only controls that can be meaningful for the current lifecycle state."""
    status = str(response.get("status") or "").strip().lower()
    if status in {"published", "canceled", "publish_unknown"}:
        return ("refresh",)
    if status == "failed":
        return ("revise", "cancel", "refresh")
    if status in {"queued", "authoring"}:
        return ("cancel", "refresh")
    if status == "publishing":
        # Publishing has crossed the durable external-write boundary. It must
        # be reconciled rather than canceled, so never render a dead control.
        return ("refresh",)
    if status == "conflict":
        return ("revise", "cancel", "refresh")
    if status == "proposed":
        if (
            response.get("review_acknowledged") is True
            and str(response.get("action") or "").strip().lower() == "publish"
        ):
            return _WIKI_UPDATE_ACTIONS
        if _review_id_from_response(response) is not None:
            return ("ack", "revise", "cancel", "refresh")
        # Never offer a publish or acknowledgement button unless this response
        # successfully carries the complete private review packet.
        return ("revise", "cancel", "refresh")
    # Unknown lifecycle values fail closed to reversible controls.
    return ("refresh",)


class WikiUpdateDynamicButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=_WIKI_UPDATE_COMPONENT_RE,
):
    """Restart-safe dispatcher for one backend-owned wiki proposal action."""

    def __init__(
        self,
        *,
        action: WikiUpdateAction,
        proposal_id: str,
        guild_id: str,
        requester_id: int,
    ) -> None:
        self.action = action
        self.proposal_id = _proposal_uuid(proposal_id) or proposal_id
        self.guild_id = guild_id
        self.requester_id = requester_id
        super().__init__(
            discord.ui.Button(
                label=_BUTTON_LABELS[action],
                style=_BUTTON_STYLES[action],
                custom_id=_wiki_update_component_id(
                    action=action,
                    proposal_id=self.proposal_id,
                    guild_id=guild_id,
                    requester_id=requester_id,
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
    ) -> "WikiUpdateDynamicButton":
        del interaction, item
        return cls(
            action=cast(WikiUpdateAction, match["action"].lower()),
            proposal_id=match["proposal_id"],
            guild_id=match["guild_id"],
            requester_id=int(match["requester_id"]),
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        client = getattr(interaction, "client", None)
        get_cog = getattr(client, "get_cog", None)
        cog = get_cog("WikiWriterCog") if callable(get_cog) else None
        if not isinstance(cog, WikiWriterCog):
            await _send_ephemeral(
                interaction,
                "Wiki update controls are temporarily unavailable. Try again.",
            )
            return

        # Dynamic items are reconstructed against a generic discord.py View,
        # including before a restart, so owner identity must be self-contained.
        restored_view = WikiProposalView(
            cog=cog,
            requester_id=self.requester_id,
            proposal_id=self.proposal_id,
            guild_id=self.guild_id,
            actions=(self.action,),
        )
        await restored_view.handle_action(interaction, self.action)


class WikiReviewAcknowledgementButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=_WIKI_REVIEW_ACK_COMPONENT_RE,
):
    """Restart-safe owner acknowledgement for one complete review attachment."""

    def __init__(
        self,
        *,
        proposal_id: str,
        requester_id: int,
        review_id: str,
    ) -> None:
        self.proposal_id = _proposal_uuid(proposal_id) or proposal_id
        self.requester_id = requester_id
        self.review_id = review_id.strip().lower()
        super().__init__(
            discord.ui.Button(
                label="Acknowledge review",
                style=discord.ButtonStyle.success,
                custom_id=_wiki_review_ack_component_id(
                    proposal_id=self.proposal_id,
                    requester_id=requester_id,
                    review_id=self.review_id,
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
    ) -> "WikiReviewAcknowledgementButton":
        del interaction, item
        return cls(
            proposal_id=match["proposal_id"],
            requester_id=int(match["requester_id"]),
            review_id=match["review_id"],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        client = getattr(interaction, "client", None)
        get_cog = getattr(client, "get_cog", None)
        cog = get_cog("WikiWriterCog") if callable(get_cog) else None
        if not isinstance(cog, WikiWriterCog):
            await _send_ephemeral(
                interaction,
                "Wiki review controls are temporarily unavailable. Try again.",
            )
            return
        guild_id = cog._configured_guild_id()
        if guild_id is None:
            await _send_ephemeral(
                interaction,
                "Wiki review controls are only available in the configured co-op server.",
            )
            return
        restored_view = WikiProposalView(
            cog=cog,
            requester_id=self.requester_id,
            proposal_id=self.proposal_id,
            guild_id=guild_id,
            review_id=self.review_id,
            actions=("ack",),
        )
        await restored_view.handle_action(interaction, "ack")


class WikiRevisionModal(discord.ui.Modal):
    """Collect a bounded revision instruction without retaining source text."""

    def __init__(self, *, view: "WikiProposalView") -> None:
        super().__init__(title="Revise wiki update")
        self.proposal_view = view
        self.instruction = discord.ui.TextInput(
            label="What should change?",
            style=discord.TextStyle.paragraph,
            max_length=WIKI_UPDATE_INSTRUCTION_MAX_LENGTH,
            required=True,
        )
        self.add_item(self.instruction)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.proposal_view.submit_revision(
            interaction, str(self.instruction.value)
        )


class WikiProposalView(discord.ui.View):
    """Ephemeral controls for one proposal with requester-only live handling."""

    def __init__(
        self,
        *,
        cog: "WikiWriterCog",
        requester_id: int,
        proposal_id: str,
        guild_id: str,
        review_id: str | None = None,
        actions: tuple[WikiProposalAction, ...] | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.requester_id = requester_id
        self.proposal_id = proposal_id
        self.guild_id = guild_id
        self.review_id = review_id
        for action in actions or ("revise", "cancel", "refresh"):
            if action == "ack":
                if review_id is not None:
                    self.add_item(
                        WikiReviewAcknowledgementButton(
                            proposal_id=proposal_id,
                            requester_id=requester_id,
                            review_id=review_id,
                        )
                    )
                continue
            self.add_item(
                WikiUpdateDynamicButton(
                    action=cast(WikiUpdateAction, action),
                    proposal_id=proposal_id,
                    guild_id=guild_id,
                    requester_id=requester_id,
                )
            )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.requester_id:
            return True
        await _send_ephemeral(
            interaction,
            "Only the requester can control this wiki update.",
        )
        return False

    async def handle_action(
        self,
        interaction: discord.Interaction,
        action: WikiProposalAction,
    ) -> None:
        if interaction.user.id != self.requester_id:
            await _send_ephemeral(
                interaction,
                "Only the requester can control this wiki update.",
            )
            return

        if action == "revise":
            if not self.cog._is_expected_configured_guild(
                interaction,
                expected_guild_id=self.guild_id,
            ):
                await _send_ephemeral(
                    interaction,
                    "Wiki update controls are only available in the configured co-op server.",
                )
                return
            await interaction.response.send_modal(WikiRevisionModal(view=self))
            return

        await interaction.response.defer(ephemeral=True)
        authorization = await self.cog._authorize_interaction(
            interaction,
            expected_guild_id=self.guild_id,
        )
        if authorization is None:
            return
        context, _member = authorization
        await self._finish(interaction, action=action, context=context)

    async def submit_revision(
        self,
        interaction: discord.Interaction,
        instruction: str,
    ) -> None:
        if interaction.user.id != self.requester_id:
            await _send_ephemeral(
                interaction,
                "Only the requester can control this wiki update.",
            )
            return

        normalized_instruction = " ".join(instruction.split())
        if not normalized_instruction:
            await _send_ephemeral(interaction, "A revision instruction is required.")
            return
        if len(normalized_instruction) > WIKI_UPDATE_INSTRUCTION_MAX_LENGTH:
            await _send_ephemeral(
                interaction,
                "Revision instructions must be 4,000 characters or fewer.",
            )
            return

        await interaction.response.defer(ephemeral=True)
        authorization = await self.cog._authorize_interaction(
            interaction,
            expected_guild_id=self.guild_id,
        )
        if authorization is None:
            return
        context, _member = authorization
        await self._finish(
            interaction,
            action="revise",
            context=context,
            instruction=normalized_instruction,
        )

    async def _finish(
        self,
        interaction: discord.Interaction,
        *,
        action: WikiProposalAction,
        context: dict[str, Any],
        instruction: str | None = None,
    ) -> None:
        try:
            response = await self.cog._post_proposal_action(
                proposal_id=self.proposal_id,
                action=action,
                context=context,
                instruction=instruction,
                review_id=self.review_id if action == "ack" else None,
            )
        except Exception:
            logger.warning("Wiki proposal action failed", exc_info=True)
            response = {
                "status": "failed",
                "message": "The wiki update service could not be reached. Try again.",
                "http_status": 503,
            }

        self.cog._audit_wiki_response(
            interaction=interaction,
            action=f"wiki.update.{action}",
            response=response,
            proposal_id=self.proposal_id,
        )
        await self.cog._send_wiki_response(
            interaction=interaction,
            response=response,
            requester_id=self.requester_id,
            guild_id=self.guild_id,
        )


async def _send_ephemeral(interaction: discord.Interaction, message: str) -> None:
    """Reply once, using followups when an interaction was already acknowledged."""
    response = interaction.response
    is_done = getattr(response, "is_done", None)
    response_done = is_done() if callable(is_done) else False
    if inspect.isawaitable(response_done):
        # ``InteractionResponse.is_done`` is synchronous.  Treat an accidental
        # async test double as not acknowledged without leaking a coroutine.
        close = getattr(response_done, "close", None)
        if callable(close):
            close()
        response_done = False
    if response_done is True:
        await interaction.followup.send(
            message,
            allowed_mentions=NO_MENTIONS,
            ephemeral=True,
        )
        return
    await response.send_message(
        message,
        allowed_mentions=NO_MENTIONS,
        ephemeral=True,
    )


def _private_review_attachment(
    response: dict[str, Any],
) -> tuple[discord.File | None, str | None]:
    """Build a bounded review file without ever putting raw content in a card."""
    raw_review = response.get("review")
    if not isinstance(raw_review, dict):
        return None, None
    try:
        review = WikiEditReviewArtifact.model_validate(raw_review)
        content = review.attachment_bytes()
    except (TypeError, ValueError):
        logger.warning("Wiki response contained an invalid review attachment")
        return None, None
    return (
        discord.File(io.BytesIO(content), filename=review.attachment_filename),
        review.review_id,
    )


class WikiWriterCog(DiscordAuditCogMixin, commands.Cog):
    """Thin Discord UI for authorized, backend-owned wiki edit proposals."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._init_audit_logger()

    @staticmethod
    def _configured_guild_id() -> str | None:
        configured_guild_id = str(settings.discord_server_id or "").strip()
        return configured_guild_id or None

    @classmethod
    def _is_configured_guild(cls, interaction: discord.Interaction) -> bool:
        configured_guild_id = cls._configured_guild_id()
        return bool(configured_guild_id) and (
            str(interaction.guild_id or "") == configured_guild_id
        )

    @classmethod
    def _is_expected_configured_guild(
        cls,
        interaction: discord.Interaction,
        *,
        expected_guild_id: str,
    ) -> bool:
        return (
            cls._configured_guild_id() == expected_guild_id
            and str(interaction.guild_id or "") == expected_guild_id
        )

    @app_commands.command(
        name="wiki-update",
        description="Propose an approved update to the co-op wiki.",
    )
    @app_commands.describe(
        instruction="The requested wiki change.",
        target_document_id="Optional Outline document ID to update.",
        include_current_thread="Include up to 20 messages from this accessible public thread.",
    )
    @require_role("Steering Committee")
    async def wiki_update(
        self,
        interaction: discord.Interaction,
        instruction: str,
        target_document_id: str | None = None,
        include_current_thread: bool = False,
    ) -> None:
        """Create one backend-owned wiki edit proposal for review."""
        if not self._is_configured_guild(interaction):
            await _send_ephemeral(
                interaction,
                "This command is only available in the configured co-op server.",
            )
            return

        normalized_instruction = " ".join(instruction.split())
        if not normalized_instruction:
            await _send_ephemeral(interaction, "A wiki update instruction is required.")
            return
        if len(normalized_instruction) > WIKI_UPDATE_INSTRUCTION_MAX_LENGTH:
            await _send_ephemeral(
                interaction,
                "Wiki update instructions must be 4,000 characters or fewer.",
            )
            return

        normalized_target_document_id = (
            " ".join((target_document_id or "").split()) or None
        )
        if (
            normalized_target_document_id is not None
            and len(normalized_target_document_id) > WIKI_TARGET_DOCUMENT_ID_MAX_LENGTH
        ):
            await _send_ephemeral(
                interaction,
                "Target document IDs must be 256 characters or fewer.",
            )
            return

        configured_guild_id = self._configured_guild_id()
        if configured_guild_id is None:  # pragma: no cover - guarded above
            await _send_ephemeral(
                interaction,
                "This command is only available in the configured co-op server.",
            )
            return
        await interaction.response.defer(ephemeral=True)
        authorization = await self._authorize_interaction(
            interaction,
            expected_guild_id=configured_guild_id,
        )
        if authorization is None:
            return
        context, member = authorization
        selected_conversation: list[dict[str, Any]] = []
        if include_current_thread:
            selected_conversation = await self._collect_current_thread(
                interaction,
                member=member,
                guild_id=configured_guild_id,
            )

        payload: dict[str, Any] = {
            "instruction": normalized_instruction,
            "selected_conversation": selected_conversation,
            "context": context,
        }
        if normalized_target_document_id is not None:
            payload["target_document_id"] = normalized_target_document_id
        try:
            response = await self._create_wiki_update(payload)
        except Exception:
            logger.warning("Wiki update request failed", exc_info=True)
            response = {
                "status": "failed",
                "message": "The wiki update service could not be reached. Try again.",
                "http_status": 503,
            }

        self._audit_wiki_response(
            interaction=interaction,
            action="wiki.update.request",
            response=response,
            proposal_id=_proposal_uuid(response.get("proposal_id")),
        )
        await self._send_wiki_response(
            interaction=interaction,
            response=response,
            requester_id=interaction.user.id,
            guild_id=configured_guild_id,
        )

    async def _authorize_interaction(
        self,
        interaction: discord.Interaction,
        *,
        expected_guild_id: str,
    ) -> tuple[dict[str, Any], discord.Member] | None:
        """Re-fetch the actor and fail closed before every durable action."""
        if not self._is_expected_configured_guild(
            interaction,
            expected_guild_id=expected_guild_id,
        ):
            await _send_ephemeral(
                interaction,
                "Wiki update controls are only available in the configured co-op server.",
            )
            return None
        try:
            guild = self.bot.get_guild(int(expected_guild_id))
        except (TypeError, ValueError):
            guild = None
        if guild is None:
            await _send_ephemeral(
                interaction,
                "The configured co-op server is temporarily unavailable. Try again.",
            )
            return None

        try:
            async with asyncio.timeout(3):
                member = await guild.fetch_member(interaction.user.id)
        except discord.NotFound:
            await _send_ephemeral(
                interaction,
                "You are no longer a member of the configured co-op server.",
            )
            return None
        except (TimeoutError, discord.HTTPException):
            logger.warning("Could not refresh wiki update actor roles", exc_info=True)
            await _send_ephemeral(
                interaction,
                "Could not verify your current wiki update access. Try again.",
            )
            return None

        role_names = self._role_names_from_user(member)
        if not check_user_roles_with_hierarchy(
            list(getattr(member, "roles", [])), ["Steering Committee"]
        ):
            await _send_ephemeral(
                interaction,
                "You no longer have the Steering Committee role required for wiki updates.",
            )
            return None

        channel = getattr(interaction, "channel", None)
        interaction_message = getattr(interaction, "message", None)
        message_id = (
            str(interaction_message.id)
            if interaction_message is not None
            and getattr(interaction_message, "id", None) is not None
            else None
        )
        interaction_id = getattr(interaction, "id", None)
        return (
            {
                "discord_user_id": str(interaction.user.id),
                "operation_id": str(uuid4()),
                "internal_user_id": None,
                "organization_id": expected_guild_id,
                "guild_id": expected_guild_id,
                "channel_id": (
                    str(getattr(interaction, "channel_id", None))
                    if getattr(interaction, "channel_id", None) is not None
                    else None
                ),
                "thread_id": (
                    str(channel.id) if isinstance(channel, discord.Thread) else None
                ),
                "response_destination_visibility": "private",
                "roles": role_names,
                "scopes": [],
                "impersonation": False,
                "interaction_id": (
                    str(interaction_id) if interaction_id is not None else None
                ),
                "message_id": message_id,
            },
            member,
        )

    @staticmethod
    def _role_names_from_user(user: discord.abc.User) -> list[str]:
        roles = getattr(user, "roles", [])
        return [
            str(getattr(role, "name", "")).strip()
            for role in roles
            if str(getattr(role, "name", "")).strip()
        ]

    async def _collect_current_thread(
        self,
        interaction: discord.Interaction,
        *,
        member: discord.Member,
        guild_id: str,
    ) -> list[dict[str, Any]]:
        """Return one bounded org-visible source from an accessible public thread."""
        channel = getattr(interaction, "channel", None)
        if not isinstance(channel, discord.Thread):
            return []
        try:
            if channel.is_private():
                return []
            thread_guild = channel.guild
            if str(thread_guild.id) != guild_id:
                return []
            bot_member = thread_guild.me
            default_role = thread_guild.default_role
            if bot_member is None or default_role is None:
                return []
            actor_permissions = (
                channel.permissions_for(member),
                channel.permissions_for(bot_member),
            )
            if not all(
                permission.view_channel and permission.read_message_history
                for permission in actor_permissions
            ):
                return []
            # A non-private thread can still inherit a role-restricted parent.
            # Only export text labelled org-visible when the guild's default
            # role can view both the thread and its parent history.
            visibility_channels = [channel]
            parent = getattr(channel, "parent", None)
            if parent is not None:
                visibility_channels.append(parent)
            if not all(
                source_channel.permissions_for(default_role).view_channel
                and source_channel.permissions_for(default_role).read_message_history
                for source_channel in visibility_channels
            ):
                return []
        except (AttributeError, discord.ClientException, discord.HTTPException):
            return []

        lines: list[str] = []
        message_ids: list[str] = []
        remaining = WIKI_THREAD_CONTEXT_MAX_CHARS
        try:
            async with asyncio.timeout(3):
                newest_messages = [
                    message
                    async for message in channel.history(
                        limit=WIKI_THREAD_MESSAGE_LIMIT,
                        oldest_first=False,
                    )
                ]
        except (TimeoutError, discord.HTTPException):
            logger.warning(
                "Could not collect requested wiki thread context", exc_info=True
            )
            return []

        # Discord returns this bounded batch newest-first. Consume it in that
        # order so a character-bound snapshot retains the current discussion,
        # then restore chronology before it reaches the author.
        selected_lines: list[tuple[str, str | None]] = []
        for message in newest_messages:
            raw_content = str(getattr(message, "content", "")).strip()
            if not raw_content or remaining <= 0:
                continue
            author = getattr(message, "author", None)
            author_id = str(getattr(author, "id", "unknown"))
            prefix = f"{author_id}: "
            separator = "\n" if selected_lines else ""
            available = remaining - len(separator)
            if available <= len(prefix):
                break
            line = f"{prefix}{raw_content}"[:available]
            if not line.strip():
                continue
            message_id = getattr(message, "id", None)
            selected_lines.append(
                (line, str(message_id) if message_id is not None else None)
            )
            remaining -= len(separator) + len(line)
            if remaining <= 0:
                break

        for line, message_id in reversed(selected_lines):
            lines.append(line)
            if message_id is not None:
                message_ids.append(message_id)

        if not lines:
            return []
        source_url = (
            str(getattr(channel, "jump_url", "") or "").strip()
            or f"https://discord.com/channels/{guild_id}/{channel.id}"
        )
        return [
            {
                "provenance": {
                    "source_type": "discord_thread",
                    "source_ref": source_url,
                    "title": str(getattr(channel, "name", "Discord thread"))[:512]
                    or "Discord thread",
                    "source_url": source_url,
                    "guild_id": guild_id,
                    "channel_id": str(channel.id),
                    "thread_id": str(channel.id),
                    "message_ids": message_ids,
                },
                "visibility": "org",
                "organization_visible_text": "\n".join(lines),
            }
        ]

    async def _create_wiki_update(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._post_backend_json, "/wiki/updates", payload
        )

    async def _post_proposal_action(
        self,
        *,
        proposal_id: str,
        action: WikiProposalAction,
        context: dict[str, Any],
        instruction: str | None = None,
        review_id: str | None = None,
    ) -> dict[str, Any]:
        if action == "revise":
            if instruction is None:  # pragma: no cover - guarded by the modal
                raise ValueError("Revision instruction is required")
            payload: dict[str, Any] = {"instruction": instruction, "context": context}
            path = f"/wiki/updates/{proposal_id}/revise"
        elif action == "publish":
            payload = {"context": context}
            path = f"/wiki/updates/{proposal_id}/publish"
        elif action == "ack":
            if review_id is None:  # pragma: no cover - guarded by the view
                raise ValueError("Wiki review acknowledgement requires a review ID")
            payload = {"context": context, "review_id": review_id}
            path = f"/wiki/updates/{proposal_id}/acknowledge-review"
        elif action == "cancel":
            payload = {"context": context}
            path = f"/wiki/updates/{proposal_id}/cancel"
        elif action == "refresh":
            payload = {"context": context}
            path = f"/wiki/updates/{proposal_id}/status"
        else:  # pragma: no cover - Literal plus dynamic-ID regex guard this
            raise ValueError("Unsupported wiki proposal action")
        return await asyncio.to_thread(self._post_backend_json, path, payload)

    def _post_backend_json(
        self,
        path: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Send an authenticated JSON request without exposing its payload in logs."""
        base_url = settings.backend_api_base_url.rstrip("/")
        secret = str(settings.api_shared_secret or "").strip()
        assertion_secret = str(settings.wiki_editing_assertion_secret or "").strip()
        if not base_url or not secret or not assertion_secret:
            raise WikiWriterConfigurationError(
                "Backend API URL, API_SHARED_SECRET, or WIKI_EDITING_ASSERTION_SECRET is not configured"
            )
        assertion = create_wiki_action_assertion(
            assertion_secret,
            method="POST",
            path=path,
            payload=payload,
        )

        response = requests.post(
            f"{base_url}{path}",
            headers={
                "X-API-Secret": secret,
                WIKI_ASSERTION_HEADER: assertion,
            },
            json=payload,
            timeout=settings.wiki_editing_request_timeout_seconds,
            verify=default_ca_bundle_path(),
            allow_redirects=False,
        )
        if 300 <= response.status_code < 400:
            raise RuntimeError(
                f"Backend returned redirect status={response.status_code}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"Backend returned non-JSON status={response.status_code}"
            ) from exc
        if not isinstance(data, dict):
            raise RuntimeError("Backend returned unexpected JSON payload")
        data.setdefault("http_status", response.status_code)
        return data

    async def _send_wiki_response(
        self,
        *,
        interaction: discord.Interaction,
        response: dict[str, Any],
        requester_id: int,
        guild_id: str,
    ) -> None:
        proposal_id = _proposal_uuid(response.get("proposal_id"))
        review_file, review_id = _private_review_attachment(response)
        actions = _controls_for_response(response)
        if "ack" in actions and (review_file is None or review_id is None):
            actions = tuple(action for action in actions if action != "ack")
        view: WikiProposalView | None = None
        if proposal_id is not None:
            view = WikiProposalView(
                cog=self,
                requester_id=requester_id,
                proposal_id=proposal_id,
                guild_id=guild_id,
                review_id=review_id,
                actions=actions,
            )
        content = self._format_wiki_response(
            response,
            review_attached=review_file is not None,
        )
        if view is None:
            kwargs: dict[str, Any] = {
                "allowed_mentions": NO_MENTIONS,
                "ephemeral": True,
            }
            if review_file is not None:
                kwargs["file"] = review_file
            await interaction.followup.send(content, **kwargs)
            return
        kwargs = {
            "view": view,
            "allowed_mentions": NO_MENTIONS,
            "ephemeral": True,
        }
        if review_file is not None:
            kwargs["file"] = review_file
        await interaction.followup.send(content, **kwargs)

    @staticmethod
    def _format_wiki_response(
        response: dict[str, Any],
        *,
        review_attached: bool = False,
    ) -> str:
        """Render only compact metadata; full content lives in a private file."""
        message = _safe_display_text(
            response.get("message") or "Wiki update status received.",
            max_length=700,
        )
        lines = [message]
        status = _safe_display_text(response.get("status"), max_length=80)
        if status:
            lines.append(f"Status: {status}")
        if response.get("audience") == "shared_coop_wiki":
            lines.append("Audience: shared co-op wiki")
        title = _safe_display_text(response.get("title"), max_length=200)
        if title:
            lines.append(f"Document: {title}")
        target_document_id = _safe_display_text(
            response.get("target_document_id"), max_length=256
        )
        if target_document_id:
            lines.append(f"Document ID: {target_document_id}")
        revision = response.get("revision")
        if (
            isinstance(revision, int)
            and not isinstance(revision, bool)
            and revision > 0
        ):
            lines.append(f"Revision: {revision}")
        lines.append(f"Sources included: {_safe_count(response.get('source_count'))}")
        operation_status = _safe_display_text(
            response.get("operation_status"), max_length=80
        )
        if operation_status:
            lines.append(f"Publish operation: {operation_status}")
        summary = _safe_display_text(response.get("summary"), max_length=500)
        if summary:
            lines.append(f"Summary: {summary}")
        if review_attached:
            lines.append(
                "The complete proposed article, diff, and safe source links are attached privately."
            )
        elif str(response.get("status") or "").strip().lower() == "proposed":
            lines.append(
                "The complete review packet is unavailable. Refresh or revise before publishing."
            )
        document_url = _safe_document_url(response.get("document_url"))
        if document_url:
            lines.append(f"Open document: <{document_url}>")
        return "\n".join(lines)[:1_900]

    @staticmethod
    def _audit_result(response: dict[str, Any]) -> str:
        http_status = response.get("http_status")
        if http_status in {401, 403}:
            return "denied"
        if isinstance(http_status, int) and http_status >= 400:
            return "error"
        status = str(response.get("status") or "").strip().lower()
        if status == "failed" or response.get("error"):
            return "error"
        if status == "denied":
            return "denied"
        return "success"

    def _audit_wiki_response(
        self,
        *,
        interaction: discord.Interaction,
        action: str,
        response: dict[str, Any],
        proposal_id: str | None,
    ) -> None:
        """Audit only proposal identifiers, lifecycle state, and bounded counts."""
        metadata: dict[str, Any] = {
            "proposal_id": proposal_id,
            "target_document_id": _safe_display_text(
                response.get("target_document_id"), max_length=256
            )
            or None,
            "status": _safe_display_text(response.get("status"), max_length=80) or None,
            "operation_status": _safe_display_text(
                response.get("operation_status"), max_length=80
            )
            or None,
            "source_count": _safe_count(response.get("source_count")),
        }
        revision = response.get("revision")
        if (
            isinstance(revision, int)
            and not isinstance(revision, bool)
            and revision > 0
        ):
            metadata["revision"] = revision
        self._audit_command_safe(
            interaction=interaction,
            action=action,
            result=self._audit_result(response),
            metadata=metadata,
            resource_type="wiki_edit_proposal",
            resource_id=proposal_id,
        )


async def setup(bot: commands.Bot) -> None:
    """Load the restart-safe wiki writer controls and cog."""
    bot.add_dynamic_items(WikiUpdateDynamicButton, WikiReviewAcknowledgementButton)
    await bot.add_cog(WikiWriterCog(bot))
