"""Tests for BackgroundScheduler.add_job first-run scheduling.

add_job defaults next_run_time to now + a small delay so interval jobs run
shortly after startup (not now+interval), and lets callers override it.
"""

from datetime import timedelta
from unittest.mock import MagicMock

from studio.app.common.core.background.scheduler import BackgroundScheduler
from studio.app.common.core.subscription.constants import SyncStatusConstants
from studio.app.common.core.utils.datetime_utils import get_current_datetime


def _add_job_and_capture(**extra_kwargs):
    """Call add_job against a mocked scheduler; return the forwarded kwargs."""
    mock_scheduler = MagicMock()
    original = BackgroundScheduler._scheduler
    BackgroundScheduler._scheduler = mock_scheduler
    try:
        BackgroundScheduler.add_job(
            func=lambda: None,
            interval_minutes=60,
            job_id="test_job",
            **extra_kwargs,
        )
    finally:
        BackgroundScheduler._scheduler = original

    mock_scheduler.add_job.assert_called_once()
    return mock_scheduler.add_job.call_args.kwargs


def test_add_job_defaults_next_run_time_shortly_after_startup():
    """Default first run is a small, bounded delay from now, not a full interval."""
    kwargs = _add_job_and_capture()

    next_run_time = kwargs["next_run_time"]
    delay = (next_run_time - get_current_datetime()).total_seconds()
    assert 0 < delay <= SyncStatusConstants.INITIAL_RUN_DELAY_SECONDS


def test_add_job_preserves_caller_next_run_time():
    """A caller-supplied next_run_time is not overridden."""
    explicit = get_current_datetime() + timedelta(minutes=30)
    kwargs = _add_job_and_capture(next_run_time=explicit)

    assert kwargs["next_run_time"] == explicit


def test_initial_run_delay_is_bounded():
    """First run lands within ~a minute of startup, not a full interval."""
    assert 0 < SyncStatusConstants.INITIAL_RUN_DELAY_SECONDS <= 60


def test_add_job_noop_when_not_initialized():
    """add_job is a safe no-op if the scheduler was never initialized."""
    original = BackgroundScheduler._scheduler
    BackgroundScheduler._scheduler = None
    try:
        # Should not raise.
        BackgroundScheduler.add_job(func=lambda: None, interval_minutes=5, job_id="x")
    finally:
        BackgroundScheduler._scheduler = original
