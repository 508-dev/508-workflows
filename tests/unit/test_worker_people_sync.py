"""Unit tests for CRM people sync normalizers."""

from five08.worker.crm.people_sync import PeopleSyncProcessor


def test_to_person_record_parses_discord_snapshot_fields() -> None:
    """People sync should parse Discord display + ID and role list."""
    processor = PeopleSyncProcessor()

    person = processor._to_person_record(
        {
            "id": "contact-1",
            "name": "Jane Doe",
            "emailAddress": "jane@example.com",
            "c508Email": "jane@508.dev",
            "cDiscordUsername": "jane#1234 (ID: 987654321)",
            "cDiscordRoles": "Member, Admin",
            "cGithubUsername": "janedoe",
        }
    )

    assert person is not None
    assert person.crm_contact_id == "contact-1"
    assert person.discord_username == "jane#1234"
    assert person.discord_user_id == "987654321"
    assert person.discord_roles == ["Member", "Admin"]
    assert person.github_username == "janedoe"


def test_email_falls_back_to_email_address_data() -> None:
    """People sync should use primary emailAddressData when emailAddress is missing."""
    processor = PeopleSyncProcessor()

    person = processor._to_person_record(
        {
            "id": "contact-2",
            "name": "John Doe",
            "emailAddressData": [
                {"emailAddress": "secondary@example.com", "primary": False},
                {"emailAddress": "primary@example.com", "primary": True},
            ],
        }
    )

    assert person is not None
    assert person.email == "primary@example.com"
