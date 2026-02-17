"""
Note of this file:
- This file is the test code for RemoteStorageController.
- RemoteStorageController's mode (Mock, S3, ...)
  is set by the value of the environment variable (.env).
"""

import os
import shutil

import pytest

from studio.app.common.core.auth.auth_dependencies import _get_user_remote_bucket_name
from studio.app.common.core.mode import MODE
from studio.app.common.core.storage.remote_storage_controller import (  # noqa: E402
    RemoteStorageController,
    RemoteStorageDeleter,
    RemoteStorageReader,
    RemoteStorageSimpleReader,
    RemoteStorageSimpleWriter,
    RemoteStorageType,
    RemoteStorageWriter,
    RemoteSyncAction,
    RemoteSyncLockFileUtil,
    RemoteSyncStatusFileUtil,
)
from studio.app.dir_path import DIRPATH

# Set test mode before getting bucket name at module level
MODE.IS_TEST = True
remote_bucket_name = _get_user_remote_bucket_name()
workspace_id = "default"
unique_id = "remote_storage_test"


def test_initialize():
    if not RemoteStorageController.is_available():
        print("RemoteStorageController is available, skip this test.")
        return

    # ----------------------------------------
    # copy output test data
    # ----------------------------------------

    test_data_output_src_path = (
        f"{DIRPATH.DATA_DIR}/output_test/{workspace_id}/{unique_id}"
    )
    test_data_output_dst_path = f"{DIRPATH.DATA_DIR}/output/{workspace_id}/{unique_id}"

    # cleaning local storage
    if os.path.exists(test_data_output_dst_path):
        shutil.rmtree(test_data_output_dst_path)

    # copy test data
    shutil.copytree(
        test_data_output_src_path,
        test_data_output_dst_path,
        dirs_exist_ok=True,
    )


@pytest.mark.asyncio
async def test_RemoteSyncLockFileUtil():
    if not RemoteStorageController.is_available():
        print("RemoteStorageController is available, skip this test.")
        return

    RemoteSyncLockFileUtil.create_sync_lock_file(workspace_id, unique_id)
    is_locked = RemoteSyncLockFileUtil.check_sync_lock_file(workspace_id, unique_id)
    assert is_locked, "check_sync_lock_file failed.."

    RemoteSyncLockFileUtil.delete_sync_lock_file(workspace_id, unique_id)
    is_locked = RemoteSyncLockFileUtil.check_sync_lock_file(workspace_id, unique_id)
    assert not is_locked, "check_sync_lock_file failed.."

    # ------------------------------------------------------------
    # Check automatic processing of status file
    #   by RemoteStorageReader ContextManager.
    # ------------------------------------------------------------

    async with RemoteStorageReader(
        remote_bucket_name, workspace_id, unique_id
    ) as remote_storage_controller:
        is_locked = RemoteSyncLockFileUtil.check_sync_lock_file(workspace_id, unique_id)
        assert is_locked, "check_sync_lock_file failed.."

        del remote_storage_controller  # not used

    is_locked = RemoteSyncLockFileUtil.check_sync_lock_file(workspace_id, unique_id)
    assert not is_locked, "check_sync_lock_file failed.."


def test_RemoteSyncStatusFileUtil():
    if not RemoteStorageController.is_available():
        print("RemoteStorageController is available, skip this test.")
        return

    # test create_sync_status_file()
    RemoteSyncStatusFileUtil.create_sync_status_file_for_success(
        remote_bucket_name, workspace_id, unique_id, RemoteSyncAction.UPLOAD
    )
    is_remote_sync_status_ok = RemoteSyncStatusFileUtil.check_sync_status_success(
        workspace_id, unique_id
    )
    assert is_remote_sync_status_ok, "create_sync_status_file failed.."

    # test get_remote_bucket_name()
    remote_bucket_name_ = RemoteSyncStatusFileUtil.get_remote_bucket_name(
        workspace_id, unique_id
    )
    assert remote_bucket_name_, "get_remote_bucket_name failed.."

    # test delete_sync_status_file()
    RemoteSyncStatusFileUtil.delete_sync_status_file(workspace_id, unique_id)


@pytest.mark.asyncio
async def test_RemoteStorageController_crud_bucket():
    if not RemoteStorageController.is_available():
        print("RemoteStorageController is available, skip this test.")
        return
    elif RemoteStorageType.get_activated_type() != RemoteStorageType.S3:
        print("RemoteStorageType is not covered, skip this test.")
        return

    new_bucket_name = "test-optinist-dummy-bucket-0123"

    async with RemoteStorageSimpleReader(new_bucket_name) as remote_storage_controller:
        result = await remote_storage_controller.create_bucket()
        assert result, f"create bucket failed. [{new_bucket_name}]"

    async with RemoteStorageSimpleWriter(new_bucket_name) as remote_storage_controller:
        result = await remote_storage_controller.delete_bucket(force_delete=True)
        assert result, f"delete bucket failed. [{new_bucket_name}]"


@pytest.mark.asyncio
async def test_RemoteStorageController_operate_input_data():
    if not RemoteStorageController.is_available():
        print("RemoteStorageController is available, skip this test.")
        return

    input_file_name = "mouse2p_short_image.tiff"

    # upload input data
    async with RemoteStorageSimpleWriter(
        remote_bucket_name
    ) as remote_storage_controller:
        result = await remote_storage_controller.upload_input_data(
            workspace_id, input_file_name
        )
    assert result, "upload_input_data failed.."

    # cleaning local storage
    test_input_data_local_path = (
        f"{DIRPATH.DATA_DIR}/input/{workspace_id}/{input_file_name}"
    )
    os.remove(test_input_data_local_path)

    # download input data
    async with RemoteStorageSimpleReader(
        remote_bucket_name
    ) as remote_storage_controller:
        result = await remote_storage_controller.download_input_data(
            workspace_id, input_file_name
        )
    assert result, "download_input_data failed.."

    # delete input data
    async with RemoteStorageSimpleWriter(
        remote_bucket_name
    ) as remote_storage_controller:
        result = await remote_storage_controller.delete_input_data(
            workspace_id, input_file_name
        )
    assert result, "delete_input_data failed.."


@pytest.mark.asyncio
async def test_RemoteStorageController_upload_experiment():
    if not RemoteStorageController.is_available():
        print("RemoteStorageController is available, skip this test.")
        return

    # upload specific files to remote
    async with RemoteStorageWriter(
        remote_bucket_name, workspace_id, unique_id
    ) as remote_storage_controller:
        target_files = [DIRPATH.EXPERIMENT_YML, DIRPATH.WORKFLOW_YML]
        await remote_storage_controller.upload_experiment(
            workspace_id, unique_id, target_files
        )

    # delete remote files
    async with RemoteStorageDeleter(
        remote_bucket_name, workspace_id, unique_id
    ) as remote_storage_controller:
        await remote_storage_controller.delete_experiment(workspace_id, unique_id)

    # upload all files to remote
    async with RemoteStorageWriter(
        remote_bucket_name, workspace_id, unique_id
    ) as remote_storage_controller:
        await remote_storage_controller.upload_experiment(workspace_id, unique_id)


@pytest.mark.asyncio
async def test_RemoteStorageController_download_experiment():
    if not RemoteStorageController.is_available():
        print("RemoteStorageController is available, skip this test.")
        return

    test_data_output_path = f"{DIRPATH.DATA_DIR}/output/{workspace_id}/{unique_id}"
    test_data_output_experiment_yaml = (
        f"{test_data_output_path}/{DIRPATH.EXPERIMENT_YML}"
    )

    # cleaning local storage
    if os.path.exists(test_data_output_path):
        shutil.rmtree(test_data_output_path)
        os.makedirs(test_data_output_path)

    # download remote metadata files
    async with RemoteStorageSimpleReader(
        remote_bucket_name
    ) as remote_storage_controller:
        # download all workspaces metadata
        await remote_storage_controller.download_all_experiments_metas()
        assert os.path.isfile(
            test_data_output_experiment_yaml
        ), "download_all_experiments_metas failed.."

        # download specified workspaces metadata
        await remote_storage_controller.download_all_experiments_metas([workspace_id])
        assert os.path.isfile(
            test_data_output_experiment_yaml
        ), "download_all_experiments_metas failed.."

    # re cleaning local storage
    if os.path.exists(test_data_output_path):
        shutil.rmtree(test_data_output_path)
        os.makedirs(test_data_output_path)

    # download remote files
    async with RemoteStorageReader(
        remote_bucket_name, workspace_id, unique_id
    ) as remote_storage_controller:
        await remote_storage_controller.download_experiment(workspace_id, unique_id)
        assert os.path.isfile(
            test_data_output_experiment_yaml
        ), "download_experiment failed.."


@pytest.mark.asyncio
async def test_RemoteStorageController_list_input_data_objects():
    """Test list_input_data_objects returns correct format."""
    if not RemoteStorageController.is_available():
        print("RemoteStorageController is not available, skip this test.")
        return

    input_file_name = "mouse2p_short_image.tiff"

    # First upload a file to ensure there's something to list
    async with RemoteStorageSimpleWriter(
        remote_bucket_name
    ) as remote_storage_controller:
        await remote_storage_controller.upload_input_data(workspace_id, input_file_name)

    # Test list_input_data_objects
    async with RemoteStorageSimpleReader(
        remote_bucket_name
    ) as remote_storage_controller:
        objects = await remote_storage_controller.list_input_data_objects(workspace_id)

        # Verify the result is a list
        assert isinstance(objects, list), "list_input_data_objects should return a list"

        # Verify at least one object exists
        assert (
            len(objects) > 0
        ), "list_input_data_objects should return at least one object"

        # Verify the format of the returned objects
        for obj in objects:
            assert "filename" in obj, "Each object should have 'filename'"
            assert "size" in obj, "Each object should have 'size'"
            assert "last_modified" in obj, "Each object should have 'last_modified'"
            assert isinstance(obj["filename"], str), "'filename' should be a string"
            assert isinstance(obj["size"], int), "'size' should be an integer"

        # Verify our uploaded file is in the list
        filenames = [obj["filename"] for obj in objects]
        assert (
            input_file_name in filenames
        ), f"Uploaded file '{input_file_name}' should be in the list"

    # Cleanup
    async with RemoteStorageSimpleWriter(
        remote_bucket_name
    ) as remote_storage_controller:
        await remote_storage_controller.delete_input_data(workspace_id, input_file_name)


class TestSyncStatusOnPartialFailure:
    """Tests for Case 69: Verify sync status reflects actual operation result."""

    @pytest.mark.asyncio
    async def test_upload_experiment_false_result_marks_error_status(self):
        """When upload_experiment returns False, status should be ERROR."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_controller = MagicMock()
        mock_controller.upload_experiment = AsyncMock(return_value=False)

        with patch.object(
            RemoteSyncStatusFileUtil,
            "create_sync_status_file_for_success",
        ) as mock_success, patch.object(
            RemoteSyncStatusFileUtil,
            "create_sync_status_file_for_error",
        ) as mock_error, patch.object(
            RemoteSyncStatusFileUtil,
            "create_sync_status_file_for_processing",
        ) as mock_processing:
            controller = RemoteStorageController.__new__(RemoteStorageController)
            controller._RemoteStorageController__controller = mock_controller
            controller._RemoteStorageController__remote_bucket_name = "test-bucket"

            result = await controller.upload_experiment("ws1", "exp1", None)

            assert result is False
            mock_processing.assert_called_once()
            mock_error.assert_called_once()
            mock_success.assert_not_called()

    @pytest.mark.asyncio
    async def test_upload_experiment_true_result_marks_success_status(self):
        """When upload_experiment returns True, status should be SUCCESS."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_controller = MagicMock()
        mock_controller.upload_experiment = AsyncMock(return_value=True)

        with patch.object(
            RemoteSyncStatusFileUtil, "create_sync_status_file_for_success"
        ) as mock_success, patch.object(
            RemoteSyncStatusFileUtil, "create_sync_status_file_for_error"
        ) as mock_error, patch.object(
            RemoteSyncStatusFileUtil, "create_sync_status_file_for_processing"
        ) as mock_processing:
            controller = RemoteStorageController.__new__(RemoteStorageController)
            controller._RemoteStorageController__controller = mock_controller
            controller._RemoteStorageController__remote_bucket_name = "test-bucket"

            result = await controller.upload_experiment("ws1", "exp1", None)

            assert result is True
            mock_processing.assert_called_once()
            mock_success.assert_called_once()
            mock_error.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_experiment_false_result_marks_error_status(self):
        """When delete_experiment returns False, status should be ERROR."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_controller = MagicMock()
        mock_controller.delete_experiment = AsyncMock(return_value=False)

        with patch.object(
            RemoteSyncStatusFileUtil,
            "create_sync_status_file_for_success",
        ) as mock_success, patch.object(
            RemoteSyncStatusFileUtil,
            "create_sync_status_file_for_error",
        ) as mock_error, patch.object(
            RemoteSyncStatusFileUtil,
            "create_sync_status_file_for_processing",
        ) as mock_processing:
            controller = RemoteStorageController.__new__(RemoteStorageController)
            controller._RemoteStorageController__controller = mock_controller
            controller._RemoteStorageController__remote_bucket_name = "test-bucket"

            result = await controller.delete_experiment("ws1", "exp1")

            assert result is False
            mock_processing.assert_called_once()
            mock_error.assert_called_once()
            mock_success.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_experiment_true_result_marks_success_status(self):
        """When delete_experiment returns True, status should be SUCCESS."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_controller = MagicMock()
        mock_controller.delete_experiment = AsyncMock(return_value=True)

        with patch.object(
            RemoteSyncStatusFileUtil, "create_sync_status_file_for_success"
        ) as mock_success, patch.object(
            RemoteSyncStatusFileUtil, "create_sync_status_file_for_error"
        ) as mock_error, patch.object(
            RemoteSyncStatusFileUtil, "create_sync_status_file_for_processing"
        ) as mock_processing:
            controller = RemoteStorageController.__new__(RemoteStorageController)
            controller._RemoteStorageController__controller = mock_controller
            controller._RemoteStorageController__remote_bucket_name = "test-bucket"

            result = await controller.delete_experiment("ws1", "exp1")

            assert result is True
            mock_processing.assert_called_once()
            mock_success.assert_called_once()
            mock_error.assert_not_called()
