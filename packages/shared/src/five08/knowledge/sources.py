"""Permission-aware read adapters for external organizational knowledge."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from psycopg.rows import dict_row

from five08.clients.outline import OutlineClient
from five08.knowledge.models import KnowledgeEvidence
from five08.projects import list_dashboard_projects
from five08.queue import get_postgres_connection
from five08.settings import SharedSettings

_PROJECT_MARKER_RE = re.compile(r"\b(?:project|projects|erp|erpnext)\b", re.I)
_PROJECT_NAME_PATTERNS = (
    re.compile(
        r"\b(?:how\s+is|what\s+is)\s+(?:the\s+)?[\"']?(.+?)[\"']?\s+project(?:'s)?\b",
        re.I,
    ),
    re.compile(r"\bproject\s+(?:named\s+)?[\"']?([^?\"']+)", re.I),
)
_PERSON_MARKER_RE = re.compile(
    r"\b(?:member|person|contact|crm|email|profile|onboarding|skills|phone|who\s+is)\b",
    re.I,
)
_PERSON_QUERY_PATTERNS = (
    re.compile(
        r"\bwhat\s+(?:is|are)\s+(.+?)(?:'s|’s)\s+"
        r"(?:email|profile|onboarding(?:\s+state)?|phone|contact(?:\s+info)?)\b",
        re.I,
    ),
    re.compile(r"\bwhat\s+(?:skills|emails?)\s+does\s+(.+?)\s+have\b", re.I),
    re.compile(r"\bwho\s+is\s+([^?]+)", re.I),
    re.compile(r"\b(?:about|for|on)\s+([^?]+)", re.I),
    re.compile(r"\b(?:member|contact|person)\s+([^?]+)", re.I),
)
_CRM_IDENTITY_FIELDS = (
    "name",
    "email",
    "email_508",
    "discord_username",
    "github_username",
)


class KnowledgeSourceAdapters:
    """Whitelisted source-specific retrieval with no model-owned field access."""

    def __init__(self, settings: SharedSettings) -> None:
        self.settings = settings

    @property
    def _timeout_seconds(self) -> float:
        return max(
            1.0,
            float(getattr(self.settings, "knowledge_source_timeout_seconds", 6.0)),
        )

    def _connection(self) -> Any:
        return get_postgres_connection(
            self.settings,
            connect_timeout_seconds=self._timeout_seconds,
            statement_timeout_seconds=self._timeout_seconds,
        )

    def resolve_actor_emails(self, discord_user_id: str) -> list[str]:
        """Resolve trusted local identities used for project roster filtering."""
        with self._connection() as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT email, email_508
                    FROM people
                    WHERE discord_user_id = %s
                      AND sync_status = 'active'
                    LIMIT 1
                    """,
                    (discord_user_id,),
                )
                row = cursor.fetchone()
        if row is None:
            return []
        return sorted(
            {
                normalized.casefold()
                for key in ("email", "email_508")
                if (normalized := str(row.get(key) or "").strip())
            }
        )

    def accessible_project_ids(
        self,
        *,
        actor_emails: list[str],
        include_all: bool,
    ) -> list[str]:
        rows = list_dashboard_projects(
            self.settings,
            viewer_emails=actor_emails,
            include_all=include_all,
            limit=500,
            include_roster=False,
            timeout_seconds=self._timeout_seconds,
        )
        return [str(row["id"]) for row in rows if row.get("id")]

    def resolve_capture_project(
        self,
        *,
        organization_id: str,
        thread_id: str | None,
    ) -> str | None:
        """Resolve a Discord engagement thread to one canonical project."""
        if not thread_id:
            return None
        with self._connection() as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT DISTINCT p.id::text AS project_id
                    FROM engagements e
                    JOIN project_external_ids pei
                      ON pei.source = 'erpnext'
                     AND pei.external_id = e.erpnext_project_id
                     AND pei.active IS TRUE
                    JOIN projects p ON p.id = pei.project_id
                    WHERE e.discord_guild_id = %s
                      AND e.discord_thread_id = %s
                      AND e.erpnext_project_id IS NOT NULL
                    LIMIT 2
                    """,
                    (organization_id, thread_id),
                )
                rows = cursor.fetchall()
        project_ids = {
            str(row.get("project_id") or "").strip()
            for row in rows
            if str(row.get("project_id") or "").strip()
        }
        if len(project_ids) != 1:
            return None
        return next(iter(project_ids))

    def search_outline(
        self,
        question: str,
        *,
        limit: int = 5,
    ) -> list[KnowledgeEvidence]:
        api_key = (self.settings.outline_contents_api_key or "").strip()
        if not api_key:
            return []
        client = OutlineClient(
            api_key=api_key,
            base_url=self.settings.outline_base_url,
            timeout_seconds=max(
                1.0,
                min(
                    float(self.settings.outline_api_timeout_seconds),
                    float(self.settings.knowledge_source_timeout_seconds),
                ),
            ),
        )
        results = client.search_documents(query=question[:200], limit=limit)
        return [
            KnowledgeEvidence(
                evidence_id=f"outline:{result.document.id}",
                source_type="outline",
                source_ref=result.document.id,
                title=result.document.title[:300],
                excerpt=(result.context or result.document.title)[:4000],
                url=(result.document.url or "")[:1000] or None,
                visibility="org",
                authority=0.95,
                relevance=max(float(result.ranking or 0.0), 0.1),
                updated_at=_parse_datetime(result.document.updated_at),
            )
            for result in results
        ]

    def search_erp_projects(
        self,
        question: str,
        *,
        actor_emails: list[str],
        include_all: bool,
        limit: int = 5,
    ) -> list[KnowledgeEvidence]:
        if _PROJECT_MARKER_RE.search(question) is None:
            return []
        query = _project_query(question)
        if query is None and re.search(r"\bproject(?:'s)?\b", question, re.I):
            return []
        rows = list_dashboard_projects(
            self.settings,
            query=query,
            viewer_emails=actor_emails,
            include_all=include_all,
            limit=limit,
            include_roster=False,
            timeout_seconds=self._timeout_seconds,
        )
        if query:
            rows = _unambiguous_project_rows(rows, query)
        evidence: list[KnowledgeEvidence] = []
        for row in rows:
            display_name = str(row.get("display_name") or "ERPNext project")
            details = [f"Project: {display_name}"]
            for label, key in (
                ("Status", "source_status"),
                ("Customer", "customer"),
                ("Priority", "priority"),
                ("Percent complete", "percent_complete"),
                ("Expected start", "expected_start_date"),
                ("Expected end", "expected_end_date"),
            ):
                value = row.get(key)
                if value is not None and str(value).strip():
                    details.append(f"{label}: {value}")
            project_id = str(row.get("id") or row.get("erpnext_project_id") or "")
            evidence.append(
                KnowledgeEvidence(
                    evidence_id=f"erpnext:{project_id}",
                    source_type="erpnext",
                    source_ref=(
                        f"erpnext:project:{row.get('erpnext_project_id') or project_id}"
                    ),
                    title=display_name[:300],
                    excerpt="; ".join(details)[:4000],
                    visibility="project",
                    authority=1.0,
                    relevance=1.0 if query else 0.5,
                    updated_at=row.get("last_synced_at"),
                )
            )
        return evidence

    def search_crm(
        self,
        question: str,
        *,
        limit: int = 5,
    ) -> list[KnowledgeEvidence]:
        if _PERSON_MARKER_RE.search(question) is None:
            return []
        query = _person_query(question)
        if not query:
            return []
        token = f"%{query}%"
        with self._connection() as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT
                        crm_contact_id,
                        name,
                        email,
                        email_508,
                        discord_username,
                        github_username,
                        skills,
                        contact_type,
                        onboarding_state,
                        is_member,
                        updated_at
                    FROM people
                    WHERE sync_status = 'active'
                      AND (
                          name ILIKE %s
                          OR email ILIKE %s
                          OR email_508 ILIKE %s
                          OR discord_username ILIKE %s
                          OR github_username ILIKE %s
                      )
                    ORDER BY updated_at DESC
                    LIMIT %s
                    """,
                    (token, token, token, token, token, max(2, min(limit, 10))),
                )
                rows = cursor.fetchall()
        return [_crm_evidence(row) for row in _unambiguous_crm_rows(rows, query)]


def _project_query(question: str) -> str | None:
    candidate_text: str | None = None
    for pattern in _PROJECT_NAME_PATTERNS:
        match = pattern.search(question)
        if match is not None:
            candidate_text = match.group(1)
            break
    if candidate_text is None:
        return None
    candidate = re.split(
        r"\b(?:status|schedule|start|end|due|roster|team|doing|at)\b",
        candidate_text,
        maxsplit=1,
        flags=re.I,
    )[0]
    normalized = " ".join(candidate.strip(" .,'\"").split())
    return normalized[:120] or None


def _person_query(question: str) -> str | None:
    for pattern in _PERSON_QUERY_PATTERNS:
        match = pattern.search(question)
        if match is None:
            continue
        candidate = re.split(
            r"\b(?:email|profile|onboarding|skills|phone|contact)\b",
            match.group(1),
            maxsplit=1,
            flags=re.I,
        )[0]
        normalized = " ".join(candidate.strip(" .,'\"").split())
        if normalized:
            return normalized[:120]
    return None


def _unambiguous_project_rows(
    rows: list[dict[str, Any]],
    query: str,
) -> list[dict[str, Any]]:
    """Return one project only when a name query resolves unambiguously."""
    normalized_query = query.strip().casefold()
    exact = [
        row
        for row in rows
        if any(
            str(row.get(field) or "").strip().casefold() == normalized_query
            for field in ("display_name", "erpnext_project_id", "id")
        )
    ]
    if len(exact) == 1:
        return exact
    if exact or len(rows) != 1:
        return []
    return rows


def _unambiguous_crm_rows(
    rows: list[dict[str, Any]],
    query: str,
) -> list[dict[str, Any]]:
    """Return one contact only when the identity match is unambiguous."""
    normalized_query = query.strip().casefold()
    exact = [
        row
        for row in rows
        if any(
            str(row.get(field) or "").strip().casefold() == normalized_query
            for field in _CRM_IDENTITY_FIELDS
        )
    ]
    if len(exact) == 1:
        return exact
    if exact or len(rows) != 1:
        return []
    return rows


def _crm_evidence(row: dict[str, Any]) -> KnowledgeEvidence:
    name = str(row.get("name") or "CRM contact")
    fields = [f"Name: {name}"]
    for label, key in (
        ("Primary email", "email"),
        ("508 email", "email_508"),
        ("Discord", "discord_username"),
        ("GitHub", "github_username"),
        ("Contact type", "contact_type"),
        ("Onboarding state", "onboarding_state"),
        ("Member", "is_member"),
        ("Skills", "skills"),
    ):
        value = row.get(key)
        if value is not None and value != "" and value != []:
            if isinstance(value, list):
                rendered = ", ".join(str(item) for item in value[:12])
            else:
                rendered = str(value)
            fields.append(f"{label}: {rendered}")
    contact_id = str(row.get("crm_contact_id") or "unknown")
    return KnowledgeEvidence(
        evidence_id=f"crm:{contact_id}",
        source_type="crm",
        source_ref=f"crm:contact:{contact_id}",
        title=name[:300],
        excerpt="; ".join(fields)[:4000],
        visibility="private",
        authority=1.0,
        relevance=1.0,
        updated_at=row.get("updated_at"),
    )


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
