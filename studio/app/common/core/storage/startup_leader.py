import os
import tempfile

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.storage.atomic_claim_file import AtomicClaimFile

logger = AppLogger.get_logger()

_LEADER_FILE = os.path.join(tempfile.gettempdir(), "optinist_startup_leader.json")
_LEADER_EXPIRE_MINUTES = 15


def try_become_startup_leader() -> bool:
    """Attempt to become the startup sync leader.

    Uses AtomicClaimFile with O_CREAT | O_EXCL for race-free election.
    Only one worker out of N should perform startup sync.

    Returns True if this process is the elected leader.
    """
    acquired, existing = AtomicClaimFile.try_acquire_or_detect_stale(
        _LEADER_FILE,
        content={"role": "startup_leader"},
        expire_minutes=_LEADER_EXPIRE_MINUTES,
    )

    if acquired:
        logger.info(f"coordinator.leader_elected pid={os.getpid()}")
        return True

    if existing:
        logger.info(
            f"coordinator.leader_deferred "
            f"pid={os.getpid()} "
            f"leader_pid={existing.get('pid')}"
        )
    else:
        logger.info(f"coordinator.leader_deferred pid={os.getpid()}")

    return False


def release_startup_leader() -> None:
    """Release the startup leader file.

    Must be called in a finally block after startup sync completes (or fails).
    """
    AtomicClaimFile.release(_LEADER_FILE)
    logger.info(f"coordinator.leader_released pid={os.getpid()}")
