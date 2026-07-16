"""Standalone cleanup worker for free-tier instances.

Runs ``DataCleanupJob`` in its own process so that the cleanup scheduler
is decoupled from the multi-worker uvicorn (UVICORN_WORKERS=5) FastAPI
application.  This avoids the need for leader-election or distributed
locking among API workers.

Usage (from cloud-startup.sh)::

    python -m studio.cleanup_worker &

The process runs a single APScheduler ``AsyncIOScheduler`` that triggers
``DataCleanupJob.run`` every 60 minutes and shuts down gracefully on
SIGTERM / SIGINT (ECS sends SIGTERM on task stop).
"""

import asyncio
import os
import signal

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from studio.app.common.core.background.cleanup_job import DataCleanupJob
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.subscription.constants import SyncStatusConstants

logger = AppLogger.get_logger()


def main() -> None:
    instance_id = os.environ.get("INSTANCE_ID") or "local"
    logger.info(
        f"cleanup_worker starting (instance: {instance_id}, "
        f"interval: {SyncStatusConstants.CLEANUP_INTERVAL_MINUTES}min)"
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    scheduler = AsyncIOScheduler(event_loop=loop)
    scheduler.add_job(
        DataCleanupJob.run,
        trigger=IntervalTrigger(minutes=SyncStatusConstants.CLEANUP_INTERVAL_MINUTES),
        id="local_data_cleanup",
        replace_existing=True,
    )
    scheduler.start()

    # Graceful shutdown on SIGTERM / SIGINT
    def _shutdown(sig: int, _frame) -> None:  # noqa: ANN001
        logger.info(f"cleanup_worker received signal {sig}, shutting down")
        scheduler.shutdown(wait=False)
        loop.call_soon_threadsafe(loop.stop)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        loop.run_forever()
    finally:
        loop.close()
        logger.info("cleanup_worker stopped")


if __name__ == "__main__":
    main()
