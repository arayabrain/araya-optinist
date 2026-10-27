"""Run starts at the plan's concurrent-run limit, and starts that fail after
counting.

- a refused start answers 429 with the detail the frontend reads; a refused
  re-run leaves the experiment's config alone
- a counted start that is not scheduled (download error, sync lock, client
  gone) releases its count, or the user loses a slot for good
"""

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi import BackgroundTasks, HTTPException

from studio.app.common.core.workflow.workflow_concurrency import (
    ConcurrencyLimit,
    WorkflowConcurrencyLimitError,
)
from studio.app.common.routers import run

REFUSAL = WorkflowConcurrencyLimitError(ConcurrencyLimit("Free", 1, "Premium"), 1)


@contextmanager
def _stubs(remote_storage=False):
    with patch.object(run, "_check_storage_quota", new=AsyncMock()), patch.object(
        run, "get_current_user_storage_usage", new=AsyncMock()
    ), patch.object(run, "ensure_structure_caches", new=AsyncMock()), patch.object(
        run, "validate_input_edges"
    ), patch.object(
        run, "WorkflowRunner"
    ) as runner_cls, patch.object(
        run.RemoteStorageController, "is_available", return_value=remote_storage
    ), patch.object(
        run.RemoteSyncLockFileUtil, "check_sync_lock_file"
    ), patch.object(
        run, "WorkflowResult"
    ) as result_cls:
        runner_cls.create_workflow_unique_id.return_value = "uid"
        runner_cls.return_value.ensure_input_data_local = AsyncMock()
        yield runner_cls, result_cls


def _call(endpoint):
    args = {
        "workspace_id": "1",
        "runItem": MagicMock(),
        "background_tasks": BackgroundTasks(),
        "current_user": SimpleNamespace(id=7),
        "remote_bucket_name": "bucket",
        "db": Mock(),
    }
    if endpoint == "run_id":
        args["uid"] = "uid"
    return getattr(run, endpoint)(**args)


ENDPOINTS = pytest.mark.parametrize("endpoint", ["run", "run_id"])


@pytest.mark.asyncio
@ENDPOINTS
async def test_a_start_at_the_limit_answers_429_with_the_detail(endpoint):
    with _stubs() as (runner_cls, _):
        runner_cls.side_effect = REFUSAL
        with pytest.raises(HTTPException) as raised:
            await _call(endpoint)

    assert raised.value.status_code == 429
    assert raised.value.detail == REFUSAL.to_detail()


@pytest.mark.asyncio
async def test_a_refused_rerun_leaves_the_experiment_alone():
    """The runner refuses before it resets the config; the plan gate stays
    down too, so the existing results keep being served."""
    with _stubs() as (runner_cls, result_cls):
        runner_cls.side_effect = REFUSAL
        with pytest.raises(HTTPException):
            await _call("run_id")

    result_cls.return_value.mark_plan_pending.assert_not_called()
    result_cls.return_value.observe_lock.return_value.release.assert_called_once()


@pytest.mark.asyncio
@ENDPOINTS
async def test_a_scheduled_start_keeps_its_count(endpoint):
    with _stubs() as (runner_cls, _):
        assert await _call(endpoint) == "uid"

    runner_cls.return_value.release_unscheduled.assert_not_called()


@pytest.mark.asyncio
@ENDPOINTS
async def test_a_failed_input_download_releases_the_count(endpoint):
    with _stubs(remote_storage=True) as (runner_cls, _):
        runner = runner_cls.return_value
        runner.ensure_input_data_local.side_effect = RuntimeError("S3 unreachable")
        with pytest.raises(HTTPException) as raised:
            await _call(endpoint)

    assert raised.value.status_code == 500
    runner.release_unscheduled.assert_called_once_with()
    runner.run_workflow.assert_not_called()


@pytest.mark.asyncio
@ENDPOINTS
async def test_a_held_sync_lock_releases_the_count(endpoint):
    with _stubs() as (runner_cls, _):
        runner = runner_cls.return_value
        runner.run_workflow.side_effect = run.RemoteStorageLockError("1", "uid")
        with pytest.raises(HTTPException) as raised:
            await _call(endpoint)

    assert raised.value.status_code == 423
    runner.release_unscheduled.assert_called_once_with()


@pytest.mark.asyncio
@ENDPOINTS
async def test_a_client_gone_during_the_download_releases_the_count(endpoint):
    with _stubs(remote_storage=True) as (runner_cls, _):
        runner = runner_cls.return_value
        runner.ensure_input_data_local.side_effect = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            await _call(endpoint)

    runner.release_unscheduled.assert_called_once_with()
