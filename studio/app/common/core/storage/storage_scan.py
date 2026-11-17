"""Storage size scans (S3 listings, folder walks) for async request code."""

import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, AsyncIterator, Callable, Dict, Optional, TypeVar

import boto3
from anyio import CapacityLimiter, Lock
from anyio.lowlevel import RunVar

from studio.app.common.db.database import run_in_limited_thread

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

_T = TypeVar("_T")

# Scans running at once in a process; more wait for a slot off the event loop
MAX_CONCURRENT_SCANS = 4


@dataclass
class _KeyLock:
    lock: Lock = field(default_factory=Lock)
    # Holder and waiters; the entry is dropped when none is left
    users: int = 0


# Per event loop, as anyio's default limiter is
_scan_limiter: RunVar[CapacityLimiter] = RunVar("storage_scan_limiter")
_scan_locks: RunVar[Dict[str, _KeyLock]] = RunVar("storage_scan_locks")

_s3_client: Optional["S3Client"] = None
_s3_client_lock = threading.Lock()


def get_s3_client() -> "S3Client":
    """
    The process's S3 client for size scans, created on first use.

    - A client is thread-safe and costly to create, so every scan thread
      shares this one. Never close it.
    - Created under a lock, from its own boto3 session: creating clients from
      one session in several threads at once is not thread-safe, and other
      code calls boto3.client() on the default session.
    """
    global _s3_client
    with _s3_client_lock:
        if _s3_client is None:
            _s3_client = boto3.session.Session().client("s3")
        return _s3_client


async def run_storage_scan(func: Callable[..., _T], *args) -> _T:
    """
    Run a blocking storage scan from async request code in a worker thread.

    - S3 listings, folder walks and the writes of what they find take seconds;
      on the event loop they hold every other request of the process.
    - Its own thread limiter: scans do not take the slots of the default
      threadpool (sync routes and dependencies) or of run_db_lookup.
    """
    return await run_in_limited_thread(_scan_limiter, MAX_CONCURRENT_SCANS, func, *args)


@asynccontextmanager
async def storage_scan_lock(key: str) -> AsyncIterator[None]:
    """
    One scan-and-store per key at a time in this process.

    - Keys: "workspace:<id>" for a workspace's refresh, "user:<id>" for a
      user's total.
    - Two scans of one key that overlap can each miss what the other stores:
      a record inserted twice, or an older total stored over a newer one.
    - Other processes are not covered.
    """
    try:
        locks = _scan_locks.get()
    except LookupError:
        locks = {}
        _scan_locks.set(locks)
    entry = locks.get(key)
    if entry is None:
        entry = locks[key] = _KeyLock()
    entry.users += 1
    try:
        async with entry.lock:
            yield
    finally:
        entry.users -= 1
        if not entry.users:
            del locks[key]
