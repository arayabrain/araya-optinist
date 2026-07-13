"""Distributed leader election for one-shot startup sync.

Uses MySQL GET_LOCK so coordination works across ECS tasks on different
hosts. File-based locks in /tmp can't — each container's /tmp is private,
so every task would elect itself and run the sync concurrently.
"""

from contextlib import contextmanager

from sqlalchemy import text

from studio.app.common.core.logger import AppLogger
from studio.app.common.db.database import session_scope

logger = AppLogger.get_logger()

_LEADER_LOCK_NAME = "optinist_startup_sync_leader"


@contextmanager
def startup_sync_leader_lock():
    """Acquire the startup-sync leader lock, non-blocking.

    Yields True if this process won the election, False otherwise.
    The lock is held for the lifetime of the `with` block and released
    on exit; if the process crashes, MySQL releases on connection close.
    """
    with session_scope() as db:
        result = db.execute(
            text("SELECT GET_LOCK(:name, :timeout) AS acquired"),
            {"name": _LEADER_LOCK_NAME, "timeout": 0},
        )
        acquired = result.scalar() == 1
        try:
            yield acquired
        finally:
            if acquired:
                db.execute(
                    text("SELECT RELEASE_LOCK(:name)"),
                    {"name": _LEADER_LOCK_NAME},
                )
