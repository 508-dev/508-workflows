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

        job_requests: list[str] = []
        rerun_requested = threading.Event()
        sync_requested = threading.Event()

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
        page.route("**/dashboard/api/jobs?*", jobs_route)
        page.route("**/dashboard/api/sync/people", sync_route)

        try:
            page.goto("/dashboard")
            page.get_by_role("heading", name="508 Admin Dashboard").wait_for()
            page.get_by_text("Discord Admin", exact=True).wait_for()
            page.get_by_text("CRM contact-123").wait_for()

            page.locator("tbody tr").first.wait_for()
            assert page.locator("tbody tr").count() == 2
            assert page.locator("#metricTotal").inner_text() == "2"
            assert page.locator("#metricQueued").inner_text() == "1"
            assert page.locator("#metricFailed").inner_text() == "1"

            with page.expect_response(
                lambda response: (
                    "/dashboard/api/jobs?" in response.url
                    and "status=failed" in response.url
                    and response.status == 200
                )
            ):
                page.locator("#status").select_option("failed")

            assert any("status=failed" in url for url in job_requests)
            expect(page.locator("tbody tr")).to_have_count(1)
            expect(page.locator("#metricTotal")).to_have_text("1")
            expect(page.locator("#metricQueued")).to_have_text("0")
            expect(page.locator("#metricFailed")).to_have_text("1")
            expect(page.get_by_text("job-queued")).not_to_be_visible()
            expect(page.get_by_text("job-failed")).to_be_visible()

            page.get_by_role(
                "button",
                name="Rerun process_docuseal_agreement_job job job-failed",
            ).click()
            assert rerun_requested.wait(timeout=5)

            page.get_by_role("button", name="Sync people").click()
            assert sync_requested.wait(timeout=5)
            page.get_by_text("Queued people sync job-sync").wait_for()
        finally:
            context.close()
            browser.close()
