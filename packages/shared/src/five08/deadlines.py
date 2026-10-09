"""Deadline-aware timeout helpers for synchronous integration calls."""

from __future__ import annotations

from time import monotonic


class DeadlineExceeded(TimeoutError):
    """Raised before an integration call when its caller-visible deadline elapsed."""


def clamp_timeout_seconds(
    timeout_seconds: float,
    *,
    deadline_monotonic: float | None = None,
) -> float:
    """Bound one integration timeout by the time remaining to an absolute deadline."""

    timeout = float(timeout_seconds)
    if timeout <= 0:
        raise ValueError("timeout_seconds must be positive")
    if deadline_monotonic is None:
        return timeout
    remaining = deadline_monotonic - monotonic()
    if remaining <= 0:
        raise DeadlineExceeded("Integration execution deadline exceeded")
    return min(timeout, remaining)
