"""Unit tests for shared audit helpers."""

from unittest.mock import MagicMock, patch

from five08.audit import get_discord_user_id_for_contact, upsert_person_discord_link


def _mock_connection(row: dict[str, object] | None) -> MagicMock:
    connection = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.return_value = row
    connection.cursor.return_value.__enter__.return_value = cursor
    connection.cursor.return_value.__exit__.return_value = None
    return connection


def test_get_discord_user_id_for_contact_uses_people_cache() -> None:
    """Prefer the synced people cache when it already has a Discord ID."""
    settings = MagicMock()
    connection = _mock_connection({"discord_user_id": "123456789"})

    with (
        patch("five08.audit.get_postgres_connection") as mock_get_connection,
        patch("five08.audit.EspoClient") as mock_espo_client,
    ):
        mock_get_connection.return_value.__enter__.return_value = connection
        mock_get_connection.return_value.__exit__.return_value = None

        result = get_discord_user_id_for_contact(settings, "contact-1")

    assert result == "123456789"
    mock_espo_client.assert_not_called()


def test_upsert_person_discord_link_updates_local_people_cache() -> None:
    """Discord link commands should make the local cache immediately usable."""
    settings = MagicMock()
    connection = _mock_connection({"id": "person-1"})

    with patch("five08.audit.get_postgres_connection") as mock_get_connection:
        mock_get_connection.return_value.__enter__.return_value = connection
        mock_get_connection.return_value.__exit__.return_value = None

        result = upsert_person_discord_link(
            settings,
            crm_contact_id=" contact-1 ",
            discord_user_id=" 123456789 ",
            discord_username="mootester117",
            name="Moo Tester",
            email="MOO@example.com",
            email_508="MOO@508.dev",
        )

    assert result == "person-1"
    cursor = connection.cursor.return_value.__enter__.return_value
    clear_call, upsert_call = cursor.execute.call_args_list
    assert "UPDATE people" in clear_call.args[0]
    assert clear_call.args[1] == ("123456789", "contact-1")
    assert "INSERT INTO people" in upsert_call.args[0]
    assert upsert_call.args[1][1:7] == (
        "contact-1",
        "Moo Tester",
        "moo@example.com",
        "moo@508.dev",
        "123456789",
        "mootester117",
    )


def test_get_discord_user_id_for_contact_ignores_cached_no_discord() -> None:
    """The cache sentinel should not be returned as a valid Discord ID."""
    settings = MagicMock()
    settings.espo_base_url = ""
    settings.espo_api_key = ""
    connection = _mock_connection({"discord_user_id": "No Discord"})

    with (
        patch("five08.audit.get_postgres_connection") as mock_get_connection,
        patch("five08.audit.EspoClient") as mock_espo_client,
    ):
        mock_get_connection.return_value.__enter__.return_value = connection
        mock_get_connection.return_value.__exit__.return_value = None

        result = get_discord_user_id_for_contact(settings, "contact-1")

    assert result is None
    mock_espo_client.assert_not_called()


def test_get_discord_user_id_for_contact_falls_back_to_crm_contact() -> None:
    """When people sync is stale, fall back to the live CRM contact fields."""
    settings = MagicMock()
    settings.espo_base_url = "https://crm.example.com"
    settings.espo_api_key = "secret"
    connection = _mock_connection(None)

    with (
        patch("five08.audit.get_postgres_connection") as mock_get_connection,
        patch("five08.audit.EspoClient") as mock_espo_client,
    ):
        mock_get_connection.return_value.__enter__.return_value = connection
        mock_get_connection.return_value.__exit__.return_value = None
        mock_espo_client.return_value.get_contact.return_value = {
            "id": "contact-1",
            "cDiscordUserID": "987654321",
        }

        result = get_discord_user_id_for_contact(settings, "contact-1")

    assert result == "987654321"
    mock_espo_client.return_value.get_contact.assert_called_once_with("contact-1")


def test_get_discord_user_id_for_contact_parses_legacy_crm_username() -> None:
    """Legacy embedded ID formats should still enable role application."""
    settings = MagicMock()
    settings.espo_base_url = "https://crm.example.com"
    settings.espo_api_key = "secret"
    connection = _mock_connection({"discord_user_id": None})

    with (
        patch("five08.audit.get_postgres_connection") as mock_get_connection,
        patch("five08.audit.EspoClient") as mock_espo_client,
    ):
        mock_get_connection.return_value.__enter__.return_value = connection
        mock_get_connection.return_value.__exit__.return_value = None
        mock_espo_client.return_value.get_contact.return_value = {
            "id": "contact-1",
            "cDiscordUsername": "janedoe#1234 (ID: 555666777)",
        }

        result = get_discord_user_id_for_contact(settings, "contact-1")

    assert result == "555666777"
