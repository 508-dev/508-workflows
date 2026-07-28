"""Read-only Outline wiki search commands for Discord members."""

from __future__ import annotations

import asyncio
from datetime import datetime
import html
import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

from five08.clients.outline import (
    OutlineAPIError,
    OutlineClient,
    OutlineDocumentSummary,
    OutlineSearchResult,
)
from five08.discord_bot.config import settings
from five08.discord_bot.utils.role_decorators import require_role


logger = logging.getLogger(__name__)
NO_MENTIONS = discord.AllowedMentions.none()
WIKI_SEARCH_RESULT_LIMIT = 5
WIKI_QUICK_LINK_LIMIT = 6
WIKI_QUERY_MAX_LENGTH = 200
WIKI_TITLE_MAX_LENGTH = 220
WIKI_CONTEXT_MAX_LENGTH = 300
_HTML_TAG_RE = re.compile(r"<[^>]+>")


class OutlineWikiConfigurationError(RuntimeError):
    """Raised when the dedicated Outline wiki integration is not configured."""


def _safe_display_text(value: str, *, max_length: int) -> str:
    """Collapse and escape untrusted wiki text for a Discord embed."""
    normalized = html.unescape(value)
    normalized = _HTML_TAG_RE.sub("", normalized)
    normalized = " ".join(normalized.split())
    normalized = discord.utils.escape_mentions(
        discord.utils.escape_markdown(normalized)
    )
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[: max_length - 1].rstrip()}…"


def _updated_date(updated_at: str | None) -> str | None:
    """Format a trusted Outline timestamp as a compact date."""
    if not updated_at:
        return None
    try:
        return (
            datetime.fromisoformat(updated_at.replace("Z", "+00:00")).date().isoformat()
        )
    except ValueError:
        return None


def _document_link(document: OutlineDocumentSummary) -> str:
    """Return one safe Markdown link to a validated Outline document URL."""
    title = _safe_display_text(document.title, max_length=WIKI_TITLE_MAX_LENGTH)
    return f"[{title}]({document.url})"


class WikiCog(commands.Cog, name="Wiki"):
    """Search member-safe Outline content and show curated quick links."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def _outline_client(self) -> OutlineClient:
        api_key = (settings.outline_wiki_api_key or "").strip()
        if not api_key:
            raise OutlineWikiConfigurationError(
                "Outline wiki search is not configured."
            )
        return OutlineClient(
            api_key=api_key,
            base_url=settings.outline_base_url,
            timeout_seconds=max(1.0, float(settings.outline_api_timeout_seconds)),
        )

    @staticmethod
    def _is_configured_guild(interaction: discord.Interaction) -> bool:
        """Only allow the wiki credential to be used in the configured co-op guild."""
        configured_guild_id = str(settings.discord_server_id or "").strip()
        return bool(configured_guild_id) and (
            str(interaction.guild_id or "") == configured_guild_id
        )

    def _search(self, query: str) -> list[OutlineSearchResult]:
        return self._outline_client().search_documents(
            query=query,
            limit=WIKI_SEARCH_RESULT_LIMIT,
        )

    def _quick_links(self) -> list[OutlineDocumentSummary]:
        return self._outline_client().list_starred_documents(
            limit=WIKI_QUICK_LINK_LIMIT,
        )

    @app_commands.command(
        name="wiki",
        description="Search member-safe wiki pages or show quick links.",
    )
    @app_commands.describe(query="Optional search terms.")
    @require_role("Member")
    async def wiki(
        self,
        interaction: discord.Interaction,
        query: str | None = None,
    ) -> None:
        """Search the wiki, or show the integration account's starred pages."""
        if not self._is_configured_guild(interaction):
            await interaction.response.send_message(
                "This command is only available in the configured co-op server.",
                allowed_mentions=NO_MENTIONS,
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        normalized_query = " ".join((query or "").split())
        if len(normalized_query) > WIKI_QUERY_MAX_LENGTH:
            await interaction.followup.send(
                f"Search terms must be {WIKI_QUERY_MAX_LENGTH} characters or fewer.",
                allowed_mentions=NO_MENTIONS,
                ephemeral=True,
            )
            return

        try:
            if normalized_query:
                results = await asyncio.to_thread(self._search, normalized_query)
                await interaction.followup.send(
                    embed=self._search_embed(normalized_query, results),
                    allowed_mentions=NO_MENTIONS,
                    ephemeral=True,
                )
                return

            quick_links = await asyncio.to_thread(self._quick_links)
            await interaction.followup.send(
                embed=self._quick_links_embed(quick_links),
                allowed_mentions=NO_MENTIONS,
                ephemeral=True,
            )
        except OutlineWikiConfigurationError:
            await interaction.followup.send(
                "The co-op wiki is not configured yet. Ask an administrator for help.",
                allowed_mentions=NO_MENTIONS,
                ephemeral=True,
            )
        except OutlineAPIError:
            logger.warning("Outline wiki lookup failed", exc_info=True)
            await interaction.followup.send(
                "The co-op wiki is temporarily unavailable. Please try again later.",
                allowed_mentions=NO_MENTIONS,
                ephemeral=True,
            )
        except ValueError:
            logger.warning("Outline wiki lookup rejected invalid client input")
            await interaction.followup.send(
                "The co-op wiki is temporarily unavailable. Please try again later.",
                allowed_mentions=NO_MENTIONS,
                ephemeral=True,
            )

    @staticmethod
    def _quick_links_embed(
        quick_links: list[OutlineDocumentSummary],
    ) -> discord.Embed:
        """Build the no-query quick-links response."""
        if not quick_links:
            description = (
                "No quick links are configured yet. "
                "Try `/wiki query:your search terms` instead."
            )
        else:
            description = "\n".join(
                f"• {_document_link(document)}" for document in quick_links
            )

        embed = discord.Embed(
            title="Co-op Wiki",
            description=description,
            color=discord.Color.teal(),
        )
        embed.set_footer(text="Search with /wiki query:…")
        return embed

    @staticmethod
    def _search_embed(
        query: str,
        results: list[OutlineSearchResult],
    ) -> discord.Embed:
        """Build a compact, private search-results response."""
        safe_query = _safe_display_text(query, max_length=WIKI_QUERY_MAX_LENGTH)
        if not results:
            return discord.Embed(
                title="No wiki matches",
                description=(
                    f"Nothing matched **{safe_query}**. Try fewer words, quoted "
                    "phrases, `OR`, or `-word`."
                ),
                color=discord.Color.orange(),
            )

        embed = discord.Embed(
            title=f"Wiki results for {safe_query}",
            description=f"Showing {len(results)} result(s)",
            color=discord.Color.teal(),
        )
        for index, result in enumerate(results, start=1):
            document = result.document
            context = result.context or "No matching excerpt available."
            parts = [_safe_display_text(context, max_length=WIKI_CONTEXT_MAX_LENGTH)]
            if updated_date := _updated_date(document.updated_at):
                parts.append(f"Updated: {updated_date}")
            parts.append(f"[Open in Outline]({document.url})")
            embed.add_field(
                name=f"{index}. {_safe_display_text(document.title, max_length=WIKI_TITLE_MAX_LENGTH)}",
                value="\n".join(parts),
                inline=False,
            )
        embed.set_footer(text="Search supports quoted phrases, OR, and -exclude.")
        return embed


async def setup(bot: commands.Bot) -> None:
    """Load the wiki cog."""
    await bot.add_cog(WikiCog(bot))
