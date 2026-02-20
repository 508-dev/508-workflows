"""Shared queue helpers."""

from collections.abc import Callable
from typing import Any

from redis import Redis
from rq import Queue
from rq.job import Job

from five08.settings import SharedSettings


def get_redis_connection(settings: SharedSettings) -> Redis:
    """Create a Redis connection from shared settings."""
    return Redis.from_url(settings.redis_url)


def get_queue(settings: SharedSettings, connection: Redis | None = None) -> Queue:
    """Build a queue object for the configured queue name."""
    redis_conn = connection or get_redis_connection(settings)
    return Queue(name=settings.redis_queue_name, connection=redis_conn)


def enqueue_job(
    queue: Queue,
    fn: Callable[..., Any],
    args: tuple[Any, ...],
    settings: SharedSettings,
) -> Job:
    """Enqueue a job with shared timeout/TTL defaults."""
    return queue.enqueue(
        fn,
        *args,
        job_timeout=settings.job_timeout_seconds,
        result_ttl=settings.job_result_ttl_seconds,
    )
