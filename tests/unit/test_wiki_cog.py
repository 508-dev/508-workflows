"""Unit tests for the Discord Outline wiki cog."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from five08.clients.outline import (
    OutlineAPIError,
    OutlineDocumentSummary,
    OutlineSearchResult,
)
from five08.discord_bot.cogs.wiki import NO_MENTIONS, WikiCog


def _make_interaction(role_names: list[str]) -> AsyncMock:
    interaction = AsyncMock()
    interaction.response = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.user = Mock()
    interaction.user.roles = [Mock(name=role_name) for role_name in role_names]
    for role, role_name in zip(interaction.user.roles, role_names, strict=True):
        role.name = role_name
    return interaction


def _document(
    *,
    document_id: str = "document-1",
    title: str = "Member handbook",
    url: str = "https://outline.example.test/doc/member-handbook-abc123",
    updated_at: str | None = "2026-07-20T12:00:00.000Z",
) -> OutlineDocumentSummary:
    return OutlineDocumentSummary(
        id=document_id,
        title=title,
        url=url,
        updated_at=updated_at,
    )


@pytest.fixture
def cog() -> WikiCog:
    return WikiCog(Mock())


@pytest.mark.asyncio
async def test_wiki_query_returns_private_search_embed(cog: WikiCog) -> None:
    interaction = _make_interaction(["Member"])
    result = OutlineSearchResult(
        document=_document(),
        context="<b>@everyone</b> submits **invoices** by Friday.",
        ranking=1.0,
    )
    cog._search = Mock(return_value=[result])

    await cog.wiki.callback(cog, interaction, "invoice process")

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    sent = interaction.followup.send.await_args
    assert sent.kwargs["ephemeral"] is True
    assert sent.kwargs["allowed_mentions"] is NO_MENTIONS
    embed = sent.kwargs["embed"]
    assert "invoice process" in embed.title
    assert len(embed.fields) == 1
    assert "<b>" not in embed.fields[0].value
    assert "@everyone" not in embed.fields[0].value
    assert "Open in Outline" in embed.fields[0].value


@pytest.mark.asyncio
async def test_wiki_without_query_shows_starred_quick_links(cog: WikiCog) -> None:
    interaction = _make_interaction(["Member"])
    cog._quick_links = Mock(
        return_value=[
            _document(document_id="one", title="Member handbook"),
            _document(document_id="two", title="Getting paid"),
        ]
    )

    await cog.wiki.callback(cog, interaction)

    cog._quick_links.assert_called_once_with()
    embed = interaction.followup.send.await_args.kwargs["embed"]
    assert embed.title == "Co-op Wiki"
    assert "Member handbook" in embed.description
    assert "Getting paid" in embed.description
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_wiki_query_with_no_results_explains_search_syntax(cog: WikiCog) -> None:
    interaction = _make_interaction(["Member"])
    cog._search = Mock(return_value=[])

    await cog.wiki.callback(cog, interaction, "does not exist")

    embed = interaction.followup.send.await_args.kwargs["embed"]
    assert embed.title == "No wiki matches"
    assert "quoted phrases" in embed.description
    assert "OR" in embed.description


@pytest.mark.asyncio
async def test_wiki_rejects_overlong_query_before_calling_outline(cog: WikiCog) -> None:
    interaction = _make_interaction(["Member"])
    cog._search = Mock()

    await cog.wiki.callback(cog, interaction, "x" * 201)

    cog._search.assert_not_called()
    message = interaction.followup.send.await_args.args[0]
    assert "200 characters or fewer" in message
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_wiki_handles_outline_failure_without_exposing_error(
    cog: WikiCog,
) -> None:
    interaction = _make_interaction(["Member"])
    cog._search = Mock(side_effect=OutlineAPIError("private API detail"))

    await cog.wiki.callback(cog, interaction, "invoice")

    message = interaction.followup.send.await_args.args[0]
    assert "temporarily unavailable" in message
    assert "private API detail" not in message
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_wiki_reports_missing_configuration(cog: WikiCog) -> None:
    interaction = _make_interaction(["Member"])
    with patch.object(
        cog,
        "_quick_links",
        side_effect=ValueError("Outline wiki search is not configured."),
    ):
        await cog.wiki.callback(cog, interaction)

    message = interaction.followup.send.await_args.args[0]
    assert "not configured" in message
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_wiki_requires_member_role(cog: WikiCog) -> None:
    interaction = _make_interaction([])

    await cog.wiki.callback(cog, interaction, "invoice")

    interaction.response.send_message.assert_awaited_once()
    assert "Member" in interaction.response.send_message.await_args.args[0]
    interaction.response.defer.assert_not_awaited()
    interaction.followup.send.assert_not_awaited()
