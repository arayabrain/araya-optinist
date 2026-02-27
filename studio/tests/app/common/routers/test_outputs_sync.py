"""
Tests for outputs.py sync-related functions.

Covers gaps #2, #3, #4:
- _download_input_files() helper
- get_csv() endpoint refactoring (404 instead of 500)
- get_image() endpoint refactoring (_ensure_input_file_synced, error messages)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from studio.app.common.routers.outputs import (
    _download_input_files,
    _ensure_input_file_synced,
    get_csv,
    get_image,
)


class TestDownloadInputFiles:
    """Tests for _download_input_files() helper (Gap #2)."""

    @pytest.mark.asyncio
    async def test_skips_when_remote_storage_unavailable(self):
        """No-op when remote storage is not available."""
        with patch(
            "studio.app.common.routers.outputs.RemoteStorageController.is_available",
            return_value=False,
        ):
            # Should not raise
            await _download_input_files("ws1", "uid1", "bucket1")

    @pytest.mark.asyncio
    async def test_skips_when_no_input_filenames(self):
        """No-op when SmkUtils returns empty list."""
        with patch(
            "studio.app.common.routers.outputs.RemoteStorageController.is_available",
            return_value=True,
        ), patch(
            "studio.app.common.routers.outputs.SmkUtils.get_datatypes_inputs",
            return_value=[],
        ):
            await _download_input_files("ws1", "uid1", "bucket1")

    @pytest.mark.asyncio
    async def test_downloads_missing_input_files(self):
        """Downloads input files that don't exist locally."""
        mock_controller = MagicMock()
        mock_controller.download_input_data = AsyncMock()

        with patch(
            "studio.app.common.routers.outputs.RemoteStorageController.is_available",
            return_value=True,
        ), patch(
            "studio.app.common.routers.outputs.SmkUtils.get_datatypes_inputs",
            return_value=["input.tiff", "data.csv"],
        ), patch(
            "studio.app.common.routers.outputs.RemoteStorageController",
            return_value=mock_controller,
        ), patch(
            "os.path.exists", return_value=False
        ):
            await _download_input_files("ws1", "uid1", "bucket1")

        assert mock_controller.download_input_data.call_count == 2
        mock_controller.download_input_data.assert_any_call("ws1", "input.tiff")
        mock_controller.download_input_data.assert_any_call("ws1", "data.csv")

    @pytest.mark.asyncio
    async def test_skips_existing_input_files(self):
        """Does not download files that already exist locally."""
        mock_controller = MagicMock()
        mock_controller.download_input_data = AsyncMock()

        with patch(
            "studio.app.common.routers.outputs.RemoteStorageController.is_available",
            return_value=True,
        ), patch(
            "studio.app.common.routers.outputs.SmkUtils.get_datatypes_inputs",
            return_value=["input.tiff"],
        ), patch(
            "studio.app.common.routers.outputs.RemoteStorageController",
            return_value=mock_controller,
        ), patch(
            "os.path.exists", return_value=True
        ):
            await _download_input_files("ws1", "uid1", "bucket1")

        mock_controller.download_input_data.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_assertion_error_silently(self):
        """AssertionError from missing snakemake_config.yaml is swallowed."""
        with patch(
            "studio.app.common.routers.outputs.RemoteStorageController.is_available",
            return_value=True,
        ), patch(
            "studio.app.common.routers.outputs.SmkUtils.get_datatypes_inputs",
            side_effect=AssertionError("missing config"),
        ):
            # Should not raise
            await _download_input_files("ws1", "uid1", "bucket1")

    @pytest.mark.asyncio
    async def test_handles_key_error_silently(self):
        """KeyError from incomplete config is swallowed."""
        with patch(
            "studio.app.common.routers.outputs.RemoteStorageController.is_available",
            return_value=True,
        ), patch(
            "studio.app.common.routers.outputs.SmkUtils.get_datatypes_inputs",
            side_effect=KeyError("datatypes"),
        ):
            await _download_input_files("ws1", "uid1", "bucket1")

    @pytest.mark.asyncio
    async def test_handles_generic_exception_with_warning(self):
        """Generic exceptions are logged as warnings, not re-raised."""
        with patch(
            "studio.app.common.routers.outputs.RemoteStorageController.is_available",
            return_value=True,
        ), patch(
            "studio.app.common.routers.outputs.SmkUtils.get_datatypes_inputs",
            side_effect=RuntimeError("S3 connection failed"),
        ):
            await _download_input_files("ws1", "uid1", "bucket1")


class TestEnsureInputFileSynced:
    """Tests for _ensure_input_file_synced() helper."""

    @pytest.mark.asyncio
    async def test_returns_true_when_file_exists_locally(self):
        """Returns True immediately when file exists on disk."""
        with patch(
            "studio.app.common.routers.outputs.os.path.exists", return_value=True
        ):
            result = await _ensure_input_file_synced("ws1", "data.csv", "bucket1")
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_no_remote_storage(self):
        """Returns False when no remote storage and file missing."""
        with patch(
            "studio.app.common.routers.outputs.os.path.exists", return_value=False
        ), patch(
            "studio.app.common.routers.outputs.RemoteStorageController.is_available",
            return_value=False,
        ):
            result = await _ensure_input_file_synced("ws1", "data.csv", "bucket1")
        assert result is False

    @pytest.mark.asyncio
    async def test_downloads_from_remote_storage(self):
        """Downloads file from S3 when available."""
        mock_controller = MagicMock()
        mock_controller.download_input_data = AsyncMock(return_value=True)

        # First os.path.exists call: file not found locally
        # Second os.path.exists call: after download, file exists
        with patch(
            "studio.app.common.routers.outputs.os.path.exists",
            side_effect=[False, True],
        ), patch(
            "studio.app.common.routers.outputs.RemoteStorageController.is_available",
            return_value=True,
        ), patch(
            "studio.app.common.routers.outputs.RemoteStorageController",
            return_value=mock_controller,
        ):
            result = await _ensure_input_file_synced("ws1", "data.csv", "bucket1")

        assert result is True
        mock_controller.download_input_data.assert_called_once_with("ws1", "data.csv")

    @pytest.mark.asyncio
    async def test_returns_false_when_not_in_s3(self):
        """Returns False when file doesn't exist in S3."""
        mock_controller = MagicMock()
        mock_controller.download_input_data = AsyncMock(return_value=False)

        with patch(
            "studio.app.common.routers.outputs.os.path.exists", return_value=False
        ), patch(
            "studio.app.common.routers.outputs.RemoteStorageController.is_available",
            return_value=True,
        ), patch(
            "studio.app.common.routers.outputs.RemoteStorageController",
            return_value=mock_controller,
        ):
            result = await _ensure_input_file_synced("ws1", "data.csv", "bucket1")

        assert result is False

    @pytest.mark.asyncio
    async def test_raises_503_on_s3_exception(self):
        """Raises HTTPException 503 on transient S3 errors."""
        mock_controller = MagicMock()
        mock_controller.download_input_data = AsyncMock(
            side_effect=RuntimeError("S3 timeout")
        )

        with patch(
            "studio.app.common.routers.outputs.os.path.exists", return_value=False
        ), patch(
            "studio.app.common.routers.outputs.RemoteStorageController.is_available",
            return_value=True,
        ), patch(
            "studio.app.common.routers.outputs.RemoteStorageController",
            return_value=mock_controller,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await _ensure_input_file_synced("ws1", "data.csv", "bucket1")

            assert exc_info.value.status_code == 503
            assert "cloud storage" in exc_info.value.detail


class TestGetCsvEndpoint:
    """Tests for get_csv() endpoint refactoring (Gap #3)."""

    @pytest.mark.asyncio
    async def test_returns_404_when_file_not_synced(self):
        """Returns 404 instead of bare 500 FileNotFoundError."""
        with patch(
            "studio.app.common.routers.outputs._ensure_input_file_synced",
            new_callable=AsyncMock,
            return_value=False,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_csv(
                    filepath="data.csv",
                    workspace_id="ws1",
                    remote_bucket_name="bucket1",
                )

            assert exc_info.value.status_code == 404
            assert "Input CSV file not found" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_returns_404_when_file_missing_after_sync(self):
        """Returns 404 when sync succeeds but file still missing."""
        with patch(
            "studio.app.common.routers.outputs._ensure_input_file_synced",
            new_callable=AsyncMock,
            return_value=True,
        ), patch("os.path.exists", return_value=False):
            with pytest.raises(HTTPException) as exc_info:
                await get_csv(
                    filepath="data.csv",
                    workspace_id="ws1",
                    remote_bucket_name="bucket1",
                )

            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_propagates_503_from_sync_helper(self):
        """503 from _ensure_input_file_synced propagates to caller."""
        with patch(
            "studio.app.common.routers.outputs._ensure_input_file_synced",
            new_callable=AsyncMock,
            side_effect=HTTPException(
                status_code=503,
                detail="Failed to sync input file from cloud storage",
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_csv(
                    filepath="data.csv",
                    workspace_id="ws1",
                    remote_bucket_name="bucket1",
                )

            assert exc_info.value.status_code == 503


class TestGetImageEndpoint:
    """Tests for get_image() endpoint refactoring (Gap #4)."""

    @pytest.mark.asyncio
    async def test_tiff_input_returns_404_when_not_synced(self):
        """TIFF input file returns 404 with descriptive message."""
        with patch(
            "studio.app.common.routers.outputs._ensure_visualization_synced",
            new_callable=AsyncMock,
        ), patch(
            "studio.app.common.routers.outputs._ensure_input_file_synced",
            new_callable=AsyncMock,
            return_value=False,
        ), patch(
            "studio.app.common.routers.outputs.normalize_output_path",
            return_value="input.tif",
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_image(
                    filepath="input.tif",
                    workspace_id="ws1",
                    unique_id="uid1",
                    start_index=0,
                    end_index=10,
                    remote_bucket_name="bucket1",
                )

            assert exc_info.value.status_code == 404
            assert "Input image file not found" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_json_output_returns_503_when_experiment_dir_missing(self):
        """JSON output returns 503 when entire experiment dir is missing."""
        with patch(
            "studio.app.common.routers.outputs._ensure_visualization_synced",
            new_callable=AsyncMock,
        ), patch(
            "studio.app.common.routers.outputs.normalize_output_path",
            return_value="ws1/uid1/output/cell_roi.json",
        ), patch(
            "os.path.exists", return_value=False
        ), patch(
            "os.path.splitext",
            return_value=("cell_roi", ".json"),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_image(
                    filepath="ws1/uid1/output/cell_roi.json",
                    workspace_id="ws1",
                    unique_id="uid1",
                    start_index=0,
                    end_index=10,
                    remote_bucket_name="bucket1",
                )

            assert exc_info.value.status_code == 503
            assert "syncing" in exc_info.value.detail.lower()
