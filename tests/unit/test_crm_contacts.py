from __future__ import annotations

from typing import Any

import pytest

from five08.crm_contacts import Contact, FROM_LOCATION, EspoContactRepository


class FakeEspoClient:
    def __init__(self, pages: list[dict[str, Any]] | None = None) -> None:
        self.pages = list(pages or [])
        self.list_calls: list[dict[str, Any]] = []
        self.update_calls: list[tuple[str, dict[str, Any]]] = []
        self.get_calls: list[str] = []
        self.contacts_by_id: dict[str, dict[str, Any]] = {}

    def list_contacts(self, params: dict[str, Any]) -> dict[str, Any]:
        self.list_calls.append(params)
        if self.pages:
            return self.pages.pop(0)
        return {"list": [], "total": 0}

    def update_contact(
        self, contact_id: str, updates: dict[str, Any]
    ) -> dict[str, Any]:
        self.update_calls.append((contact_id, updates))
        current = dict(self.contacts_by_id.get(contact_id, {"id": contact_id}))
        current.update(updates)
        self.contacts_by_id[contact_id] = current
        return current

    def get_contact(self, contact_id: str) -> dict[str, Any]:
        self.get_calls.append(contact_id)
        return dict(self.contacts_by_id[contact_id])


def test_search_builds_remote_filters_and_applies_local_location_filter() -> None:
    client = FakeEspoClient(
        pages=[
            {
                "list": [
                    {
                        "id": "contact-1",
                        "name": "Alice",
                        "type": "Member",
                        "cTimezone": "",
                        "addressCountry": "Germany",
                    },
                    {
                        "id": "contact-2",
                        "name": "Bob",
                        "type": "Member",
                        "cTimezone": "",
                        "addressCountry": "",
                    },
                ],
                "total": 2,
            }
        ]
    )
    repo = EspoContactRepository(client)

    contacts = repo.search(
        timezone="empty",
        location="present",
        member_type=["Member"],
    )

    assert [contact.id for contact in contacts] == ["contact-1"]
    assert "where" not in client.list_calls[0]


def test_search_filters_by_role_and_phone_country_code_locally() -> None:
    client = FakeEspoClient(
        pages=[
            {
                "list": [
                    {
                        "id": "contact-1",
                        "name": "Alice",
                        "cRoles": ["developer"],
                        "phoneNumber": "5551212",
                    },
                    {
                        "id": "contact-2",
                        "name": "Bob",
                        "cRoles": ["developer"],
                        "phoneNumber": "+1 5551212",
                    },
                    {
                        "id": "contact-3",
                        "name": "Carol",
                        "cRoles": ["designer"],
                        "phoneNumber": "5551213",
                    },
                ],
                "total": 3,
            }
        ]
    )
    repo = EspoContactRepository(client)

    contacts = repo.search(
        roles="developer",
        phone_country_code="+1",
        phone_missing_country_code=True,
    )

    assert [contact.id for contact in contacts] == ["contact-1"]
    assert "where" not in client.list_calls[0]


def test_search_matches_raw_timezone_when_normalization_fails() -> None:
    client = FakeEspoClient(
        pages=[
            {
                "list": [
                    {"id": "contact-1", "name": "Alice", "cTimezone": "EST"},
                    {"id": "contact-2", "name": "Bob", "cTimezone": "PST"},
                ],
                "total": 2,
            }
        ]
    )
    repo = EspoContactRepository(client)

    contacts = repo.search(timezone="EST")

    assert [contact.id for contact in contacts] == ["contact-1"]


def test_search_supports_generic_field_operator_filters() -> None:
    client = FakeEspoClient(
        pages=[
            {
                "list": [
                    {
                        "id": "contact-1",
                        "name": "Alice",
                        "type": "Member",
                        "cSeniority": "senior",
                        "phoneNumber": "+1 5551212",
                    },
                    {
                        "id": "contact-2",
                        "name": "Bob",
                        "type": "Inactive Member",
                        "cSeniority": "junior",
                        "phoneNumber": "5551212",
                    },
                ],
                "total": 2,
            }
        ]
    )
    repo = EspoContactRepository(client)

    contacts = repo.search(
        member_type__in=["Member", "Prospect"],
        seniority__not_equals="junior",
        phone__starts_with="+1",
    )

    assert [contact.id for contact in contacts] == ["contact-1"]


def test_search_supports_like_wildcards() -> None:
    client = FakeEspoClient(
        pages=[
            {
                "list": [
                    {"id": "contact-1", "name": "Alice", "phoneNumber": "+1 5551212"},
                    {"id": "contact-2", "name": "Bob", "phoneNumber": "5551212"},
                ],
                "total": 2,
            }
        ]
    )
    repo = EspoContactRepository(client)

    contacts = repo.search(phone__not_like="+1%")

    assert [contact.id for contact in contacts] == ["contact-2"]


def test_search_supports_compound_location_filters() -> None:
    client = FakeEspoClient(
        pages=[
            {
                "list": [
                    {
                        "id": "contact-1",
                        "name": "Alice",
                        "addressCity": "Berlin",
                        "addressCountry": "Germany",
                    },
                    {
                        "id": "contact-2",
                        "name": "Bob",
                        "addressCity": "",
                        "addressCountry": "",
                    },
                ],
                "total": 2,
            }
        ]
    )
    repo = EspoContactRepository(client)

    contacts = repo.search(location__contains="berlin")

    assert [contact.id for contact in contacts] == ["contact-1"]


def test_search_limit_zero_means_no_limit() -> None:
    client = FakeEspoClient(
        pages=[
            {
                "list": [
                    {"id": "contact-1", "name": "Alice"},
                    {"id": "contact-2", "name": "Bob"},
                ],
                "total": 2,
            }
        ]
    )
    repo = EspoContactRepository(client)

    contacts = repo.search(limit=0)

    assert [contact.id for contact in contacts] == ["contact-1", "contact-2"]


def test_search_adds_required_fields_to_custom_select() -> None:
    client = FakeEspoClient(
        pages=[
            {
                "list": [
                    {
                        "id": "contact-1",
                        "name": "Alice",
                        "phoneNumber": "+1 5551212",
                    }
                ],
                "total": 1,
            }
        ]
    )
    repo = EspoContactRepository(client)

    contacts = repo.search(
        select="id,name",
        phone__starts_with="+1",
    )

    assert [contact.id for contact in contacts] == ["contact-1"]
    assert client.list_calls[0]["select"] == "id,name,phoneNumber"


def test_batch_update_infers_timezone_from_location() -> None:
    client = FakeEspoClient(
        pages=[
            {
                "list": [
                    {
                        "id": "contact-1",
                        "name": "Alice",
                        "cTimezone": "",
                        "addressCity": "Berlin",
                        "addressCountry": "Germany",
                    }
                ],
                "total": 1,
            }
        ]
    )
    repo = EspoContactRepository(client)

    result = repo.batch_update(
        where={"timezone": "empty", "location": "present"},
        update={"timezone": FROM_LOCATION},
        apply=True,
    )

    assert result.applied is True
    assert result.count == 1
    assert client.update_calls == [("contact-1", {"cTimezone": "UTC+01:00"})]


def test_prepare_contact_updates_skips_timezone_when_inference_fails() -> None:
    client = FakeEspoClient()
    repo = EspoContactRepository(client)

    updates = repo.prepare_contact_updates(
        current_values={"cTimezone": "UTC-05:00"},
        updates={"timezone": FROM_LOCATION},
    )

    assert "cTimezone" not in updates


def test_prepare_contact_updates_rejects_invalid_timezone_value() -> None:
    client = FakeEspoClient()
    repo = EspoContactRepository(client)

    with pytest.raises(ValueError, match="Invalid timezone value"):
        repo.prepare_contact_updates(
            current_values={"cTimezone": "UTC-05:00"},
            updates={"timezone": "EST"},
        )


def test_search_by_phone_country_code_defaults_to_present_prefix() -> None:
    client = FakeEspoClient(
        pages=[
            {
                "list": [
                    {"id": "contact-1", "name": "Alice", "phoneNumber": "5551212"},
                    {"id": "contact-2", "name": "Bob", "phoneNumber": "+1 5551212"},
                ],
                "total": 2,
            }
        ]
    )
    repo = EspoContactRepository(client)

    contacts = repo.search(phone_country_code="+1")

    assert [contact.id for contact in contacts] == ["contact-2"]


def test_contact_object_tracks_alias_updates_and_saves_normalized_roles() -> None:
    client = FakeEspoClient()
    client.contacts_by_id["contact-1"] = {
        "id": "contact-1",
        "name": "Alice",
        "cRoles": [],
    }
    repo = EspoContactRepository(client)
    contact = repo.get("contact-1")

    contact.roles = "Developer, Data Scientist"
    assert contact.pending_updates == {
        "cRoles": ["developer", "data scientist"],
    }

    contact.save()

    assert client.update_calls == [
        ("contact-1", {"cRoles": ["developer", "data scientist"]})
    ]


def test_contact_rejects_setting_class_defined_attributes() -> None:
    client = FakeEspoClient()
    client.contacts_by_id["contact-1"] = {"id": "contact-1", "name": "Alice"}
    repo = EspoContactRepository(client)
    contact = repo.get("contact-1")
    dynamic_contact: Any = contact

    with pytest.raises(AttributeError, match="Cannot set attribute 'id'"):
        dynamic_contact.id = "contact-2"


def test_contact_save_fails_when_payload_has_no_id() -> None:
    client = FakeEspoClient()
    repo = EspoContactRepository(client)
    raw_contact = {
        "name": "Alice",
        "cRoles": [],
    }

    contact = Contact(repo, raw_contact)
    contact.roles = "Developer"

    with pytest.raises(ValueError, match="missing required id"):
        contact.save()
