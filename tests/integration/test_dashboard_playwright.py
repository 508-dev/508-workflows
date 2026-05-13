"""Playwright integration tests for the admin dashboard UI."""

from __future__ import annotations

import json
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
        groups=["discord_admin"],
        is_admin=True,
        id_token="",
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
        crm_base_url = api.settings.espo_base_url.rstrip("/")

        job_requests: list[str] = []
        people_requests: list[str] = []
        onboarding_requests: list[str] = []
        rerun_requested = threading.Event()
        sync_requested = threading.Event()
        assign_onboarder_requested = threading.Event()
        detail_requested = threading.Event()

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
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(_people_payload()),
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
                        "state_updated": False,
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
        page.route("**/dashboard/api/onboarding?*", onboarding_route)
        page.route("**/dashboard/api/people?*", people_route)
        page.route("**/dashboard/api/audit-events?*", audit_route)
        page.route("**/dashboard/api/sync/people", sync_route)

        try:
            page.goto("/dashboard")
            page.get_by_role("heading", name="508 Admin Dashboard").wait_for()
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
            page.locator("#onboardingBody").get_by_text("Reaching out").wait_for()
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
            expect(page.get_by_role("button", name="Status ↑")).to_be_visible()
            page.locator("#onboardingFilterKind").select_option("skills")
            page.locator("#onboardingFilterValue").select_option("missing")
            page.get_by_role("button", name="Add filter").click()
            page.get_by_role(
                "button", name="Remove Skills: Not parsed onboarding filter"
            ).wait_for()
            assert any("skills=missing" in url for url in onboarding_requests)

            page.get_by_role("link", name="Jobs").click()
            expect(page).to_have_url(f"{dashboard_server}/dashboard/jobs")
            expect(page.get_by_role("link", name="Jobs")).to_have_attribute(
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
                name="View details for process_docuseal_agreement_job job job-failed",
            ).click()
            assert detail_requested.wait(timeout=5)
            page.get_by_role("heading", name="Job detail").wait_for()
            page.get_by_text('"submission_id": "sub-123"').wait_for()
            page.get_by_text('"api_key": "[redacted]"').wait_for()

            page.get_by_role(
                "button",
                name="Rerun process_docuseal_agreement_job job job-failed",
            ).click()
            assert rerun_requested.wait(timeout=5)

            page.get_by_role("link", name="Audit").click()
            expect(page).to_have_url(f"{dashboard_server}/dashboard/audit")
            page.get_by_text("worker.job_rerun").wait_for()

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
