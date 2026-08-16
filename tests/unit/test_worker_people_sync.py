"""Unit tests for CRM people sync normalizers."""

from unittest.mock import MagicMock

from five08.worker.crm.people_sync import EspoPeopleSyncClient, PeopleSyncProcessor


def test_list_contact_page_requests_address_state() -> None:
    """Full-sync contact fetches should include dashboard cache fields."""
    client = EspoPeopleSyncClient()
    client.api = MagicMock()
    client.api.request.return_value = {"list": [], "total": 0}

    client.list_contact_page(offset=0, max_size=100)

    request_params = client.api.request.call_args.args[2]
    assert "addressState" in request_params["select"]
    assert "cOnboardingState" in request_params["select"]
    assert "cOnboarder" in request_params["select"]
    assert "cOnboardingUpdatedAt" in request_params["select"]
    assert "description" in request_params["select"]


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
            "addressState": "Washington",
            "cOnboardingState": "Selected",
            "cOnboarder": "michael",
            "cOnboardingUpdatedAt": "2026-05-08 10:03:00",
            "description": "Backend engineer focused on reliable integrations.",
        }
    )

    assert person is not None
    assert person.crm_contact_id == "contact-1"
    assert person.discord_username == "jane#1234"
    assert person.discord_user_id == "987654321"
    assert person.discord_roles == ["Member", "Admin"]
    assert person.github_username == "janedoe"
    assert person.address_state == "Washington"
    assert person.onboarding_state == "Selected"
    assert person.onboarder == "michael"
    assert person.onboarding_updated_at is not None
    assert (
        person.profile_summary == "Backend engineer focused on reliable integrations."
    )


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
