"""Edge-case unit tests for /reprocess-resume helpers and guards."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from five08.discord_bot.cogs.crm import CRMCog


@pytest.fixture
def mock_bot():
    bot = Mock()
    bot.get_cog = Mock()
    return bot


@pytest.fixture
def mock_espo_api():
    with patch("five08.discord_bot.cogs.crm.EspoAPI") as mock_api_class:
        mock_api = Mock()
        mock_api_class.return_value = mock_api
        yield mock_api


@pytest.fixture
def crm_cog(mock_bot, mock_espo_api):
    cog = CRMCog(mock_bot)
    cog.espo_api = mock_espo_api
    return cog


@pytest.fixture
def mock_interaction():
    interaction = AsyncMock()
    interaction.response = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.user = Mock()
    interaction.user.roles = []
    return interaction


@pytest.mark.asyncio
async def test_search_reprocess_uses_discord_mention_lookup(crm_cog):
    contact = {"id": "contact123", "name": "Mention User"}
    with (
        patch.object(
            crm_cog, "_find_contact_by_discord_id", new=AsyncMock(return_value=contact)
        ) as mock_by_id,
        patch.object(
            crm_cog, "_search_contact_for_linking", new=AsyncMock()
        ) as mock_linking,
    ):
        results = await crm_cog._search_contacts_for_reprocess_resume("<@123456789>")

    assert results == [contact]
    mock_by_id.assert_awaited_once_with("123456789")
    mock_linking.assert_not_awaited()


@pytest.mark.asyncio
async def test_search_reprocess_falls_back_to_508_email(crm_cog):
    with patch.object(
        crm_cog,
        "_search_contact_for_linking",
        new=AsyncMock(
            side_effect=[[], [{"id": "contact123", "name": "Fallback User"}]]
        ),
    ) as mock_linking:
        results = await crm_cog._search_contacts_for_reprocess_resume("fallbackuser")

    assert results == [{"id": "contact123", "name": "Fallback User"}]
    assert mock_linking.await_count == 2
    assert mock_linking.await_args_list[0].args == ("fallbackuser",)
    assert mock_linking.await_args_list[1].args == ("fallbackuser@508.dev",)


@pytest.mark.asyncio
async def test_search_reprocess_falls_back_to_discord_username(crm_cog):
    contact = {"id": "contact123", "name": "Discord Username Match"}
    with (
        patch.object(
            crm_cog, "_search_contact_for_linking", new=AsyncMock(return_value=[])
        ) as mock_linking,
        patch.object(
            crm_cog,
            "_find_contact_by_discord_username",
            new=AsyncMock(return_value=contact),
        ) as mock_by_username,
    ):
        results = await crm_cog._search_contacts_for_reprocess_resume("@MixedCaseName")

    assert results == [contact]
    mock_linking.assert_awaited_once_with("@MixedCaseName")
    mock_by_username.assert_awaited_once_with("mixedcasename")


@pytest.mark.asyncio
async def test_get_latest_resume_attachment_returns_latest_with_filename(crm_cog):
    crm_cog.espo_api.request.return_value = {
        "resumeIds": ["resume-old", "resume-new"],
        "resumeNames": {"resume-new": "latest_resume.pdf"},
    }

    attachment_id, filename = await crm_cog._get_latest_resume_attachment_for_contact(
        "contact123"
    )

    assert attachment_id == "resume-new"
    assert filename == "latest_resume.pdf"


@pytest.mark.asyncio
async def test_get_latest_resume_attachment_returns_none_filename_without_names(
    crm_cog,
):
    crm_cog.espo_api.request.return_value = {
        "resumeIds": ["resume-only"],
    }

    attachment_id, filename = await crm_cog._get_latest_resume_attachment_for_contact(
        "contact123"
    )

    assert attachment_id == "resume-only"
    assert filename is None


@pytest.mark.asyncio
async def test_reprocess_resume_requires_api_shared_secret(crm_cog, mock_interaction):
    mock_interaction.user.id = 101
    with (
        patch("five08.discord_bot.cogs.crm.settings.api_shared_secret", None),
        patch.object(
            crm_cog, "_search_contacts_for_reprocess_resume", new=AsyncMock()
        ) as mock_search,
    ):
        await crm_cog.reprocess_resume.callback(crm_cog, mock_interaction, "candidate")

    mock_search.assert_not_awaited()
    mock_interaction.followup.send.assert_called_once_with(
        "❌ API_SHARED_SECRET is not configured for backend API access."
    )
