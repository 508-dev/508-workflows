"""Unit tests for _build_role_suggestions_embed in CRMCog."""

from __future__ import annotations

import os

# Set required env vars before any settings-dependent modules are imported.
_TEST_ENV = {
    "DISCORD_BOT_TOKEN": "test_token",
    "ESPO_API_KEY": "test_api_key",
    "ESPO_BASE_URL": "https://crm.test.com",
    "KIMAI_BASE_URL": "https://kimai.test.com",
    "KIMAI_API_TOKEN": "test_kimai_token",
    "CHANNEL_ID": "123456789",
    "EMAIL_USERNAME": "test@example.com",
    "EMAIL_PASSWORD": "test_password",
    "IMAP_SERVER": "imap.test.com",
    "SMTP_SERVER": "smtp.test.com",
}
for _k, _v in _TEST_ENV.items():
    os.environ.setdefault(_k, _v)

from unittest.mock import Mock, patch  # noqa: E402

import pytest  # noqa: E402

from five08.discord_bot.cogs.crm import CRMCog  # noqa: E402


@pytest.fixture
def crm_cog():
    bot = Mock()
    bot.get_cog = Mock()
    with patch("five08.discord_bot.cogs.crm.EspoAPI"):
        cog = CRMCog(bot)
    return cog


def _profile(**kwargs) -> dict:
    base: dict = {"skills": [], "primary_roles": [], "address_country": None}
    base.update(kwargs)
    return base


def test_returns_none_when_no_suggestions(crm_cog) -> None:
    embed = crm_cog._build_role_suggestions_embed(
        contact_name="Alice",
        extracted_profile=_profile(),
    )
    assert embed is None


def test_technical_field_present_for_known_skill(crm_cog) -> None:
    embed = crm_cog._build_role_suggestions_embed(
        contact_name="Alice",
        extracted_profile=_profile(skills=["React"]),
    )
    assert embed is not None
    assert any(f.name == "Technical" for f in embed.fields)


def test_locality_field_present_for_known_country(crm_cog) -> None:
    embed = crm_cog._build_role_suggestions_embed(
        contact_name="Alice",
        extracted_profile=_profile(address_country="Japan"),
    )
    assert embed is not None
    assert any(f.name == "Locality" for f in embed.fields)


def test_both_fields_present_when_both_available(crm_cog) -> None:
    embed = crm_cog._build_role_suggestions_embed(
        contact_name="Alice",
        extracted_profile=_profile(skills=["Kubernetes"], address_country="Nigeria"),
    )
    assert embed is not None
    field_names = [f.name for f in embed.fields]
    assert "Technical" in field_names
    assert "Locality" in field_names


def test_filters_out_all_existing_roles_returns_none(crm_cog) -> None:
    embed = crm_cog._build_role_suggestions_embed(
        contact_name="Alice",
        extracted_profile=_profile(skills=["React"], address_country="Japan"),
        current_discord_roles=["Frontend", "Japan", "Asia"],
    )
    assert embed is None


def test_only_shows_missing_roles(crm_cog) -> None:
    embed = crm_cog._build_role_suggestions_embed(
        contact_name="Alice",
        extracted_profile=_profile(
            skills=["React", "Solidity"], address_country="Japan"
        ),
        current_discord_roles=["Frontend", "Japan", "Asia"],
    )
    # Frontend/Japan/Asia already present; Blockchain missing
    assert embed is not None
    technical_field = next(f for f in embed.fields if f.name == "Technical")
    assert "`Blockchain`" in technical_field.value
    assert "Frontend" not in technical_field.value


def test_never_suggests_excluded_roles(crm_cog) -> None:
    with (
        patch(
            "five08.discord_bot.cogs.crm.suggest_technical_discord_roles"
        ) as mock_tech,
        patch("five08.discord_bot.cogs.crm.suggest_locality_discord_roles") as mock_loc,
    ):
        mock_tech.return_value = ["Member", "Admin", "Backend"]
        mock_loc.return_value = ["508 Bot", "USA"]
        embed = crm_cog._build_role_suggestions_embed(
            contact_name="Alice",
            extracted_profile=_profile(skills=["anything"], address_country="US"),
        )
    assert embed is not None
    technical_field = next(f for f in embed.fields if f.name == "Technical")
    locality_field = next(f for f in embed.fields if f.name == "Locality")
    assert "Member" not in technical_field.value
    assert "Admin" not in technical_field.value
    assert "`Backend`" in technical_field.value
    assert "508 Bot" not in locality_field.value
    assert "`USA`" in locality_field.value


def test_embed_description_mentions_contact_name(crm_cog) -> None:
    embed = crm_cog._build_role_suggestions_embed(
        contact_name="Bob Smith",
        extracted_profile=_profile(skills=["React"]),
    )
    assert embed is not None
    assert "Bob Smith" in embed.description


def test_no_locality_field_when_country_unknown(crm_cog) -> None:
    embed = crm_cog._build_role_suggestions_embed(
        contact_name="Alice",
        extracted_profile=_profile(skills=["React"], address_country="Narnia"),
    )
    assert embed is not None
    field_names = [f.name for f in embed.fields]
    assert "Locality" not in field_names
    assert "Technical" in field_names


def test_no_technical_field_when_no_matching_skills(crm_cog) -> None:
    embed = crm_cog._build_role_suggestions_embed(
        contact_name="Alice",
        extracted_profile=_profile(
            skills=["Underwater Basket Weaving"], address_country="Germany"
        ),
    )
    assert embed is not None
    field_names = [f.name for f in embed.fields]
    assert "Technical" not in field_names
    assert "Locality" in field_names


def test_without_current_roles_shows_all_suggestions(crm_cog) -> None:
    embed = crm_cog._build_role_suggestions_embed(
        contact_name="Alice",
        extracted_profile=_profile(skills=["React"], address_country="USA"),
        current_discord_roles=None,  # unknown — show everything
    )
    assert embed is not None
    field_names = [f.name for f in embed.fields]
    assert "Technical" in field_names
    assert "Locality" in field_names
