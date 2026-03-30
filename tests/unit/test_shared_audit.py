"""Unit tests for shared audit helpers."""

from unittest.mock import MagicMock, patch

from five08.audit import get_discord_user_id_for_contact


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
