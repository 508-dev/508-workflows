"""RQ worker process entrypoint."""

import logging
from typing import List

from rq import Queue, Worker

from five08.logging import configure_logging
from five08.queue import get_redis_connection
from five08.worker.config import settings

logger = logging.getLogger(__name__)


def _queue_names() -> List[str]:
    """Parse comma-separated queue names from settings."""
    names = [name.strip() for name in settings.worker_queue_names.split(",")]
    return [name for name in names if name]


def run() -> None:
    """Start RQ worker and consume configured queues."""
    configure_logging(settings.log_level)

    redis_conn = get_redis_connection(settings)
    queue_names = _queue_names()
    queues = [Queue(name, connection=redis_conn) for name in queue_names]

    worker = Worker(
        queues=queues,
        connection=redis_conn,
        name=settings.worker_name,
    )
    logger.info("Starting worker name=%s queues=%s", settings.worker_name, queue_names)
    worker.work(with_scheduler=True, burst=settings.worker_burst)


if __name__ == "__main__":
    run()
