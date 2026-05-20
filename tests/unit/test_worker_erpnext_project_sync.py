from __future__ import annotations

from typing import Any

from five08.worker import erpnext_project_sync


class FakeERPNextClient:
    def __init__(self, pages: list[list[dict[str, Any]]]) -> None:
        self.pages = pages
        self.list_offsets: list[int] = []
        self.closed = False

    def list_projects(
        self,
        *,
        status: str,
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        assert status == "Open"
        assert limit == erpnext_project_sync.PROJECT_LIST_PAGE_LIMIT
        self.list_offsets.append(offset)
        index = offset // limit
        if index >= len(self.pages):
            return []
        return self.pages[index]

    def get_project(self, project_id: str) -> dict[str, Any]:
        return {
            "name": project_id,
            "project_name": "VoyTravel",
            "status": "Open",
            "users": [{"user": "member@508.dev", "email": "member@508.dev"}],
        }

    def close(self) -> None:
        self.closed = True


def test_sync_open_projects_closes_client_and_marks_missing_open_projects(
    monkeypatch,
) -> None:
    client = FakeERPNextClient([[{"name": "PROJ-001"}]])
    processor = erpnext_project_sync.ERPNextProjectSyncProcessor.__new__(
        erpnext_project_sync.ERPNextProjectSyncProcessor
    )
    processor.client = client
    upserted: list[str] = []

    monkeypatch.setattr(
        erpnext_project_sync,
        "upsert_project",
        lambda _settings, payload: upserted.append(payload.external_id),
    )
    monkeypatch.setattr(
        erpnext_project_sync,
        "mark_missing_erpnext_open_projects_not_open",
        lambda _settings, seen_project_ids: (
            2 if seen_project_ids == ["PROJ-001"] else 0
        ),
    )

    result = processor.sync_open_projects()

    assert client.closed is True
    assert upserted == ["PROJ-001"]
    assert result["stale_open_count"] == 2


def test_sync_open_projects_paginates_until_short_page(
    monkeypatch,
) -> None:
    page_limit = erpnext_project_sync.PROJECT_LIST_PAGE_LIMIT
    client = FakeERPNextClient(
        [
            [{"name": f"PROJ-{index:03d}"} for index in range(page_limit)],
            [{"name": f"PROJ-{page_limit:03d}"}],
        ]
    )
    processor = erpnext_project_sync.ERPNextProjectSyncProcessor.__new__(
        erpnext_project_sync.ERPNextProjectSyncProcessor
    )
    processor.client = client
    stale_mark_calls: list[list[str]] = []

    monkeypatch.setattr(erpnext_project_sync, "upsert_project", lambda *_args: None)
    monkeypatch.setattr(
        erpnext_project_sync,
        "mark_missing_erpnext_open_projects_not_open",
        lambda _settings, seen_project_ids: (
            stale_mark_calls.append(seen_project_ids) or 0
        ),
    )

    result = processor.sync_open_projects()

    assert client.closed is True
    assert result["seen_count"] == page_limit + 1
    assert result["stale_open_count"] == 0
    assert client.list_offsets == [0, page_limit]
    assert stale_mark_calls == [
        [f"PROJ-{index:03d}" for index in range(page_limit)]
        + [f"PROJ-{page_limit:03d}"]
    ]
