"""ERPNext project sync into the local project cache."""

from __future__ import annotations

import logging
from typing import Any

from five08.clients.erpnext import ERPNextClient, ERPNextAPIError
from five08.projects import (
    erpnext_project_to_input,
    mark_missing_erpnext_open_projects_not_open,
    upsert_project,
)
from five08.worker.config import settings

logger = logging.getLogger(__name__)


class ERPNextProjectSyncProcessor:
    """Synchronize ERPNext Project records into local Postgres tables."""

    def __init__(self) -> None:
        base_url = (settings.erpnext_base_url or "").strip()
        api_key = (settings.erpnext_api_key or "").strip()
        if not base_url or not api_key:
            raise ValueError("ERPNEXT_BASE_URL and ERPNEXT_API_KEY must be configured")
        self.client = ERPNextClient(
            base_url,
            api_key,
            timeout_seconds=settings.erpnext_api_timeout_seconds,
        )

    def sync_open_projects(self) -> dict[str, Any]:
        """Sync all open ERPNext projects and their Project.users roster."""
        project_limit = 500
        synced_count = 0
        failed_project_ids: list[str] = []
        roster_member_count = 0
        stale_open_count = 0
        seen_project_ids: list[str] = []
        seen_count = 0

        try:
            try:
                offset = 0
                while True:
                    page = self.client.list_projects(
                        status="Open",
                        limit=project_limit,
                        offset=offset,
                    )
                    seen_count += len(page)
                    for project_ref in page:
                        project_id = str(project_ref.get("name") or "").strip()
                        if not project_id:
                            failed_project_ids.append("missing-project-id")
                            continue
                        seen_project_ids.append(project_id)
                        try:
                            detail = self.client.get_project(project_id)
                            payload = erpnext_project_to_input(detail)
                            if payload is None:
                                failed_project_ids.append(project_id)
                                continue
                            upsert_project(settings, payload)
                            synced_count += 1
                            roster_member_count += len(payload.roster_members or [])
                        except Exception as exc:
                            logger.warning(
                                "Failed syncing ERPNext project id=%s: %s",
                                project_id,
                                exc,
                            )
                            failed_project_ids.append(project_id)
                    if len(page) < project_limit:
                        break
                    offset += project_limit
            except ERPNextAPIError as exc:
                logger.error("Failed listing ERPNext projects: %s", exc)
                raise

            stale_open_count = mark_missing_erpnext_open_projects_not_open(
                settings,
                seen_project_ids,
            )
        finally:
            self.client.close()

        return {
            "source": "erpnext",
            "status_filter": "Open",
            "seen_count": seen_count,
            "synced_count": synced_count,
            "failed_count": len(failed_project_ids),
            "failed_project_ids": failed_project_ids,
            "roster_member_count": roster_member_count,
            "stale_open_count": stale_open_count,
        }
