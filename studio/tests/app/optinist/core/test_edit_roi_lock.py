import shutil

import pytest

from studio.app.common.core.storage.remote_storage_controller import (
    RemoteStorageLockError,
    RemoteSyncLockFileUtil,
)
from studio.app.common.core.utils.filepath_creater import (
    create_directory,
    join_filepath,
)
from studio.app.dir_path import DIRPATH
from studio.app.optinist.core.edit_ROI import utils as edit_roi_utils
from studio.app.optinist.core.edit_ROI.utils import EditRoiUtils

workspace_id = "default"
unique_id = "edit_roi_lock"
node_id = "suite2p_roi_abcdefghij"
workflow_dir = join_filepath([DIRPATH.OUTPUT_DIR, workspace_id, unique_id])


class ExplodingExecutor:
    """Stands in for the commit worker pool and fails the way a bad commit does."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def submit(self, *args, **kwargs):
        raise RuntimeError("commit failed")


@pytest.fixture
def filepath():
    shutil.rmtree(workflow_dir, ignore_errors=True)
    create_directory(join_filepath([workflow_dir, node_id]))
    yield join_filepath([workflow_dir, node_id, "suite2p_roi.pkl"])
    shutil.rmtree(workflow_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_a_failed_commit_releases_the_lock(filepath, monkeypatch):
    monkeypatch.setattr(
        edit_roi_utils, "ProcessPoolExecutor", lambda max_workers: ExplodingExecutor()
    )

    with pytest.raises(RuntimeError):
        await EditRoiUtils.execute(filepath, "bucket")

    # Otherwise every retry answers 423 until the stale-lock timeout expires.
    assert not RemoteSyncLockFileUtil.check_sync_lock_file(workspace_id, unique_id)


@pytest.mark.asyncio
async def test_a_concurrent_commit_is_refused_and_leaves_the_lock(filepath):
    RemoteSyncLockFileUtil.create_sync_lock_file(workspace_id, unique_id)

    with pytest.raises(RemoteStorageLockError):
        await EditRoiUtils.execute(filepath, "bucket")

    # The refused request must not release the holder's lock on its way out.
    assert RemoteSyncLockFileUtil.check_sync_lock_file(workspace_id, unique_id)
    RemoteSyncLockFileUtil.delete_sync_lock_file(workspace_id, unique_id)
