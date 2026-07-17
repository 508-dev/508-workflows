from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import Mock, patch

import five08.projects as projects_module
import pytest
import requests

from five08.projects import (
    best_wiki_match,
    _cached_active_supplier_id_for_email,
    erpnext_project_to_input,
    fetch_outline_document,
    list_dashboard_projects,
    mark_missing_erpnext_open_projects_not_open,
    normalize_match_text,
    parse_project_wiki_tables,
    project_viewer_emails_for_discord,
    ProjectInput,
    upsert_project,
)
from five08.settings import SharedSettings


def test_erpnext_project_to_input_maps_users_roster() -> None:
    payload = erpnext_project_to_input(
        {
            "name": "PROJ-0018",
            "project_name": "VoyTravel",
            "status": "Open",
            "customer": "VoyTravel",
            "priority": "Medium",
            "percent_complete": 25.5,
            "actual_start_date": "2026-01-02",
            "modified": "2026-05-19 17:08:08.013010",
            "users": [
                {
                    "user": "michael@508.dev",
                    "email": "michael@508.dev",
                    "full_name": "Michael Wu",
                }
            ],
        }
    )

    assert payload is not None
    assert payload.external_id == "PROJ-0018"
    assert payload.display_name == "VoyTravel"
    assert payload.customer == "VoyTravel"
    assert payload.percent_complete == 25.5
    assert payload.actual_start_date is not None
    assert payload.source_modified_at is not None
    assert payload.roster_members is not None
    assert payload.roster_members[0].source_user_id == "michael@508.dev"
    assert payload.roster_members[0].full_name == "Michael Wu"


def test_project_wiki_table_parser_and_fuzzy_match() -> None:
    rows = parse_project_wiki_tables(
        """
## Current Projects

| Client | Description | Timeline | Rough Budget | Started Date | **DRI** | Members | Updated | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Voy Travel | Travel app | Q1 | $10k | Jan | Michael | Kevin, Fabien | May | Active |
| Adventure Cow LLC | Game prototype | Q2 | $5k | Feb | Roberto | Roberto | May | Active |
"""
    )

    assert len(rows) == 2
    assert rows[0]["Client"] == "Voy Travel"
    assert rows[0]["section"] == "Current Projects"

    match = best_wiki_match(
        {
            "display_name": "VoyTravel",
            "customer": "VoyTravel",
            "erpnext_project_id": "PROJ-0018",
        },
        rows,
    )

    assert match is not None
    assert match["confidence"] in {"medium", "high"}
    assert match["row"]["Client"] == "Voy Travel"


def test_normalize_match_text_strips_urls() -> None:
    assert normalize_match_text("VoyTravel https://example.com/private Roadmap") == (
        "voy travel roadmap"
    )


def test_project_viewer_emails_for_discord_returns_active_people_emails() -> None:
    class Cursor:
        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def execute(self, query: str, params: tuple[str]) -> None:
            assert "discord_user_id = %s" in query
            assert params == ("123456789",)

        def fetchall(self) -> list[dict[str, str | None]]:
            return [
                {"email": "Member@Example.com", "email_508": "member@508.dev"},
                {"email": "member@example.com", "email_508": None},
            ]

    class Connection:
        def __enter__(self) -> Connection:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def cursor(self, *args: object, **kwargs: object) -> Cursor:
            return Cursor()

    with patch("five08.projects.get_postgres_connection", return_value=Connection()):
        emails = project_viewer_emails_for_discord(SharedSettings(), "123456789")

    assert emails == ["member@508.dev", "member@example.com"]


def test_mark_missing_erpnext_open_projects_closes_cached_projects_for_empty_sync() -> (
    None
):
    cursor = Mock()
    cursor.rowcount = 2
    connection = Mock()
    connection.__enter__ = Mock(return_value=connection)
    connection.__exit__ = Mock(return_value=None)
    cursor_context = Mock()
    cursor_context.__enter__ = Mock(return_value=cursor)
    cursor_context.__exit__ = Mock(return_value=None)
    connection.cursor.return_value = cursor_context

    with patch("five08.projects.get_postgres_connection", return_value=connection):
        result = mark_missing_erpnext_open_projects_not_open(SharedSettings(), [])

    assert result == 2
    query, params = cursor.execute.call_args.args
    assert "NOT (pei.external_id = ANY" in query
    assert params[-1] == []


def test_stale_erpnext_project_payload_cannot_overwrite_newer_cache_or_roster() -> None:
    """A delayed Open fetch must not reopen an action-time Closed refresh."""
    project_id = "00000000-0000-0000-0000-000000000001"
    cursor = Mock()
    cursor.fetchone.side_effect = [
        {"project_id": project_id},
        None,
    ]
    connection = Mock()
    connection.__enter__ = Mock(return_value=connection)
    connection.__exit__ = Mock(return_value=None)
    cursor_context = Mock()
    cursor_context.__enter__ = Mock(return_value=cursor)
    cursor_context.__exit__ = Mock(return_value=None)
    connection.cursor.return_value = cursor_context
    stale_open = ProjectInput(
        source="erpnext",
        external_id="PROJ-001",
        display_name="Project",
        source_status="Open",
        source_modified_at=datetime(2026, 7, 16, tzinfo=timezone.utc),
    )

    with patch("five08.projects.get_postgres_connection", return_value=connection):
        result = upsert_project(SharedSettings(), stale_open)

    assert result == project_id
    update_query, update_params = cursor.execute.call_args_list[2].args
    assert "OR %s > projects.source_modified_at" in update_query
    assert update_params[-2:] == (
        stale_open.source_modified_at,
        stale_open.source_modified_at,
    )
    executed_queries = [call.args[0] for call in cursor.execute.call_args_list]
    assert not any(
        "INSERT INTO project_roster_members" in query for query in executed_queries
    )
    assert not any(
        "INSERT INTO project_external_ids" in query for query in executed_queries
    )


def test_fetch_outline_document_wraps_transport_errors() -> None:
    settings = SharedSettings(outline_api_key="outline-key")
    with patch(
        "five08.projects.requests.post",
        side_effect=requests.Timeout("timed out"),
    ):
        with pytest.raises(ValueError, match="Outline document fetch failed"):
            fetch_outline_document(settings, document_id="doc-1")


def test_list_dashboard_projects_visibility_requires_erp_roster_row() -> None:
    executed: list[str] = []

    class Cursor:
        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def execute(self, query: str, params: object) -> None:
            executed.append(query)

        def fetchall(self) -> list[dict[str, object]]:
            return []

    class Connection:
        def __enter__(self) -> Connection:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def cursor(self, *args: object, **kwargs: object) -> Cursor:
            return Cursor()

    with patch("five08.projects.get_postgres_connection", return_value=Connection()):
        list_dashboard_projects(
            SharedSettings(),
            viewer_emails=["member@508.dev"],
            include_all=False,
        )

    assert "visible_prm.source = 'erpnext'" in executed[0]
    assert "visible_prm.roster_kind = 'erp_users'" in executed[0]


def test_list_dashboard_projects_does_not_guess_supplier_link_from_full_name() -> None:
    project_id = "11111111-1111-4111-8111-111111111111"

    class Cursor:
        def __init__(self) -> None:
            self.call_count = 0

        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def execute(self, query: str, params: object) -> None:
            self.call_count += 1

        def fetchall(self) -> list[dict[str, object]]:
            if self.call_count == 1:
                return [
                    {
                        "id": project_id,
                        "display_name": "Project",
                        "customer": None,
                        "source_status": "Open",
                        "project_type": "Internal",
                        "percent_complete": None,
                        "expected_start_date": None,
                        "expected_end_date": None,
                        "actual_start_date": None,
                        "actual_end_date": None,
                        "source_modified_at": None,
                        "last_synced_at": None,
                        "erpnext_project_id": "PROJ-001",
                        "manual_wiki_match_status": None,
                        "manual_wiki_row_key": None,
                        "manual_wiki_row_label": None,
                        "manual_wiki_row_section": None,
                        "linked_engagement_count": 0,
                    }
                ]
            return [
                {
                    "project_id": project_id,
                    "source": "erpnext",
                    "source_user_id": "fabien@508.dev",
                    "email": "fabien@508.dev",
                    "full_name": "Fabien Rajaonarison",
                    "roster_kind": "erp_users",
                    "source_payload": {},
                    "last_seen_at": None,
                    "crm_contact_id": "crm-fabien",
                }
            ]

    class Connection:
        def __enter__(self) -> Connection:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def cursor(self, *args: object, **kwargs: object) -> Cursor:
            return Cursor()

    settings = SharedSettings(erpnext_base_url="https://erp.example.test")
    with patch("five08.projects.get_postgres_connection", return_value=Connection()):
        projects = list_dashboard_projects(settings, include_all=True)

    member = projects[0]["roster_members"][0]
    assert member["supplier_erpnext_url"] is None
    assert "source_payload" not in member


def test_list_dashboard_projects_uses_stored_supplier_link() -> None:
    project_id = "11111111-1111-4111-8111-111111111111"

    class Cursor:
        def __init__(self) -> None:
            self.call_count = 0

        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def execute(self, query: str, params: object) -> None:
            self.call_count += 1

        def fetchall(self) -> list[dict[str, object]]:
            if self.call_count == 1:
                return [
                    {
                        "id": project_id,
                        "display_name": "Project",
                        "customer": None,
                        "source_status": "Open",
                        "project_type": "Internal",
                        "percent_complete": None,
                        "expected_start_date": None,
                        "expected_end_date": None,
                        "actual_start_date": None,
                        "actual_end_date": None,
                        "source_modified_at": None,
                        "last_synced_at": None,
                        "erpnext_project_id": "PROJ-001",
                        "manual_wiki_match_status": None,
                        "manual_wiki_row_key": None,
                        "manual_wiki_row_label": None,
                        "manual_wiki_row_section": None,
                        "linked_engagement_count": 0,
                    }
                ]
            return [
                {
                    "project_id": project_id,
                    "source": "erpnext",
                    "source_user_id": "fabien@508.dev",
                    "email": "fabien@508.dev",
                    "full_name": "Fabien Rajaonarison",
                    "roster_kind": "erp_users",
                    "source_payload": {"supplier_erpnext_id": "NAINA CONSULTING"},
                    "last_seen_at": None,
                    "crm_contact_id": "crm-fabien",
                    "crm_email": "fabien@naina.digital",
                    "crm_email_508": "fabien@508.dev",
                }
            ]

    class Connection:
        def __enter__(self) -> Connection:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def cursor(self, *args: object, **kwargs: object) -> Cursor:
            return Cursor()

    settings = SharedSettings(
        erpnext_base_url="https://erp.example.test",
        erpnext_api_key="key:secret",
    )
    with (
        patch("five08.projects.get_postgres_connection", return_value=Connection()),
        patch("five08.projects.ERPNextClient") as mock_erpnext_client,
    ):
        projects = list_dashboard_projects(settings, include_all=True)

    member = projects[0]["roster_members"][0]
    mock_erpnext_client.assert_not_called()
    assert (
        member["supplier_erpnext_url"]
        == "https://erp.example.test/app/supplier/NAINA%20CONSULTING"
    )
    assert "crm_email" not in member
    assert "crm_email_508" not in member


def test_supplier_lookup_caches_erpnext_failures() -> None:
    class FailingERPNextClient:
        calls = 0

        def __init__(
            self,
            base_url: str,
            api_key: str,
            timeout_seconds: float,
        ) -> None:
            self.base_url = base_url

        def search_suppliers(
            self,
            query: str,
            *,
            limit: int,
        ) -> list[dict[str, object]]:
            type(self).calls += 1
            raise projects_module.ERPNextAPIError("ERP unavailable")

        def close(self) -> None:
            return None

    projects_module._SUPPLIER_EMAIL_CACHE.clear()
    settings = SharedSettings(
        erpnext_base_url="https://erp.example.test",
        erpnext_api_key="key:secret",
    )
    with patch("five08.projects.ERPNextClient", FailingERPNextClient):
        assert (
            _cached_active_supplier_id_for_email(settings, "fabien@naina.digital")
            is None
        )
        assert (
            _cached_active_supplier_id_for_email(settings, "fabien@naina.digital")
            is None
        )

    assert FailingERPNextClient.calls == 1
