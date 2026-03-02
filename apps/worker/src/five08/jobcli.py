"""Command-line helpers for worker job inspection and reruns."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx

DEFAULT_API_URL = "http://localhost:8090"
DEFAULT_TIMEOUT_SECONDS = 10.0
API_SECRET_ENV_VAR = "API_SHARED_SECRET"


def _default_api_url() -> str:
    """Return default backend API URL, preferring explicit environment overrides."""
    return os.getenv("WORKER_API_BASE_URL", DEFAULT_API_URL)


def _default_api_secret() -> str | None:
    """Return API secret from environment when available."""
    return os.getenv(API_SECRET_ENV_VAR)


def _build_parser() -> argparse.ArgumentParser:
    """Construct top-level CLI parser for job operations."""
    parser = argparse.ArgumentParser(
        prog="jobsctl",
        description="Jobs utility for querying status and rerunning worker jobs.",
    )
    parser.add_argument(
        "--api-url",
        default=_default_api_url(),
        help=f"Backend API base URL (default: {_default_api_url()}).",
    )
    parser.add_argument(
        "--secret",
        default=_default_api_secret(),
        help=f"API secret for X-API-Secret header (default: ${API_SECRET_ENV_VAR}).",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser(
        "status",
        help="Fetch job status and result payload.",
    )
    status_parser.add_argument("job_id", help="Existing job id.")
    status_parser.set_defaults(handler=_handle_status)

    rerun_parser = subparsers.add_parser(
        "rerun",
        help="Create a duplicate rerun job from an existing job id.",
    )
    rerun_parser.add_argument("job_id", help="Existing job id to duplicate.")
    rerun_parser.set_defaults(handler=_handle_rerun)

    return parser


def _build_headers(secret: str | None) -> dict[str, str]:
    """Build headers with required API auth header."""
    if not secret:
        raise ValueError("Missing API secret (use --secret or set API_SHARED_SECRET).")

    return {"X-API-Secret": secret}


def _parse_response(response: httpx.Response) -> dict[str, Any]:
    """Parse response JSON payload or raise a clear HTTP error."""
    try:
        payload = response.json()
    except ValueError:
        raise ValueError(
            f"API response was not JSON (status={response.status_code}): {response.text}"
        )

    if response.is_success:
        return payload

    message = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(message, str) and message:
        raise ValueError(f"API error {response.status_code}: {message}")
    raise ValueError(f"API error {response.status_code}: {response.text}")


def _request_json(
    *,
    method: str,
    api_url: str,
    path: str,
    secret: str | None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a JSON request against the backend API."""
    headers = _build_headers(secret)
    url = f"{api_url.rstrip('/')}/{path.lstrip('/')}"
    if payload is not None:
        try:
            response = httpx.request(
                method=method,
                url=url,
                headers=headers,
                json=payload,
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
        except httpx.RequestError as exc:
            raise RuntimeError(f"Failed to contact API: {exc}") from exc
    else:
        try:
            response = httpx.request(
                method=method,
                url=url,
                headers=headers,
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
        except httpx.RequestError as exc:
            raise RuntimeError(f"Failed to contact API: {exc}") from exc
    return _parse_response(response)


def _handle_status(args: argparse.Namespace) -> int:
    """Handle jobsctl status <job_id>."""
    try:
        payload = _request_json(
            method="GET",
            api_url=args.api_url,
            path=f"/jobs/{args.job_id}",
            secret=args.secret,
        )
    except Exception as exc:  # broad catch keeps UX predictable
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _handle_rerun(args: argparse.Namespace) -> int:
    """Handle jobsctl rerun <job_id>."""
    try:
        payload = _request_json(
            method="POST",
            api_url=args.api_url,
            path=f"/jobs/{args.job_id}/rerun",
            secret=args.secret,
        )
    except Exception as exc:  # broad catch keeps UX predictable
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def run(argv: list[str] | None = None) -> int:
    """Run CLI and return process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(run())
