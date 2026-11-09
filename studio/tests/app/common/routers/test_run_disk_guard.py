"""A run is refused with 507 when the host's local disk is nearly full; the
storage quota (403) is checked first, so the disk is not even measured then."""

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi import BackgroundTasks, HTTPException

from studio.app.common.core.storage.local_disk_guard import LocalDiskFullError
from studio.app.common.routers import run

GIB = 1024**3


@contextmanager
def _stubs(quota_exceeded=False):
    with patch.object(
        run, "get_current_user_storage_usage", new=AsyncMock(return_value=10)
    ), patch.object(run, "get_user_storage_usage"), patch.object(
        run, "get_effective_quota_bytes", return_value=5 if quota_exceeded else 100
    ), patch.object(
        run, "ensure_structure_caches", new=AsyncMock()
    ), patch.object(
        run, "validate_input_edges"
    ), patch.object(
        run, "WorkflowRunner"
    ) as runner_cls, patch.object(
        run.RemoteStorageController, "is_available", return_value=False
    ), patch.object(
        run.RemoteSyncLockFileUtil, "check_sync_lock_file"
    ), patch.object(
        run, "WorkflowResult"
    ), patch.object(
        run, "ensure_local_disk_headroom"
    ) as headroom:
        runner_cls.create_workflow_unique_id.return_value = "uid"
        yield runner_cls, headroom


def _args(endpoint):
    args = {
        "workspace_id": "1",
        "runItem": MagicMock(),
        "background_tasks": BackgroundTasks(),
        "current_user": MagicMock(id=7),
        "remote_bucket_name": "bucket",
        "db": Mock(),
    }
    if endpoint == "run_id":
        args["uid"] = "uid"
    return args


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["run", "run_id"])
async def test_a_full_local_disk_is_507_with_the_guard_message(endpoint):
    with _stubs() as (runner_cls, headroom):
        headroom.side_effect = LocalDiskFullError(3 * GIB, 8 * GIB)
        with pytest.raises(HTTPException) as info:
            await getattr(run, endpoint)(**_args(endpoint))

    assert info.value.status_code == 507
    assert info.value.detail.startswith("Cannot run workflow: ")
    assert "not your storage quota" in info.value.detail
    assert "Delete records you no longer need" in info.value.detail
    runner_cls.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["run", "run_id"])
async def test_the_quota_is_refused_first_without_measuring_the_disk(endpoint):
    with _stubs(quota_exceeded=True) as (runner_cls, headroom):
        with pytest.raises(HTTPException) as info:
            await getattr(run, endpoint)(**_args(endpoint))

    assert info.value.status_code == 403
    headroom.assert_not_called()
    runner_cls.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["run", "run_id"])
async def test_enough_headroom_starts_the_run(endpoint):
    with _stubs() as (runner_cls, headroom):
        assert await getattr(run, endpoint)(**_args(endpoint)) == "uid"
    headroom.assert_called_once_with()
    runner_cls.assert_called_once()
