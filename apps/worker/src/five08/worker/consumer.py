"""Dramatiq worker process entrypoint."""

import logging
import signal
import threading

from five08.logging import configure_logging
from five08.queue import parse_queue_names
from five08.worker.config import settings

logger = logging.getLogger(__name__)


def _safe_import_actors() -> bool:
    """Import actors while surfacing startup errors clearly."""
    try:
        import five08.worker.actors  # noqa: F401

        return True
    except Exception:
        logger.exception("Failed to import actor module during worker startup")
        return False


def run() -> None:
    """Start Dramatiq worker and consume configured queues."""
    import dramatiq
    from dramatiq import Worker

    queue_set: set[str] = set()
    worker: Worker | None = None
    stop_requested = threading.Event()

    if not _safe_import_actors():
        raise RuntimeError("Worker startup aborted due to actor import failure.")

    configure_logging(settings.log_level)

    def _handle_shutdown_signal(signal_number: int, _frame: object) -> None:
        logger.info(
            "Received shutdown signal=%s for worker=%s",
            signal_number,
            settings.worker_name,
        )
        stop_requested.set()

    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, _handle_shutdown_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, _handle_shutdown_signal)

    try:
        queue_names = parse_queue_names(settings.worker_queue_names)
        if not queue_names:
            queue_names = parse_queue_names(settings.redis_queue_name)
        queue_set = set(queue_names)
        logger.debug("Resolved queue set=%s", sorted(queue_set))
        broker = dramatiq.get_broker()
        logger.debug("Resolved dramatiq broker=%s", type(broker).__name__)

        worker = Worker(broker, queues=queue_set)
        logger.info(
            "Starting worker name=%s queues=%s", settings.worker_name, sorted(queue_set)
        )
        if settings.worker_burst:
            logger.warning(
                "WORKER_BURST is set but Dramatiq worker burst mode is unsupported"
            )
        worker.start()
        logger.info(
            "Worker started; waiting for shutdown signal for %s", settings.worker_name
        )
        stop_requested.wait()
    except Exception:
        logger.exception(
            "Worker initialization or execution failed name=%s queues=%s",
            settings.worker_name,
            sorted(queue_set),
        )
        raise
    finally:
        if worker is None:
            return
        try:
            logger.info("Stopping worker name=%s", settings.worker_name)
            worker.stop(timeout=settings.job_timeout_seconds * 1000)
        except Exception:
            logger.exception("Worker stop failed name=%s", settings.worker_name)


if __name__ == "__main__":
    run()
