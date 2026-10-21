"""Standalone mode has no user: a run skips the quota check and cache refresh."""
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi import BackgroundTasks

from studio.app.common.routers import run


@contextmanager
def _stubs():
    with patch.object(
        run, "get_current_user_storage_usage", new=AsyncMock()
    ) as usage, patch.object(
        run, "get_user_storage_usage"
    ) as storage_info, patch.object(
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
    ):
        runner_cls.create_workflow_unique_id.return_value = "uid"
        yield runner_cls, usage, storage_info


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["run", "run_id"])
async def test_a_run_without_a_user_starts_with_no_quota_lookup(endpoint):
    tasks = BackgroundTasks()
    args = {
        "workspace_id": "1",
        "runItem": MagicMock(),
        "background_tasks": tasks,
        "current_user": None,
        "remote_bucket_name": "bucket",
        "db": Mock(),
    }
    if endpoint == "run_id":
        args["uid"] = "uid"

    with _stubs() as (runner_cls, usage, storage_info):
        assert await getattr(run, endpoint)(**args) == "uid"

    assert runner_cls.call_args.args[-1] is None
    usage.assert_not_called()
    storage_info.assert_not_called()
    assert tasks.tasks == []
