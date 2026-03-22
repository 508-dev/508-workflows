from __future__ import annotations

from typing import Any

from five08.crm_contacts import FROM_LOCATION, EspoContactRepository


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
        timezone_empty=True,
        location_present=True,
        member_type=["Member"],
    )

    assert [contact.id for contact in contacts] == ["contact-1"]
    assert client.list_calls[0]["where"] == [
        {"type": "equals", "attribute": "type", "value": "Member"},
    ]


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
        role="developer",
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
        search={"timezone_empty": True, "location_present": True},
        updates={"timezone": FROM_LOCATION},
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
