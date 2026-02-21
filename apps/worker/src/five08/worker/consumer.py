"""Dramatiq worker process entrypoint."""

import logging

from five08.logging import configure_logging
from five08.queue import parse_queue_names
from five08.worker.config import settings

logger = logging.getLogger(__name__)


def run() -> None:
    """Start Dramatiq worker and consume configured queues."""
    from dramatiq import Worker

    import five08.worker.actors  # noqa: F401

    configure_logging(settings.log_level)

    queue_names = parse_queue_names(settings.worker_queue_names)
    if not queue_names:
        queue_names = parse_queue_names(settings.redis_queue_name)
    queue_set = set(queue_names)

    worker = Worker(queues=queue_set)
    logger.info(
        "Starting worker name=%s queues=%s", settings.worker_name, sorted(queue_set)
    )
    if settings.worker_burst:
        logger.info("Worker burst mode enabled")
    worker.start()
    worker.join()


if __name__ == "__main__":
    run()
