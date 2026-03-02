import json
import os
import socket
import uuid
from datetime import datetime, timezone
from typing import Optional, Tuple

from studio.app.common.core.logger import AppLogger

logger = AppLogger.get_logger()

# Unique per-worker UUID, generated lazily on first access.
# Lazy init ensures each forked worker gets its own UUID even when
# the module is imported before fork (e.g. uvicorn --preload).
_WORKER_UUID: Optional[str] = None
_WORKER_PID: Optional[int] = None


def get_worker_uuid() -> str:
    global _WORKER_UUID, _WORKER_PID
    current_pid = os.getpid()
    if _WORKER_UUID is None or _WORKER_PID != current_pid:
        _WORKER_UUID = str(uuid.uuid4())
        _WORKER_PID = current_pid
    return _WORKER_UUID


def _compute_expiry(expire_minutes: int) -> str:
    """Compute expiry timestamp handling minute overflow correctly."""
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    expiry = now + timedelta(minutes=expire_minutes)
    return expiry.isoformat()


class AtomicClaimFile:
    """Reusable atomic file claim with stale detection.

    Used by: startup leader election, download claim sentinels.
    Pattern mirrors existing RemoteSyncLockFileUtil but uses O_CREAT|O_EXCL
    instead of plain open() for race-free creation.
    """

    @staticmethod
    def try_create(
        path: str,
        content: dict,
        expire_minutes: int = 10,
    ) -> bool:
        """Atomically create claim file. Returns True if created (we own it).

        Uses O_CREAT | O_EXCL to eliminate TOCTOU race window.
        Content is enriched with pid, worker_uuid, hostname, and timestamps.
        """
        enriched = {
            **content,
            "pid": os.getpid(),
            "worker_uuid": get_worker_uuid(),
            "hostname": socket.gethostname(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": _compute_expiry(expire_minutes),
        }

        dir_path = os.path.dirname(path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)

        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            try:
                data = json.dumps(enriched, default=str).encode()
                os.write(fd, data)
                os.fsync(fd)
            finally:
                os.close(fd)
            return True
        except FileExistsError:
            return False

    @staticmethod
    def is_held(
        path: str,
        expire_minutes: int = 10,
    ) -> Tuple[bool, Optional[dict]]:
        """Check if claim is held and fresh.

        Returns (is_held, claim_data).
        A claim is NOT held if:
        - File doesn't exist
        - File is expired (past expires_at, or older than expire_minutes)
        - Owner PID is dead
        - Hostname differs from current (previous container)
        """
        if not os.path.isfile(path):
            return False, None

        try:
            with open(path, "r") as f:
                claim_data = json.load(f)
        except (json.JSONDecodeError, OSError):
            # Corrupt or inaccessible file -- treat as not held
            return False, None

        # Check hostname (different hostname = previous container)
        claim_hostname = claim_data.get("hostname", "")
        if claim_hostname and claim_hostname != socket.gethostname():
            return False, claim_data

        # Check expiry: prefer expires_at (written at creation time) over
        # recomputing from started_at + expire_minutes parameter, so the
        # expiry used at creation time is authoritative.
        now = datetime.now(timezone.utc)
        expired = False
        expires_at_str = claim_data.get("expires_at")
        if expires_at_str:
            try:
                expires_at = datetime.fromisoformat(expires_at_str)
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if now >= expires_at:
                    expired = True
            except (ValueError, TypeError):
                pass

        # Fallback: use started_at + expire_minutes if expires_at unavailable
        if not expired and not expires_at_str:
            started_at_str = claim_data.get("started_at")
            if started_at_str:
                try:
                    started_at = datetime.fromisoformat(started_at_str)
                    if started_at.tzinfo is None:
                        started_at = started_at.replace(tzinfo=timezone.utc)
                    age_seconds = (now - started_at).total_seconds()
                    if age_seconds > expire_minutes * 60:
                        expired = True
                except (ValueError, TypeError):
                    pass

        if expired:
            return False, claim_data

        # Check PID liveness
        claim_pid = claim_data.get("pid")
        if claim_pid is not None:
            try:
                os.kill(int(claim_pid), 0)
            except (OSError, ProcessLookupError):
                # PID is dead
                return False, claim_data
            except (ValueError, TypeError):
                pass

        return True, claim_data

    @staticmethod
    def release(path: str) -> None:
        """Delete claim file if it exists."""
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning(f"Failed to release claim file {path}: {e}")

    @staticmethod
    def try_acquire_or_detect_stale(
        path: str,
        content: dict,
        expire_minutes: int = 10,
    ) -> Tuple[bool, Optional[dict]]:
        """Try to acquire a claim, cleaning up stale claims first.

        Returns (acquired, existing_claim_data).
        If the existing claim is stale, deletes it and retries once.
        """
        # First attempt
        if AtomicClaimFile.try_create(path, content, expire_minutes):
            return True, None

        # File exists -- check if stale
        is_held, claim_data = AtomicClaimFile.is_held(path, expire_minutes)
        if is_held:
            return False, claim_data

        # Stale -- clean up and retry
        AtomicClaimFile.release(path)
        if AtomicClaimFile.try_create(path, content, expire_minutes):
            return True, None

        # Another process beat us to re-creation
        _, new_claim = AtomicClaimFile.is_held(path, expire_minutes)
        return False, new_claim
