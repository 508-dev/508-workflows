"""Playwright integration tests for the operations dashboard UI."""

from __future__ import annotations

import json
import os
import re
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse
from unittest.mock import Mock

import pytest
import uvicorn

from five08.backend import api

pytestmark = pytest.mark.playwright


class _BrowserAuthStore(api.RedisAuthStore):
    def __init__(self, session: api.AuthSession) -> None:
        self.session = session

    async def get_session(self, session_id: str) -> api.AuthSession | None:
        if session_id == "session-1":
            return self.session
        return None

    async def delete_session(self, session_id: str) -> None:
        return None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_port(port: int) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise RuntimeError(f"Timed out waiting for dashboard test server on {port}")


@pytest.fixture
def dashboard_server() -> Iterator[str]:
    session = api.AuthSession(
        subject="123456789",
        email="admin@508.dev",
        display_name="Discord Admin",
        groups=["Admin"],
        is_admin=True,
        id_token="id-token-1",
        expires_at=4_102_444_800,
        actor_provider=api.ActorProvider.DISCORD.value,
        crm_contact_id="contact-123",
    )
    app = api.create_app(run_lifespan=False)
    app.state.queue = Mock()
    app.state.auth_store = _BrowserAuthStore(session)

    port = _free_port()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        lifespan="off",
        log_level="warning",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_for_port(port)

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _jobs_payload() -> list[dict[str, object]]:
    return [
        {
            "job_id": "job-queued",
            "type": "sync_people_from_crm_job",
            "status": "queued",
            "attempts": 0,
            "max_attempts": 8,
            "last_error": None,
            "created_at": "2026-05-08T12:00:00+00:00",
            "updated_at": "2026-05-08T12:00:00+00:00",
        },
        {
            "job_id": "job-failed",
            "type": "process_docuseal_agreement_job",
            "status": "failed",
            "attempts": 2,
            "max_attempts": 8,
            "last_error": "timeout",
            "created_at": "2026-05-08T11:55:00+00:00",
            "updated_at": "2026-05-08T11:58:00+00:00",
        },
    ]


def _job_detail_payload() -> dict[str, object]:
    return {
        "job_id": "job-failed",
        "type": "process_docuseal_agreement_job",
        "status": "failed",
        "attempts": 2,
        "max_attempts": 8,
        "run_after": None,
        "locked_at": None,
        "locked_by": None,
        "last_error": "timeout",
        "idempotency_key": "webhook:docuseal:1",
        "created_at": "2026-05-08T11:55:00+00:00",
        "updated_at": "2026-05-08T11:58:00+00:00",
        "payload": {
            "args": [],
            "kwargs": {"submission_id": "sub-123", "api_key": "[redacted]"},
        },
        "result": None,
    }


def _people_payload() -> list[dict[str, object]]:
    return [
        {
            "crm_contact_id": "contact-123",
            "name": "Alice Prospect",
            "email": "alice@example.com",
            "email_508": "alice@508.dev",
            "discord_user_id": "123456789",
            "discord_username": "alice",
            "latest_resume_id": "resume-file-123",
            "latest_resume_name": "alice-resume.pdf",
            "profile_status": {
                "crm_active": True,
                "is_member": True,
                "discord_linked": True,
                "email_508": True,
                "latest_resume": True,
                "skills_count": 4,
            },
        }
    ]


def _candidate_search_payload() -> list[dict[str, object]]:
    return [
        {
            "crm_contact_id": "contact-candidate-2",
            "name": "Devon Candidate",
            "email": "devon@example.com",
            "contact_type": "Prospect",
        }
    ]


def _onboarding_payload() -> list[dict[str, object]]:
    return [
        {
            "crm_contact_id": "contact-prospect-1",
            "name": "Bea Prospect",
            "email": "bea@example.com",
            "email_508": "",
            "discord_user_id": "",
            "discord_username": "",
            "contact_type": "Prospect",
            "latest_resume_id": "resume-file-456",
            "latest_resume_name": "bea-resume.pdf",
            "linkedin": "linkedin.com/in/bea-prospect",
            "github_username": "beaprospect",
            "onboarding_state": "Reachingout",
            "onboarder": "michael",
            "onboarding_updated_at": "2026-05-08T10:03:00+00:00",
            "profile_status": {
                "crm_active": True,
                "is_member": False,
                "discord_linked": False,
                "email_508": False,
                "latest_resume": True,
                "skills_count": 0,
            },
        }
    ]


def _audit_payload() -> list[dict[str, object]]:
    return [
        {
            "id": "event-1",
            "occurred_at": "2026-05-08T12:03:00+00:00",
            "source": "admin_dashboard",
            "action": "worker.job_rerun",
            "resource_type": "worker_job",
            "resource_id": "job-rerun",
            "result": "success",
            "actor_provider": "discord",
            "actor_subject": "123456789",
            "actor_display_name": "Discord Admin",
            "metadata": {"job_type": "process_docuseal_agreement_job"},
        }
    ]


def _configuration_payload() -> dict[str, object]:
    return {
        "items": [
            {
                "key": "DOCUSEAL_BASE_URL",
                "label": "DocuSeal base URL",
                "category": "Onboarding",
                "description": "DocuSeal API endpoint used for agreement workflows.",
                "value_type": "url",
                "is_secret": False,
                "env_locked": False,
                "source": "database",
                "configured": True,
                "restart_required": False,
                "secret_encryption_configured": None,
                "value": "https://docuseal.example.com",
            },
            {
                "key": "DOCUSEAL_API_KEY",
                "label": "DocuSeal API key",
                "category": "Onboarding",
                "description": "DocuSeal API key for agreement workflows.",
                "value_type": "string",
                "is_secret": True,
                "env_locked": False,
                "source": "database",
                "configured": True,
                "restart_required": False,
                "secret_encryption_configured": True,
                "masked_value": "doc...key",
            },
            {
                "key": "DOCUSEAL_MEMBER_AGREEMENT_TEMPLATE_ID",
                "label": "DocuSeal member agreement template",
                "category": "Onboarding",
                "description": "Template ID used to filter/sign member agreements.",
                "value_type": "int",
                "is_secret": False,
                "env_locked": False,
                "source": "default",
                "configured": True,
                "restart_required": False,
                "secret_encryption_configured": None,
                "value": 123,
            },
            {
                "key": "OPENAI_API_KEY",
                "label": "OpenAI API key",
                "category": "AI",
                "description": "Primary OpenAI-compatible API key.",
                "value_type": "string",
                "is_secret": True,
                "env_locked": False,
                "source": "database",
                "configured": True,
                "restart_required": False,
                "secret_encryption_configured": True,
                "masked_value": "sec...lue",
            },
        ]
    }


def _gigs_payload() -> list[dict[str, object]]:
    return [
        {
            "id": "11111111-1111-4111-8111-111111111111",
            "status": "recruiting",
            "status_label": "Recruiting",
            "title": "Webflow build",
            "required_skills": ["Webflow", "Design"],
            "preferred_skills": ["React"],
            "discord_guild_id": "guild-1",
            "discord_channel_id": "channel-1",
            "discord_channel_name": "gigs",
            "discord_thread_id": "thread-1",
            "posted_at": "2026-05-08T10:00:00+00:00",
            "last_activity_at": "2026-05-08T12:00:00+00:00",
            "application_count": 1,
            "interested_count": 0,
            "applications": [
                {
                    "id": "22222222-2222-4222-8222-222222222222",
                    "status": "suggested",
                    "source": "match_candidates",
                    "match_score": 41.2,
                    "crm_contact_id": "contact-candidate-1",
                    "name": "Casey Candidate",
                    "latest_resume_id": "resume-file-789",
                    "latest_resume_name": "casey-resume.pdf",
                    "evaluation": {"llm_summary": "Strong Webflow background."},
                }
            ],
        },
        {
            "id": "44444444-4444-4444-8444-444444444444",
            "status": "recruiting",
            "status_label": "Recruiting",
            "title": "React cleanup",
            "required_skills": ["React", "QA"],
            "preferred_skills": [],
            "discord_guild_id": "guild-1",
            "discord_channel_id": "channel-1",
            "discord_channel_name": "gigs",
            "discord_thread_id": "thread-2",
            "posted_at": "2026-05-09T10:00:00+00:00",
            "last_activity_at": "2026-05-09T12:00:00+00:00",
            "application_count": 0,
            "interested_count": 0,
            "applications": [],
        },
    ]


def _job_leads_payload() -> list[dict[str, object]]:
    return [
        {
            "id": "55555555-5555-4555-8555-555555555555",
            "status": "pending",
            "source_key": "hackernews_who_is_hiring",
            "source_type": "hackernews",
            "external_id": "48392586",
            "source_url": "https://news.ycombinator.com/item?id=48392586",
            "source_posted_at": "2026-07-01T16:00:00+00:00",
            "title": "Contract React Build",
            "organization": "Example Co",
            "body_normalized": (
                "Remote 1099 contractor wanted for React build.\n\n"
                "The project covers a new workflow, component library work, and a "
                "small API integration. Please include relevant recent work when you apply."
            ),
            "posting_type": "part_time_or_full_time",
            "location": "Remote",
            "remote": True,
            "apply_url": "https://example.com/jobs/react",
            "tags": ["1099", "contract"],
            "confidence": 0.8,
            "contractor_classification": {
                "is_contractor_friendly": True,
                "posting_type": "part_time_or_full_time",
                "tags": ["1099", "contract"],
                "confidence": 0.8,
                "confidence_label": "high",
                "rationale": "Explicitly asks for a remote 1099 contractor.",
                "method": "llm",
                "contact_email": "hiring@example.com",
            },
            "review_summary": (
                "LLM: Full-time or part-time / contract; evidence: 1099, contract - "
                "Explicitly asks for a remote 1099 contractor."
            ),
            "reviewed_by_discord_user_id": None,
            "reviewed_at": None,
            "discord_guild_id": None,
            "discord_channel_id": None,
            "discord_thread_id": None,
            "posted_at": None,
            "created_at": "2026-07-01T16:00:00+00:00",
            "updated_at": "2026-07-01T16:00:00+00:00",
        }
    ]


def _job_lead_scrape_status_payload() -> dict[str, object]:
    return {
        "status": "succeeded",
        "job_id": "job-leads-1",
        "source": "hackernews_who_is_hiring",
        "story_id": None,
        "created_at": "2026-08-04T07:00:00+00:00",
        "updated_at": "2026-08-04T07:05:00+00:00",
        "last_error": None,
        "result": {
            "source": "hackernews_who_is_hiring",
            "thread_found": True,
            "threads": [
                {
                    "story_id": 49156683,
                    "title": "Ask HN: Who is hiring? (August 2026)",
                    "url": "https://news.ycombinator.com/item?id=49156683",
                    "created_at": "2026-08-03T15:00:54+00:00",
                    "comments_reported": 140,
                }
            ],
            "potential_gigs_scraped": 108,
            "included": 3,
            "filtered_out": 105,
            "filter_reasons": {
                "empty": 0,
                "seeking_work": 2,
                "not_contractor_friendly": 103,
            },
            "created": 2,
            "updated": 1,
            "persisted": 3,
        },
    }


def _job_channels_payload() -> dict[str, object]:
    return {
        "channels": [
            {
                "channel_id": "channel-1",
                "channel_name": "gigs",
                "posting_type": "part_time",
                "requires_tag": True,
                "available_tags": [
                    {"id": "tag-contract", "name": "Contract", "moderated": False},
                    {"id": "tag-remote", "name": "Remote", "moderated": False},
                ],
            }
        ]
    }


@pytest.mark.playwright
def test_dashboard_interactivity_with_playwright(dashboard_server: str) -> None:
    playwright_api = pytest.importorskip("playwright.sync_api")
    sync_playwright = playwright_api.sync_playwright
    playwright_error = playwright_api.Error
    expect = playwright_api.expect

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except playwright_error as exc:
            if os.environ.get("CI"):
                raise
            pytest.skip(f"Playwright chromium is not installed: {exc}")

        context = browser.new_context(base_url=dashboard_server)
        context.add_cookies(
            [
                {
                    "name": api.settings.auth_session_cookie_name,
                    "value": "session-1",
                    "url": dashboard_server,
                }
            ]
        )
        page = context.new_page()
        crm_base_url = api._crm_base_url()

        job_requests: list[str] = []
        people_requests: list[str] = []
        onboarding_requests: list[str] = []
        rerun_requested = threading.Event()
        sync_requested = threading.Event()
        assign_onboarder_requested = threading.Event()
        update_onboarding_status_requested = threading.Event()
        detail_requested = threading.Event()
        gig_application_add_requested = threading.Event()
        gig_lead_post_requested = threading.Event()
        gig_lead_review_requested = threading.Event()
        gig_lead_stage_requested = threading.Event()
        gig_lead_recovery_clear_requested = threading.Event()
        gig_lead_post_body: dict[str, object] = {}
        gig_lead_review_body: dict[str, object] = {}
        gig_lead_recovery_clear_body: dict[str, object] = {}
        gig_list_requests: list[str] = []
        gig_detail_requests: list[str] = []
        gigs_list_payload = _gigs_payload()
        job_leads_payload = _job_leads_payload()

        def jobs_route(route: Any) -> None:
            request = route.request
            job_requests.append(request.url)
            query = parse_qs(urlparse(request.url).query)
            requested_status = query.get("status", [""])[0]
            jobs = _jobs_payload()
            if requested_status:
                jobs = [job for job in jobs if job["status"] == requested_status]
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(jobs),
            )

        def rerun_route(route: Any) -> None:
            rerun_requested.set()
            route.fulfill(
                status=202,
                content_type="application/json",
                body=json.dumps(
                    {
                        "status": "queued",
                        "source_job_id": "job-failed",
                        "job_id": "job-rerun",
                        "type": "process_docuseal_agreement_job",
                        "created": True,
                    }
                ),
            )

        def job_detail_route(route: Any) -> None:
            detail_requested.set()
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(_job_detail_payload()),
            )

        def people_route(route: Any) -> None:
            people_requests.append(route.request.url)
            query = parse_qs(urlparse(route.request.url).query).get("query", [""])[0]
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    _candidate_search_payload()
                    if query == "Devon"
                    else _people_payload()
                ),
            )

        def onboarding_route(route: Any) -> None:
            onboarding_requests.append(route.request.url)
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(_onboarding_payload()),
            )

        def assign_onboarder_route(route: Any) -> None:
            assign_onboarder_requested.set()
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "status": "updated",
                        "contact_id": "contact-prospect-1",
                        "contact_name": "Bea Prospect",
                        "onboarder": "jane",
                        "previous_state": "reachingout",
                        "onboarding_state": "reachingout",
                        "sync_job_id": "sync-job-person",
                    }
                ),
            )

        def update_onboarding_status_route(route: Any) -> None:
            update_onboarding_status_requested.set()
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "status": "updated",
                        "contact_id": "contact-prospect-1",
                        "contact_name": "Bea Prospect",
                        "previous_state": "reachingout",
                        "onboarding_state": "awaitingcontribution",
                        "onboarding_status_label": "Awaiting contribution",
                        "sync_job_id": "sync-job-person",
                    }
                ),
            )

        def audit_route(route: Any) -> None:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(_audit_payload()),
            )

        def configuration_route(route: Any) -> None:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(_configuration_payload()),
            )

        def gigs_route(route: Any) -> None:
            gig_list_requests.append(route.request.url)
            query = parse_qs(urlparse(route.request.url).query)
            search_query = query.get("query", [""])[0].casefold()
            requested_status = query.get("status", [""])[0]
            gigs = gigs_list_payload
            if requested_status:
                gigs = [gig for gig in gigs if gig.get("status") == requested_status]
            if search_query:
                filtered_gigs = []
                for gig in gigs:
                    required_skills = gig.get("required_skills", [])
                    preferred_skills = gig.get("preferred_skills", [])
                    haystack = " ".join(
                        [
                            str(gig.get("title") or ""),
                            " ".join(
                                str(skill)
                                for skill in required_skills
                                if isinstance(skill, str)
                            ),
                            " ".join(
                                str(skill)
                                for skill in preferred_skills
                                if isinstance(skill, str)
                            ),
                        ]
                    ).casefold()
                    if search_query in haystack:
                        filtered_gigs.append(gig)
                gigs = filtered_gigs
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(gigs),
            )

        def gig_leads_route(route: Any) -> None:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(job_leads_payload),
            )

        def gig_lead_scrape_status_route(route: Any) -> None:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(_job_lead_scrape_status_payload()),
            )

        def gig_lead_review_route(route: Any) -> None:
            gig_lead_review_requested.set()
            body = route.request.post_data_json
            gig_lead_review_body.update(body)
            job_leads_payload[0]["status"] = body["status"]
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(job_leads_payload[0]),
            )

        def gig_lead_stage_route(route: Any) -> None:
            gig_lead_stage_requested.set()
            job_leads_payload[0].update(
                {
                    "staged_discord_guild_id": "guild-1",
                    "staged_discord_channel_id": "holding-channel-1",
                    "staged_discord_thread_id": "holding-thread-1",
                    "staged_at": "2026-08-20T08:00:00+00:00",
                }
            )
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "status": "staged",
                        "lead_id": "55555555-5555-4555-8555-555555555555",
                        "guild_id": "guild-1",
                        "channel_id": "holding-channel-1",
                        "channel_name": "lead-review-queue",
                        "thread_id": "holding-thread-1",
                        "staged_at": "2026-08-20T08:00:00+00:00",
                    }
                ),
            )

        def gig_lead_recovery_clear_route(route: Any) -> None:
            gig_lead_recovery_clear_requested.set()
            gig_lead_recovery_clear_body.update(route.request.post_data_json)
            job_leads_payload[0].pop("staging_recovery", None)
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(job_leads_payload[0]),
            )

        def job_channels_route(route: Any) -> None:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(_job_channels_payload()),
            )

        def gig_lead_post_route(route: Any) -> None:
            nonlocal gigs_list_payload
            gig_lead_post_requested.set()
            gig_lead_post_body.update(route.request.post_data_json)
            job_leads_payload[0]["status"] = "posted"
            job_leads_payload[0]["discord_guild_id"] = "guild-1"
            job_leads_payload[0]["discord_channel_id"] = "channel-1"
            job_leads_payload[0]["discord_thread_id"] = "thread-lead-1"
            job_leads_payload[0]["engagement_id"] = (
                "66666666-6666-4666-8666-666666666666"
            )
            gigs_list_payload = [
                *gigs_list_payload,
                {
                    "id": "66666666-6666-4666-8666-666666666666",
                    "status": "recruiting",
                    "status_label": "Recruiting",
                    "title": "Contract React Build",
                    "required_skills": [],
                    "preferred_skills": [],
                    "discord_guild_id": "guild-1",
                    "discord_channel_id": "channel-1",
                    "discord_channel_name": "gigs",
                    "discord_thread_id": "thread-lead-1",
                    "posted_at": "2026-07-01T16:00:00+00:00",
                    "last_activity_at": "2026-07-01T16:00:00+00:00",
                    "application_count": 0,
                    "interested_count": 0,
                    "applications": [],
                },
            ]
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "status": "posted",
                        "lead_id": "55555555-5555-4555-8555-555555555555",
                        "guild_id": "guild-1",
                        "channel_id": "channel-1",
                        "thread_id": "thread-lead-1",
                        "engagement_id": "66666666-6666-4666-8666-666666666666",
                        "engagement_status": "recruiting",
                    }
                ),
            )

        def gig_detail_route(route: Any) -> None:
            gig_detail_requests.append(route.request.url)
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    gigs_list_payload[0] if gigs_list_payload else _gigs_payload()[0]
                ),
            )

        def notifications_route(route: Any) -> None:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "stale_days": 7,
                        "contacted_reminder_days": 5,
                        "notifications": [
                            {
                                "id": "contacted-gig:11111111-1111-4111-8111-111111111111",
                                "type": "contacted_gig",
                                "severity": "warning",
                                "title": "Contacted gig needs an update",
                                "message": "Webflow build has been CONTACTED for 5 day(s).",
                                "engagement_id": "11111111-1111-4111-8111-111111111111",
                                "gig_title": "Webflow build",
                                "age_days": 5,
                            }
                        ],
                    }
                ),
            )

        def gig_application_status_route(route: Any) -> None:
            body = route.request.post_data_json
            assert body["status"] == "unavailable"
            casey_application_id = "22222222-2222-4222-8222-222222222222"
            for application in gigs_list_payload[0]["applications"]:
                if application["id"] == casey_application_id:
                    application["status"] = "unavailable"
                    break
            else:
                raise AssertionError(
                    f"Expected Casey Candidate application fixture {casey_application_id}"
                )
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "id": casey_application_id,
                        "status": "unavailable",
                    }
                ),
            )

        def gig_application_add_route(route: Any) -> None:
            gig_application_add_requested.set()
            body = route.request.post_data_json
            assert body["crm_profile"].endswith("/#Contact/view/contact-candidate-2")
            new_application = {
                "id": "33333333-3333-4333-8333-333333333333",
                "status": "suggested",
                "source": "crm",
                "crm_contact_id": "contact-candidate-2",
                "name": "Devon Candidate",
                "evaluation": {"crm_name": "Devon Candidate"},
            }
            gigs_list_payload[0]["applications"].append(new_application)
            gigs_list_payload[0]["application_count"] = 2
            route.fulfill(
                status=201,
                content_type="application/json",
                body=json.dumps(new_application),
            )

        def sync_route(route: Any) -> None:
            sync_requested.set()
            route.fulfill(
                status=202,
                content_type="application/json",
                body=json.dumps(
                    {
                        "status": "queued",
                        "source": "dashboard",
                        "job_id": "job-sync",
                        "created": True,
                    }
                ),
            )

        page.route("**/dashboard/api/jobs/*/rerun", rerun_route)
        page.route("**/dashboard/api/jobs/job-failed", job_detail_route)
        page.route("**/dashboard/api/jobs?*", jobs_route)
        page.route(
            "**/dashboard/api/onboarding/contact-prospect-1/onboarder",
            assign_onboarder_route,
        )
        page.route(
            "**/dashboard/api/onboarding/contact-prospect-1/status",
            update_onboarding_status_route,
        )
        page.route("**/dashboard/api/onboarding?*", onboarding_route)
        page.route("**/dashboard/api/people?*", people_route)
        page.route("**/dashboard/api/audit-events?*", audit_route)
        page.route("**/dashboard/api/configuration", configuration_route)
        page.route("**/dashboard/api/notifications?*", notifications_route)
        page.route("**/dashboard/api/job-channels", job_channels_route)
        page.route(
            "**/dashboard/api/gig-leads/scrape-status*", gig_lead_scrape_status_route
        )
        page.route(
            re.compile(
                ".*/dashboard/api/gig-leads/55555555-5555-4555-8555-555555555555/post$"
            ),
            gig_lead_post_route,
        )
        page.route(
            re.compile(
                ".*/dashboard/api/gig-leads/55555555-5555-4555-8555-555555555555/stage$"
            ),
            gig_lead_stage_route,
        )
        page.route(
            re.compile(
                ".*/dashboard/api/gig-leads/55555555-5555-4555-8555-555555555555/"
                "staging-recovery/clear$"
            ),
            gig_lead_recovery_clear_route,
        )
        page.route(
            re.compile(
                ".*/dashboard/api/gig-leads/55555555-5555-4555-8555-555555555555/review$"
            ),
            gig_lead_review_route,
        )
        page.route("**/dashboard/api/gig-leads?*", gig_leads_route)
        page.route(
            re.compile(".*/dashboard/api/gigs/11111111-1111-4111-8111-111111111111$"),
            gig_detail_route,
        )
        page.route(
            re.compile(
                ".*/dashboard/api/gigs/11111111-1111-4111-8111-111111111111"
                "/applications/22222222-2222-4222-8222-222222222222/status$"
            ),
            gig_application_status_route,
        )
        page.route(
            re.compile(
                ".*/dashboard/api/gigs/11111111-1111-4111-8111-111111111111"
                "/applications$"
            ),
            gig_application_add_route,
        )
        page.route(re.compile(".*/dashboard/api/gigs(?:\\?.*)?$"), gigs_route)
        page.route("**/dashboard/api/sync/people", sync_route)

        try:
            page.goto("/dashboard")
            page.get_by_role("heading", name="508 Operations Dashboard").wait_for()
            page.locator("#notifications").click()
            page.get_by_text("Contacted gig needs an update").click()
            expect(page).to_have_url(
                f"{dashboard_server}/dashboard/gigs/11111111-1111-4111-8111-111111111111"
            )
            page.get_by_role("heading", name="People").wait_for()
            page.goto("/dashboard")
            page.get_by_role("heading", name="508 Operations Dashboard").wait_for()
            expect(page.locator("#userName")).to_have_text("Discord Admin")
            expect(page.locator("#userMeta")).to_contain_text("CRM contact-123")
            expect(page).to_have_url(f"{dashboard_server}/dashboard/people")
            expect(page.get_by_role("link", name="People")).to_have_attribute(
                "aria-current", "page"
            )
            page.get_by_text("Alice Prospect").wait_for()
            expect(
                page.get_by_role("link", name="Open Alice Prospect in CRM")
            ).to_have_attribute(
                "href",
                f"{crm_base_url}/#Contact/view/contact-123",
            )
            expect(
                page.get_by_role("link", name="Open Alice Prospect resume")
            ).to_have_attribute(
                "href",
                f"{crm_base_url}/api/v1/Attachment/file/resume-file-123",
            )
            page.get_by_text("Skills parsed").wait_for()
            page.locator("#peopleFilterKind").select_option("skills")
            page.locator("#peopleFilterValue").select_option("present")
            page.get_by_role("button", name="Add filter").click()
            page.get_by_role("button", name="Remove Skills: Parsed filter").wait_for()
            assert any("skills=present" in url for url in people_requests)
            expect(page.get_by_role("button", name="Name ↑")).to_be_visible()

            page.get_by_role("button", name="Sync people").click()
            assert sync_requested.wait(timeout=5)
            page.get_by_text("Queued people sync job-sync").wait_for()

            page.get_by_role("link", name="Onboarding").click()
            expect(page).to_have_url(f"{dashboard_server}/dashboard/onboarding")
            page.get_by_text("Bea Prospect").wait_for()
            page.locator("#onboardingBody [data-slot='badge']").filter(
                has_text="Reaching out"
            ).wait_for()
            expect(
                page.get_by_role("link", name="Open Bea Prospect resume")
            ).to_have_attribute(
                "href",
                f"{crm_base_url}/api/v1/Attachment/file/resume-file-456",
            )
            expect(
                page.get_by_role("link", name="Open Bea Prospect LinkedIn")
            ).to_have_attribute(
                "href",
                "https://linkedin.com/in/bea-prospect",
            )
            expect(
                page.get_by_role("link", name="Open Bea Prospect GitHub")
            ).to_have_attribute(
                "href",
                "https://github.com/beaprospect",
            )
            page.get_by_role("textbox", name="Onboarder for Bea Prospect").fill("jane")
            page.get_by_role("button", name="Save onboarder for Bea Prospect").click()
            assert assign_onboarder_requested.wait(timeout=5)
            page.get_by_text("Assigned jane").wait_for()
            expect(
                page.get_by_role("textbox", name="Onboarder for Bea Prospect")
            ).to_have_value("jane")
            page.get_by_label("Onboarding status for Bea Prospect").select_option(
                "awaitingcontribution"
            )
            assert update_onboarding_status_requested.wait(timeout=5)
            page.get_by_text("Status set to Awaiting contribution").wait_for()
            page.locator("#onboardingBody [data-slot='badge']").filter(
                has_text="Awaiting contribution"
            ).wait_for()
            expect(page.get_by_role("button", name="Status ↑")).to_be_visible()
            page.locator("#onboardingFilterKind").select_option("skills")
            page.locator("#onboardingFilterValue").select_option("missing")
            page.get_by_role("button", name="Add filter").click()
            page.get_by_role(
                "button", name="Remove Skills: Not parsed onboarding filter"
            ).wait_for()
            assert any("skills=missing" in url for url in onboarding_requests)

            page.get_by_role("link", name="Gigs").click()
            expect(page).to_have_url(f"{dashboard_server}/dashboard/gigs")
            page.get_by_text("Webflow build").wait_for()
            page.get_by_text("React cleanup").wait_for()
            expect(page.locator("#gigStatus")).to_have_value("")
            assert any(
                "status=" not in urlparse(url).query for url in gig_list_requests
            )
            page.locator("#gigLeadsTab").click()
            expect(page).to_have_url(f"{dashboard_server}/dashboard/gigs#leads")
            page.reload()
            page.get_by_text("Contract React Build").wait_for()
            page.get_by_role("link", name="People").click()
            expect(page).to_have_url(f"{dashboard_server}/dashboard/people")
            page.get_by_role("link", name="Gigs").click()
            expect(page).to_have_url(f"{dashboard_server}/dashboard/gigs")
            expect(page.locator("#gigsTab")).to_have_attribute("aria-pressed", "true")
            page.locator("#gigLeadsTab").click()
            expect(
                page.get_by_text("Full-time or part-time / contract · LLM")
            ).to_be_visible()
            expect(page.get_by_role("link", name="Apply website")).to_have_attribute(
                "href", "https://example.com/jobs/react"
            )
            expect(
                page.get_by_role("link", name="Email hiring@example.com")
            ).to_have_attribute("href", "mailto:hiring@example.com")
            expect(page.locator("#gigLeadScrapeComments")).to_have_text("140")
            expect(page.locator("#gigLeadScrapeScanned")).to_have_text("108")
            expect(page.locator("#gigLeadScrapeIncluded")).to_have_text("3")
            expect(page.locator("#gigLeadScrapeFiltered")).to_have_text("105")
            lead_comment = page.locator(
                "#gigLeadComment-55555555-5555-4555-8555-555555555555"
            )
            expect(lead_comment).to_have_class(re.compile("max-h-20"))
            page.get_by_role("button", name="Show full comment").click()
            expect(
                page.get_by_role("button", name="Collapse comment")
            ).to_have_attribute("aria-expanded", "true")
            expect(lead_comment).not_to_have_class(re.compile("max-h-20"))
            expect(lead_comment).to_contain_text("small API integration")
            expect(
                page.get_by_role("button", name="Post to holding forum")
            ).to_be_visible()
            page.get_by_role("button", name="Post to holding forum").click()
            assert gig_lead_stage_requested.wait(timeout=5)
            page.get_by_text(
                "Posted lead to #lead-review-queue for qualification"
            ).wait_for()
            expect(page.get_by_role("link", name="Holding thread")).to_have_attribute(
                "href", "https://discord.com/channels/guild-1/holding-thread-1"
            )
            page.get_by_role("button", name="Qualify lead").click()
            assert gig_lead_review_requested.wait(timeout=5)
            assert gig_lead_review_body["status"] == "approved"
            page.get_by_text("Qualified lead").wait_for()
            page.get_by_label("Post as").select_option("recruiting")
            expect(page.get_by_label("Post as")).to_have_value("recruiting")
            page.get_by_role("button", name="Promote to Discord").click()
            assert gig_lead_post_requested.wait(timeout=5)
            assert gig_lead_post_body["engagement_status"] == "recruiting"
            assert gig_lead_post_body["tags"] == "Contract,Remote"
            page.get_by_text(
                "Promoted qualified lead to Discord; staying on Leads"
            ).wait_for()
            expect(page).to_have_url(f"{dashboard_server}/dashboard/gigs#leads")
            expect(page.locator("#gigLeadsTab")).to_have_attribute(
                "aria-pressed", "true"
            )
            expect(
                page.locator("#gigLeadsBody").get_by_text("Posted", exact=True)
            ).to_be_visible()
            expect(page.get_by_role("link", name="View posted gig")).to_have_attribute(
                "href", "/dashboard/gigs/66666666-6666-4666-8666-666666666666"
            )
            expect(
                page.get_by_role("link", name="Discord", exact=True)
            ).to_have_attribute(
                "href", "https://discord.com/channels/guild-1/thread-lead-1"
            )

            page.reload()
            page.get_by_text("Contract React Build").wait_for()
            expect(page.get_by_role("link", name="View posted gig")).to_have_attribute(
                "href", "/dashboard/gigs/66666666-6666-4666-8666-666666666666"
            )

            job_leads_payload[0]["status"] = "rejected"
            page.locator("#refreshGigLeads").click()
            gig_lead_review_requested.clear()
            gig_lead_review_body.clear()
            page.get_by_role("button", name="Restore to pending").click()
            assert gig_lead_review_requested.wait(timeout=5)
            assert gig_lead_review_body["status"] == "pending"
            page.get_by_text("Restored lead to pending").wait_for()

            job_leads_payload[0].update(
                {
                    "staged_discord_guild_id": None,
                    "staged_discord_channel_id": None,
                    "staged_discord_thread_id": None,
                    "staged_at": None,
                    "staging_recovery": {
                        "guild_id": "guild-1",
                        "channel_id": "holding-channel-1",
                        "thread_id": "orphaned-thread-1",
                    },
                }
            )
            page.locator("#refreshGigLeads").click()
            recovery_warning = page.get_by_text(
                re.compile(
                    "Staging blocked until the orphaned holding thread orphaned-thread-1"
                )
            )
            recovery_warning.wait_for()
            expect(
                page.get_by_role("button", name="Post to holding forum")
            ).not_to_be_visible()
            page.once("dialog", lambda dialog: dialog.accept())
            page.get_by_role("button", name="Clear holding recovery block").click()
            assert gig_lead_recovery_clear_requested.wait(timeout=5)
            assert gig_lead_recovery_clear_body == {"orphan_deleted": True}
            page.get_by_text("Cleared the holding-thread recovery block").wait_for()
            expect(recovery_warning).not_to_be_visible()
            expect(
                page.get_by_role("button", name="Post to holding forum")
            ).to_be_visible()

            page.locator("#gigsTab").click()
            expect(page.locator("#gigsTab")).to_have_attribute("aria-pressed", "true")
            expect(page.locator("#gigStatus")).to_have_value("")
            expect(page.get_by_label("Include historical")).not_to_be_checked()
            page.get_by_text("Contract React Build").wait_for()
            page.locator("#gigQuery").fill("webflow")
            page.get_by_role("button", name="Search").click()
            assert any("query=webflow" in url for url in gig_list_requests)
            expect(page.get_by_text("React cleanup")).not_to_be_visible()
            page.get_by_role("button", name="Manage people").click()
            expect(page).to_have_url(
                f"{dashboard_server}/dashboard/gigs/11111111-1111-4111-8111-111111111111"
            )
            page.get_by_role("heading", name="People").wait_for()
            page.get_by_text("Casey Candidate").wait_for()
            expect(
                page.get_by_role("link", name="Casey Candidate", exact=True)
            ).to_have_attribute(
                "href", f"{crm_base_url}/#Contact/view/contact-candidate-1"
            )
            expect(
                page.get_by_role("link", name="Open Casey Candidate CRM profile")
            ).to_have_attribute(
                "href",
                f"{crm_base_url}/#Contact/view/contact-candidate-1",
            )
            page.get_by_label("Search candidates to add").fill("Devon")
            page.get_by_text("Devon Candidate", exact=True).click()
            assert any("query=Devon" in url for url in people_requests)
            page.get_by_role("button", name="Add candidate").click()
            assert gig_application_add_requested.wait(timeout=5)
            page.get_by_text("Devon Candidate").wait_for()
            casey_status = page.get_by_label("Candidate status for Casey Candidate")
            expect(casey_status).to_be_enabled()
            expect(casey_status).to_have_value("suggested")
            with page.expect_response(
                lambda response: (
                    response.request.method == "POST"
                    and response.url.endswith(
                        "/dashboard/api/gigs/"
                        "11111111-1111-4111-8111-111111111111"
                        "/applications/"
                        "22222222-2222-4222-8222-222222222222/status"
                    )
                    and response.status == 200
                )
            ):
                casey_status.select_option("unavailable")
            expect(casey_status).to_have_value("unavailable")
            assert gig_detail_requests

            gigs_list_payload = []
            page.goto("/dashboard/gigs/11111111-1111-4111-8111-111111111111")
            page.get_by_role("heading", name="People").wait_for()
            page.get_by_text("Casey Candidate").wait_for()

            page.get_by_role("link", name="Background tasks").click()
            expect(page).to_have_url(f"{dashboard_server}/dashboard/jobs")
            expect(page.get_by_role("link", name="Background tasks")).to_have_attribute(
                "aria-current", "page"
            )

            page.locator("#jobsBody tr").first.wait_for()
            assert page.locator("#jobsBody tr").count() == 2
            assert page.locator("#metricTotal").inner_text() == "2"
            assert page.locator("#metricQueued").inner_text() == "1"
            assert page.locator("#metricFailed").inner_text() == "1"
            expect(page.get_by_text("People lookup")).not_to_be_visible()
            expect(page.get_by_role("button", name="Updated ↓")).to_be_visible()

            with page.expect_response(
                lambda response: (
                    "/dashboard/api/jobs?" in response.url
                    and "status=failed" in response.url
                    and response.status == 200
                )
            ):
                page.locator("#status").select_option("failed")

            assert any("status=failed" in url for url in job_requests)
            expect(page.locator("#jobsBody tr")).to_have_count(1)
            expect(page.locator("#metricTotal")).to_have_text("1")
            expect(page.locator("#metricQueued")).to_have_text("0")
            expect(page.locator("#metricFailed")).to_have_text("1")
            expect(page.get_by_text("job-queued")).not_to_be_visible()
            expect(page.get_by_text("job-failed")).to_be_visible()

            page.get_by_role(
                "button",
                name="View details for process_docuseal_agreement_job task job-failed",
            ).click()
            assert detail_requested.wait(timeout=5)
            page.get_by_role("heading", name="Task detail").wait_for()
            page.get_by_text('"submission_id": "sub-123"').wait_for()
            page.get_by_text('"api_key": "[redacted]"').wait_for()

            page.get_by_role(
                "button",
                name="Rerun process_docuseal_agreement_job task job-failed",
            ).click()
            assert rerun_requested.wait(timeout=5)

            page.get_by_role("link", name="Audit").click()
            expect(page).to_have_url(f"{dashboard_server}/dashboard/audit")
            page.get_by_text("worker.job_rerun").wait_for()

            page.get_by_role("link", name="Configuration").click()
            expect(page).to_have_url(f"{dashboard_server}/dashboard/configuration")
            page.get_by_role("heading", name="Onboarding").wait_for()
            page.get_by_role("heading", name="AI Providers").wait_for()
            onboarding_table = page.get_by_role(
                "table", name="Onboarding configuration settings"
            )
            expect(onboarding_table).to_contain_text("DocuSeal base URL")
            expect(onboarding_table).to_contain_text("DocuSeal API key")
            expect(page.get_by_text("doc...key")).to_be_visible()
            expect(
                page.get_by_text("DocuSeal member agreement template", exact=True)
            ).not_to_be_visible()
            page.get_by_text("Advanced").click()
            expect(
                page.get_by_text("DocuSeal member agreement template", exact=True)
            ).to_be_visible()
            page.get_by_role("button", name=re.compile("AI Providers")).click()
            expect(page.get_by_role("heading", name="Onboarding")).not_to_be_visible()
            page.get_by_role("heading", name="AI Providers").wait_for()
            expect(page.get_by_text("OpenAI API key")).to_be_visible()
            expect(page.get_by_text("sec...lue")).to_be_visible()

            with page.expect_response(
                lambda response: (
                    response.url == f"{dashboard_server}/auth/logout"
                    and response.status == 200
                )
            ):
                page.get_by_role("button", name="Log out").click()
            expect(page).to_have_url(f"{dashboard_server}/dashboard")
            cookies = context.cookies(dashboard_server)
            assert not any(
                cookie["name"] == api.settings.auth_session_cookie_name
                for cookie in cookies
            )
        finally:
            context.close()
            browser.close()
