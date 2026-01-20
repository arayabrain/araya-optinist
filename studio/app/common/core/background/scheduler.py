"""
Background job scheduler for OptiNiSt Cloud.

Uses APScheduler to run periodic background tasks:
- Sync published experiments from S3 to local storage
- Clean up data for logged-out free users

Multi-worker Safety:
When running FastAPI with multiple workers (--workers > 1), each worker
process would start its own scheduler instance, causing duplicate job
execution. This module uses a file-based lock to ensure only one worker
runs the scheduler. Other workers will skip scheduler initialization.
"""

import atexit
import os
import tempfile
from typing import Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.mode import MODE
from studio.app.common.core.storage.remote_storage_controller import RemoteStorageType

logger = AppLogger.get_logger()

# Lock file path for multi-worker coordination
_SCHEDULER_LOCK_FILE = os.path.join(tempfile.gettempdir(), "optinist_scheduler.lock")


class BackgroundScheduler:
    """
    Static class for managing background jobs scheduler.

    Multi-worker Safety:
        When running with multiple FastAPI workers, only the first worker
        to acquire the lock file will run the scheduler. Other workers
        will skip scheduler initialization to prevent duplicate jobs.
    """

    _scheduler: AsyncIOScheduler = None
    _owns_lock: bool = False

    @classmethod
    def _is_process_running(cls, pid: int) -> bool:
        """Check if a process with the given PID is still running."""
        try:
            # os.kill with signal 0 checks if process exists without killing it
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    @classmethod
    def _cleanup_stale_lock(cls) -> bool:
        """
        Check if the lock file is stale (owner process is dead) and clean it up.

        Returns:
            True if stale lock was cleaned up, False otherwise.
        """
        try:
            with open(_SCHEDULER_LOCK_FILE, "r") as f:
                content = f.read().strip()

            try:
                owner_pid = int(content)
            except ValueError:
                # Invalid PID in lock file - treat as stale
                logger.warning(
                    f"Invalid PID in lock file: {content}. Cleaning up stale lock."
                )
                os.remove(_SCHEDULER_LOCK_FILE)
                return True

            if not cls._is_process_running(owner_pid):
                logger.info(
                    f"Lock file owner PID {owner_pid} is no longer running. "
                    f"Cleaning up stale lock."
                )
                os.remove(_SCHEDULER_LOCK_FILE)
                return True

            return False

        except FileNotFoundError:
            # Lock file doesn't exist, nothing to clean
            return False
        except Exception as e:
            logger.warning(f"Error checking for stale lock: {e}")
            return False

    @classmethod
    def _acquire_scheduler_lock(cls, _retry_count: int = 0) -> bool:
        """
        Attempt to acquire the scheduler lock using a marker file.

        Uses atomic file creation (O_CREAT | O_EXCL) to ensure only one
        process can acquire the lock. The lock file contains the PID
        of the owning process for staleness detection.

        Handles stale locks: If the lock file exists but the owner process
        is no longer running (crashed, killed, etc.), the stale lock is
        automatically cleaned up and acquisition is retried (max 1 retry).

        Args:
            _retry_count: Internal counter to prevent infinite recursion.
                          Do not pass this parameter externally.

        Returns:
            True if lock acquired, False if another process owns it.
        """
        # Prevent unbounded recursion - allow at most 1 retry
        max_retries = 1
        if _retry_count > max_retries:
            logger.warning(
                f"Scheduler lock acquisition failed after {max_retries} retries"
            )
            return False

        # First, check for and clean up any stale locks
        if os.path.exists(_SCHEDULER_LOCK_FILE):
            if cls._cleanup_stale_lock():
                logger.info("Cleaned up stale lock, retrying acquisition")

        try:
            # Atomic file creation - fails if file exists
            fd = os.open(
                _SCHEDULER_LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644
            )
            # Write our PID to the lock file for staleness detection
            os.write(fd, f"{os.getpid()}\n".encode())
            os.close(fd)

            cls._owns_lock = True
            logger.info(
                f"Acquired scheduler lock (PID {os.getpid()}, "
                f"lock file: {_SCHEDULER_LOCK_FILE})"
            )

            # Register cleanup on process exit
            atexit.register(cls._release_scheduler_lock)
            return True

        except FileExistsError:
            # Another process owns the lock - verify it's still alive
            try:
                with open(_SCHEDULER_LOCK_FILE, "r") as f:
                    owner_pid_str = f.read().strip()
                try:
                    owner_pid = int(owner_pid_str)
                    if cls._is_process_running(owner_pid):
                        logger.info(
                            f"Scheduler lock owned by PID {owner_pid}, "
                            f"skipping scheduler in this worker (PID {os.getpid()})"
                        )
                    else:
                        # Owner died between our stale check and atomic create
                        # This is a race condition - retry with incremented counter
                        logger.info(
                            f"Lock owner PID {owner_pid} died during acquisition. "
                            f"Retrying (attempt {_retry_count + 1})..."
                        )
                        try:
                            os.remove(_SCHEDULER_LOCK_FILE)
                        except FileNotFoundError:
                            pass  # Another process already cleaned it up
                        return cls._acquire_scheduler_lock(_retry_count + 1)
                except ValueError:
                    logger.warning(f"Invalid PID in lock file: {owner_pid_str}")
            except Exception as read_err:
                logger.info(
                    f"Scheduler lock exists, skipping in this worker "
                    f"(PID {os.getpid()}). Read error: {read_err}"
                )
            return False

        except Exception as e:
            logger.warning(f"Error acquiring scheduler lock: {e}")
            return False

    @classmethod
    def _release_scheduler_lock(cls):
        """Release the scheduler lock file if we own it."""
        if cls._owns_lock:
            try:
                os.remove(_SCHEDULER_LOCK_FILE)
                cls._owns_lock = False
                logger.info(f"Released scheduler lock (PID {os.getpid()})")
            except Exception as e:
                logger.warning(f"Error releasing scheduler lock: {e}")

    @classmethod
    def initialize(cls):
        """
        Initialize the scheduler (call on app startup).

        Validates critical environment configuration (S3 bucket) and fails fast
        if misconfigured to prevent silent failures during job execution.

        Multi-worker Safety:
            Only the first worker to acquire the lock will initialize the
            scheduler. Other workers will skip to prevent duplicate jobs.
        """
        if cls._scheduler is not None:
            logger.warning("Scheduler already initialized")
            return

        # Skip in standalone mode
        if MODE.IS_STANDALONE:
            logger.info("Standalone mode - background scheduler disabled")
            return

        # Multi-worker safety: only one worker should run the scheduler
        if not cls._acquire_scheduler_lock():
            logger.info(
                "Scheduler initialization skipped - another worker owns the lock"
            )
            return

        # Validate S3 configuration before initializing jobs
        cls._validate_s3_configuration()

        cls._scheduler = AsyncIOScheduler()
        logger.info("Background scheduler initialized")

    @classmethod
    def _validate_s3_configuration(cls):
        """
        Validate S3 bucket configuration at startup.

        Raises error if S3 is not properly configured, preventing silent failures
        during cleanup and sync jobs that depend on S3.
        """
        remote_storage_type = RemoteStorageType.get_activated_type()
        if remote_storage_type != RemoteStorageType.S3:
            logger.info(
                f"Remote storage type is {remote_storage_type.name}, "
                "skipping S3 configuration validation."
            )
            return

        import os

        s3_bucket = os.environ.get("S3_DEFAULT_BUCKET_NAME")

        if not s3_bucket:
            error_msg = (
                "S3_DEFAULT_BUCKET_NAME environment variable not set. "
                "Background jobs (cleanup, sync) require S3 configuration. "
                "Please configure S3_DEFAULT_BUCKET_NAME or disable background jobs."
            )
            logger.error(error_msg)
            raise RuntimeError(error_msg)

        # Validate AWS credentials are available
        try:
            import boto3
            from botocore.exceptions import NoCredentialsError

            try:
                s3_client = boto3.client("s3")
                # Test access by listing buckets (lightweight operation)
                s3_client.head_bucket(Bucket=s3_bucket)
                logger.info(f"S3 configuration validated: bucket={s3_bucket}")
            except NoCredentialsError:
                error_msg = (
                    "AWS credentials not configured. Background jobs require "
                    "AWS credentials to access S3. Please configure AWS credentials."
                )
                logger.error(error_msg)
                raise RuntimeError(error_msg)
            except Exception as e:
                logger.warning(
                    f"S3 bucket validation warning: {e}. "
                    f"Bucket '{s3_bucket}' may not exist or be accessible. "
                    f"Background jobs may fail."
                )
        except ImportError:
            logger.error("boto3 not installed, cannot validate S3 configuration")
            raise RuntimeError("boto3 required for background jobs")

    @classmethod
    def start(cls):
        """Start the scheduler (call after adding all jobs)"""
        if cls._scheduler is None:
            logger.warning("Scheduler not initialized, cannot start")
            return

        if not cls._scheduler.running:
            cls._scheduler.start()
            logger.info("Background scheduler started")
        else:
            logger.warning("Scheduler already running")

    @classmethod
    def shutdown(cls):
        """Shutdown the scheduler gracefully and release the lock."""
        if cls._scheduler is None:
            logger.warning("Scheduler not initialized, nothing to shutdown")
            return

        if cls._scheduler.running:
            cls._scheduler.shutdown(wait=True)
            logger.info("Background scheduler shut down")
        else:
            logger.warning("Scheduler not running, nothing to shutdown")

        # Release the lock file
        cls._release_scheduler_lock()

    @classmethod
    def add_job(cls, func: Callable, interval_minutes: int, job_id: str, **kwargs):
        """
        Add a job to run at a fixed interval.

        NOTE: AsyncIOScheduler automatically detects if func is an async function
        and handles it correctly. No special configuration needed for async jobs.
        """
        if cls._scheduler is None:
            logger.warning("Scheduler not initialized, cannot add job")
            return

        cls._scheduler.add_job(
            func,
            trigger=IntervalTrigger(minutes=interval_minutes),
            id=job_id,
            replace_existing=True,
            **kwargs,
        )

        logger.info(
            f"Added background job: {job_id} "
            f"(runs every {interval_minutes} minutes)"
        )
