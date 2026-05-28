"""
Unit tests for S3StorageController class.

Tests S3 operations including download_experiment with sync_mode filtering.
"""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from studio.app.common.core.storage.remote_storage_controller import (
    RemoteExperimentNotFoundError,
    RemoteExperimentSyncMode,
)
from studio.app.common.core.storage.s3_storage_controller import S3StorageController


class TestS3StorageControllerInit:
    """Tests for S3StorageController initialization"""

    def test_init_with_bucket_name(self):
        """Controller initializes with bucket name"""
        controller = S3StorageController("test-bucket")
        assert controller.bucket_name == "test-bucket"

    def test_init_without_bucket_name_raises(self):
        """Controller raises on empty bucket name"""
        with pytest.raises(AssertionError):
            S3StorageController("")

    def test_init_with_none_bucket_raises(self):
        """Controller raises on None bucket name"""
        with pytest.raises(AssertionError):
            S3StorageController(None)


class TestS3StorageControllerPathBuilding:
    """Tests for path building methods"""

    def setup_method(self):
        self.controller = S3StorageController("test-bucket")

    def test_make_experiment_remote_path(self):
        """Remote path includes app/studio_data prefix"""
        path = self.controller._make_experiment_remote_path("1", "exp123")
        assert path == "app/studio_data/output/1/exp123"

    def test_make_experiment_local_path(self):
        """Local path uses DIRPATH.OUTPUT_DIR"""
        with patch(
            "studio.app.common.core.storage.s3_storage_controller.DIRPATH"
        ) as mock_dirpath:
            mock_dirpath.OUTPUT_DIR = "/app/studio_data/output"
            path = self.controller._make_experiment_local_path("1", "exp123")
            assert path == "/app/studio_data/output/1/exp123"

    def test_make_input_data_remote_path(self):
        """Input remote path includes app/studio_data prefix"""
        path = self.controller._make_input_data_remote_path("1", "data.tiff")
        assert path == "app/studio_data/input/1/data.tiff"

    def test_make_input_data_local_path(self):
        """Input local path uses DIRPATH.INPUT_DIR"""
        with patch(
            "studio.app.common.core.storage.s3_storage_controller.DIRPATH"
        ) as mock_dirpath:
            mock_dirpath.INPUT_DIR = "/app/studio_data/input"
            path = self.controller._make_input_data_local_path("1", "data.tiff")
            assert path == "/app/studio_data/input/1/data.tiff"


class TestS3StorageControllerDownloadExperiment:
    """Tests for download_experiment method with sync_mode"""

    def setup_method(self):
        self.controller = S3StorageController("test-bucket")

    @pytest.mark.asyncio
    async def test_download_experiment_empty_s3_raises_not_found(self):
        """Raises RemoteExperimentNotFoundError when S3 prefix has no objects"""
        mock_client = AsyncMock()
        mock_client.list_objects_v2.return_value = {"KeyCount": 0}
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch.object(
            self.controller,
            "_S3StorageController__get_s3_client",
            return_value=mock_client,
        ):
            with pytest.raises(RemoteExperimentNotFoundError):
                await self.controller.download_experiment("1", "exp123")

    @pytest.mark.asyncio
    async def test_download_experiment_all_mode(self):
        """All mode downloads all files"""
        mock_client = AsyncMock()
        mock_client.list_objects_v2.return_value = {
            "KeyCount": 3,
            "Contents": [
                {"Key": "app/studio_data/output/1/exp123/experiment.yaml", "Size": 100},
                {"Key": "app/studio_data/output/1/exp123/data.pkl", "Size": 1000000},
                {"Key": "app/studio_data/output/1/exp123/image.tiff", "Size": 500000},
            ],
        }
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        downloaded_files = []

        async def mock_download(bucket, s3_path, local_path):
            downloaded_files.append(s3_path)

        mock_client.download_file = mock_download

        with patch.object(
            self.controller,
            "_S3StorageController__get_s3_client",
            return_value=mock_client,
        ), patch("os.path.exists", return_value=False), patch(
            "os.path.isfile", return_value=False
        ), patch(
            "os.path.isdir", return_value=False
        ), patch(
            "os.makedirs"
        ), patch(
            "os.replace"
        ), patch(
            "studio.app.common.core.storage.s3_storage_controller.DIRPATH"
        ) as mock_dirpath:
            mock_dirpath.OUTPUT_DIR = "/app/studio_data/output"

            result = await self.controller.download_experiment(
                "1", "exp123", sync_mode=RemoteExperimentSyncMode.ALL
            )

        assert result is True
        assert len(downloaded_files) == 3

    @pytest.mark.asyncio
    async def test_download_experiment_essential_only_mode(self):
        """Essential only mode skips large files"""
        mock_client = AsyncMock()
        mock_client.list_objects_v2.return_value = {
            "KeyCount": 3,
            "Contents": [
                {"Key": "app/studio_data/output/1/exp123/experiment.yaml", "Size": 100},
                {"Key": "app/studio_data/output/1/exp123/data.pkl", "Size": 1000000},
                {"Key": "app/studio_data/output/1/exp123/cell_roi.json", "Size": 500},
            ],
        }
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        downloaded_files = []

        async def mock_download(bucket, s3_path, local_path):
            downloaded_files.append(s3_path)

        mock_client.download_file = mock_download

        with patch.object(
            self.controller,
            "_S3StorageController__get_s3_client",
            return_value=mock_client,
        ), patch("os.path.exists", return_value=False), patch(
            "os.path.isfile", return_value=False
        ), patch(
            "os.path.isdir", return_value=False
        ), patch(
            "os.makedirs"
        ), patch(
            "os.replace"
        ), patch(
            "studio.app.common.core.storage.s3_storage_controller.DIRPATH"
        ) as mock_dirpath:
            mock_dirpath.OUTPUT_DIR = "/app/studio_data/output"

            result = await self.controller.download_experiment(
                "1", "exp123", sync_mode=RemoteExperimentSyncMode.ESSENTIAL_ONLY
            )

        assert result is True
        # Should skip data.pkl (large file)
        assert len(downloaded_files) == 2
        assert any("experiment.yaml" in f for f in downloaded_files)
        assert any("cell_roi.json" in f for f in downloaded_files)
        assert not any("data.pkl" in f for f in downloaded_files)

    @pytest.mark.asyncio
    async def test_download_experiment_thumbnails_only_mode(self):
        """Thumbnails only mode only downloads thumbnail files"""
        mock_client = AsyncMock()
        mock_client.list_objects_v2.return_value = {
            "KeyCount": 4,
            "Contents": [
                {"Key": "app/studio_data/output/1/exp123/experiment.yaml", "Size": 100},
                {
                    "Key": "app/studio_data/output/1/exp123/thumbnails/input_thumb.png",
                    "Size": 5000,
                },
                {
                    "Key": "app/studio_data/output/1/exp123/thumbnails/roi_thumb.png",
                    "Size": 5000,
                },
                {"Key": "app/studio_data/output/1/exp123/data.pkl", "Size": 1000000},
            ],
        }
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        downloaded_files = []

        async def mock_download(bucket, s3_path, local_path):
            downloaded_files.append(s3_path)

        mock_client.download_file = mock_download

        with patch.object(
            self.controller,
            "_S3StorageController__get_s3_client",
            return_value=mock_client,
        ), patch("os.path.exists", return_value=False), patch(
            "os.path.isfile", return_value=False
        ), patch(
            "os.path.isdir", return_value=False
        ), patch(
            "os.makedirs"
        ), patch(
            "os.replace"
        ), patch(
            "studio.app.common.core.storage.s3_storage_controller.DIRPATH"
        ) as mock_dirpath:
            mock_dirpath.OUTPUT_DIR = "/app/studio_data/output"

            result = await self.controller.download_experiment(
                "1", "exp123", sync_mode=RemoteExperimentSyncMode.THUMBNAILS_ONLY
            )

        assert result is True
        # Should only download thumbnail files
        assert len(downloaded_files) == 2
        assert all("_thumb.png" in f for f in downloaded_files)

    @pytest.mark.asyncio
    async def test_download_experiment_visualization_mode(self):
        """Visualization mode downloads JSON, TIFF, and YAML files"""
        mock_client = AsyncMock()
        mock_client.list_objects_v2.return_value = {
            "KeyCount": 5,
            "Contents": [
                {"Key": "app/studio_data/output/1/exp123/experiment.yaml", "Size": 100},
                {"Key": "app/studio_data/output/1/exp123/cell_roi.json", "Size": 500},
                {"Key": "app/studio_data/output/1/exp123/image.tiff", "Size": 500000},
                {"Key": "app/studio_data/output/1/exp123/data.pkl", "Size": 1000000},
                {"Key": "app/studio_data/output/1/exp123/model.h5", "Size": 2000000},
            ],
        }
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        downloaded_files = []

        async def mock_download(bucket, s3_path, local_path):
            downloaded_files.append(s3_path)

        mock_client.download_file = mock_download

        with patch.object(
            self.controller,
            "_S3StorageController__get_s3_client",
            return_value=mock_client,
        ), patch("os.path.exists", return_value=False), patch(
            "os.path.isfile", return_value=False
        ), patch(
            "os.path.isdir", return_value=False
        ), patch(
            "os.makedirs"
        ), patch(
            "os.replace"
        ), patch(
            "studio.app.common.core.storage.s3_storage_controller.DIRPATH"
        ) as mock_dirpath:
            mock_dirpath.OUTPUT_DIR = "/app/studio_data/output"

            result = await self.controller.download_experiment(
                "1", "exp123", sync_mode=RemoteExperimentSyncMode.VISUALIZATION
            )

        assert result is True
        # Should download yaml, json, and tiff only
        assert len(downloaded_files) == 3
        assert any("experiment.yaml" in f for f in downloaded_files)
        assert any("cell_roi.json" in f for f in downloaded_files)
        assert any("image.tiff" in f for f in downloaded_files)
        assert not any("data.pkl" in f for f in downloaded_files)
        assert not any("model.h5" in f for f in downloaded_files)

    @pytest.mark.asyncio
    async def test_download_experiment_skips_existing_files(self):
        """Skips files that already exist locally with correct size"""
        mock_client = AsyncMock()
        mock_client.list_objects_v2.return_value = {
            "KeyCount": 2,
            "Contents": [
                {"Key": "app/studio_data/output/1/exp123/experiment.yaml", "Size": 100},
                {"Key": "app/studio_data/output/1/exp123/cell_roi.json", "Size": 500},
            ],
        }
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        downloaded_files = []

        async def mock_download(bucket, s3_path, local_path):
            downloaded_files.append(s3_path)

        mock_client.download_file = mock_download

        def mock_isfile(path):
            # First file exists, second doesn't
            return "experiment.yaml" in path

        def mock_getsize(path):
            return 100  # Same size as S3

        with patch.object(
            self.controller,
            "_S3StorageController__get_s3_client",
            return_value=mock_client,
        ), patch("os.path.exists", return_value=False), patch(
            "os.path.isfile", side_effect=mock_isfile
        ), patch(
            "os.path.getsize", side_effect=mock_getsize
        ), patch(
            "os.path.isdir", return_value=False
        ), patch(
            "os.makedirs"
        ), patch(
            "os.replace"
        ), patch(
            "studio.app.common.core.storage.s3_storage_controller.DIRPATH"
        ) as mock_dirpath:
            mock_dirpath.OUTPUT_DIR = "/app/studio_data/output"

            result = await self.controller.download_experiment("1", "exp123")

        assert result is True
        # Should only download the file that doesn't exist
        assert len(downloaded_files) == 1
        assert "cell_roi.json" in downloaded_files[0]

    @pytest.mark.asyncio
    async def test_download_experiment_skips_coordination_files(self):
        """Skips coordination files (remote_sync.lock, remote_sync_stat.json)"""
        mock_client = AsyncMock()
        # Use actual coordination file names from RemoteSyncLockFileUtil and
        # RemoteSyncStatusFileUtil
        mock_client.list_objects_v2.return_value = {
            "KeyCount": 3,
            "Contents": [
                {"Key": "app/studio_data/output/1/exp123/experiment.yaml", "Size": 100},
                {
                    "Key": "app/studio_data/output/1/exp123/remote_sync.lock",
                    "Size": 10,
                },
                {
                    "Key": "app/studio_data/output/1/exp123/remote_sync_stat.json",
                    "Size": 10,
                },
            ],
        }
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        downloaded_files = []

        async def mock_download(bucket, s3_path, local_path):
            downloaded_files.append(s3_path)

        mock_client.download_file = mock_download

        with patch.object(
            self.controller,
            "_S3StorageController__get_s3_client",
            return_value=mock_client,
        ), patch("os.path.exists", return_value=False), patch(
            "os.path.isfile", return_value=False
        ), patch(
            "os.path.isdir", return_value=False
        ), patch(
            "os.makedirs"
        ), patch(
            "os.replace"
        ), patch(
            "studio.app.common.core.storage.s3_storage_controller.DIRPATH"
        ) as mock_dirpath:
            mock_dirpath.OUTPUT_DIR = "/app/studio_data/output"

            result = await self.controller.download_experiment("1", "exp123")

        assert result is True
        # Should only download experiment.yaml, not coordination files
        assert len(downloaded_files) == 1
        assert "experiment.yaml" in downloaded_files[0]

    @pytest.mark.asyncio
    async def test_download_experiment_skips_directory_entries(self):
        """Skips S3 directory entries (keys ending with /)"""
        mock_client = AsyncMock()
        mock_client.list_objects_v2.return_value = {
            "KeyCount": 2,
            "Contents": [
                {"Key": "app/studio_data/output/1/exp123/", "Size": 0},  # Directory
                {"Key": "app/studio_data/output/1/exp123/experiment.yaml", "Size": 100},
            ],
        }
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        downloaded_files = []

        async def mock_download(bucket, s3_path, local_path):
            downloaded_files.append(s3_path)

        mock_client.download_file = mock_download

        with patch.object(
            self.controller,
            "_S3StorageController__get_s3_client",
            return_value=mock_client,
        ), patch("os.path.exists", return_value=False), patch(
            "os.path.isfile", return_value=False
        ), patch(
            "os.path.isdir", return_value=False
        ), patch(
            "os.makedirs"
        ), patch(
            "os.replace"
        ), patch(
            "studio.app.common.core.storage.s3_storage_controller.DIRPATH"
        ) as mock_dirpath:
            mock_dirpath.OUTPUT_DIR = "/app/studio_data/output"

            result = await self.controller.download_experiment("1", "exp123")

        assert result is True
        assert len(downloaded_files) == 1
        assert "experiment.yaml" in downloaded_files[0]


class TestS3StorageControllerUploadThumbnail:
    """Tests for upload_thumbnail method"""

    def setup_method(self):
        self.controller = S3StorageController("test-bucket")

    @pytest.mark.asyncio
    async def test_upload_thumbnail_success(self):
        """Successfully uploads thumbnail to S3"""
        with patch("os.path.exists", return_value=True), patch(
            "os.path.getsize", return_value=5000
        ), patch("asyncio.get_event_loop") as mock_loop, patch("boto3.client"):
            mock_loop_instance = MagicMock()
            mock_loop.return_value = mock_loop_instance
            mock_loop_instance.run_in_executor = AsyncMock(return_value=None)

            result = await self.controller.upload_thumbnail(
                "1", "exp123", "/path/to/input_thumb.png"
            )

        assert result is True

    @pytest.mark.asyncio
    async def test_upload_thumbnail_file_not_found(self):
        """Returns False when thumbnail file doesn't exist"""
        with patch("os.path.exists", return_value=False):
            result = await self.controller.upload_thumbnail(
                "1", "exp123", "/path/to/missing_thumb.png"
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_upload_thumbnail_error_handling(self):
        """Returns False on upload error"""
        with patch("os.path.exists", return_value=True), patch(
            "os.path.getsize", return_value=5000
        ), patch("asyncio.get_event_loop") as mock_loop:
            mock_loop_instance = MagicMock()
            mock_loop.return_value = mock_loop_instance
            mock_loop_instance.run_in_executor = AsyncMock(
                side_effect=Exception("Upload failed")
            )

            result = await self.controller.upload_thumbnail(
                "1", "exp123", "/path/to/input_thumb.png"
            )

        assert result is False


class TestNoSuchBucketErrorHandling:
    """Tests for NoSuchBucket error handling"""

    def test_is_no_such_bucket_error_with_client_error(self):
        """Detects NoSuchBucket from ClientError"""
        from botocore.exceptions import ClientError

        from studio.app.common.core.storage.s3_storage_controller import (
            is_no_such_bucket_error,
        )

        error = ClientError(
            {"Error": {"Code": "NoSuchBucket", "Message": "Bucket does not exist"}},
            "ListObjectsV2",
        )
        assert is_no_such_bucket_error(error) is True

    def test_is_no_such_bucket_error_with_other_client_error(self):
        """Returns False for other ClientError codes"""
        from botocore.exceptions import ClientError

        from studio.app.common.core.storage.s3_storage_controller import (
            is_no_such_bucket_error,
        )

        error = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Access denied"}},
            "ListObjectsV2",
        )
        assert is_no_such_bucket_error(error) is False

    def test_is_no_such_bucket_error_with_regular_exception(self):
        """Returns False for regular exceptions"""
        from studio.app.common.core.storage.s3_storage_controller import (
            is_no_such_bucket_error,
        )

        error = Exception("Some other error")
        assert is_no_such_bucket_error(error) is False


class TestListInputDataObjectsNoSuchBucket:
    """Tests for list_input_data_objects NoSuchBucket handling"""

    def setup_method(self):
        self.controller = S3StorageController("test-bucket")

    @pytest.mark.asyncio
    async def test_list_input_data_objects_no_such_bucket_returns_empty(self):
        """Returns empty list when bucket does not exist"""
        from botocore.exceptions import ClientError

        mock_client = AsyncMock()
        mock_client.list_objects_v2.side_effect = ClientError(
            {"Error": {"Code": "NoSuchBucket", "Message": "Bucket does not exist"}},
            "ListObjectsV2",
        )
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch.object(
            self.controller,
            "_S3StorageController__get_s3_client",
            return_value=mock_client,
        ):
            result = await self.controller.list_input_data_objects("1")

        assert result == []


class TestDownloadAllExperimentsMetasNoSuchBucket:
    """Tests for download_all_experiments_metas NoSuchBucket handling"""

    def setup_method(self):
        self.controller = S3StorageController("test-bucket")

    @pytest.mark.asyncio
    async def test_download_all_experiments_metas_no_such_bucket_raises(self):
        """Raises RemoteStorageBucketNotFoundError when bucket does not exist"""
        from botocore.exceptions import ClientError

        from studio.app.common.core.storage.remote_storage_controller import (
            RemoteStorageBucketNotFoundError,
        )

        mock_client = AsyncMock()
        mock_client.list_objects_v2.side_effect = ClientError(
            {"Error": {"Code": "NoSuchBucket", "Message": "Bucket does not exist"}},
            "ListObjectsV2",
        )
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch.object(
            self.controller,
            "_S3StorageController__get_s3_client",
            return_value=mock_client,
        ):
            with pytest.raises(RemoteStorageBucketNotFoundError) as exc_info:
                await self.controller._S3StorageController__download_all_experiments_metas_via_boto3(  # noqa: E501
                    ["1"]
                )

        assert "test-bucket" in str(exc_info.value)


class TestValidateExperimentInS3:
    """Tests for validate_experiment_in_s3 method"""

    def setup_method(self):
        self.controller = S3StorageController("test-bucket")
        self.prefix = S3StorageController.make_s3_output_prefix("ws1", "uid1")

    def _make_s3_client(self, responses):
        """Create mock S3 client returning given responses."""
        mock_client = AsyncMock()
        if isinstance(responses, list):
            mock_client.list_objects_v2.side_effect = responses
        else:
            mock_client.list_objects_v2.return_value = responses
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        return mock_client

    def _make_obj(self, filename, size=100):
        """Create an S3 object dict."""
        return {"Key": f"{self.prefix}{filename}", "Size": size}

    @pytest.mark.asyncio
    async def test_valid_experiment(self):
        """Both required files present -> valid"""
        resp = {
            "KeyCount": 2,
            "Contents": [
                self._make_obj("experiment.yaml"),
                self._make_obj("workflow.yaml"),
            ],
        }
        mock_client = self._make_s3_client(resp)

        with patch.object(
            self.controller,
            "_S3StorageController__get_s3_client",
            return_value=mock_client,
        ):
            result = await self.controller.validate_experiment_in_s3("ws1", "uid1")

        assert result["valid"] is True
        assert result["error"] is None
        assert result["has_thumbnails"] is False

    @pytest.mark.asyncio
    async def test_valid_with_thumbnails(self):
        """Detects thumbnail files"""
        resp = {
            "KeyCount": 3,
            "Contents": [
                self._make_obj("experiment.yaml"),
                self._make_obj("workflow.yaml"),
                self._make_obj("thumbnails/input_thumb.png"),
            ],
        }
        mock_client = self._make_s3_client(resp)

        with patch.object(
            self.controller,
            "_S3StorageController__get_s3_client",
            return_value=mock_client,
        ):
            result = await self.controller.validate_experiment_in_s3("ws1", "uid1")

        assert result["valid"] is True
        assert result["has_thumbnails"] is True

    @pytest.mark.asyncio
    async def test_empty_prefix(self):
        """No objects in S3 -> invalid"""
        resp = {"KeyCount": 0}
        mock_client = self._make_s3_client(resp)

        with patch.object(
            self.controller,
            "_S3StorageController__get_s3_client",
            return_value=mock_client,
        ):
            result = await self.controller.validate_experiment_in_s3("ws1", "uid1")

        assert result["valid"] is False
        assert "No objects found" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_workflow_yaml(self):
        """Only experiment.yaml present -> invalid"""
        resp = {
            "KeyCount": 1,
            "Contents": [
                self._make_obj("experiment.yaml"),
            ],
        }
        mock_client = self._make_s3_client(resp)

        with patch.object(
            self.controller,
            "_S3StorageController__get_s3_client",
            return_value=mock_client,
        ):
            result = await self.controller.validate_experiment_in_s3("ws1", "uid1")

        assert result["valid"] is False
        assert "workflow.yaml" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_experiment_yaml(self):
        """Only workflow.yaml present -> invalid"""
        resp = {
            "KeyCount": 1,
            "Contents": [
                self._make_obj("workflow.yaml"),
            ],
        }
        mock_client = self._make_s3_client(resp)

        with patch.object(
            self.controller,
            "_S3StorageController__get_s3_client",
            return_value=mock_client,
        ):
            result = await self.controller.validate_experiment_in_s3("ws1", "uid1")

        assert result["valid"] is False
        assert "experiment.yaml" in result["error"]

    @pytest.mark.asyncio
    async def test_s3_exception(self):
        """S3 error -> invalid with error message"""
        mock_client = AsyncMock()
        mock_client.list_objects_v2.side_effect = RuntimeError("S3 unavailable")
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch.object(
            self.controller,
            "_S3StorageController__get_s3_client",
            return_value=mock_client,
        ):
            result = await self.controller.validate_experiment_in_s3("ws1", "uid1")

        assert result["valid"] is False
        assert "S3 validation error" in result["error"]

    @pytest.mark.asyncio
    async def test_pagination(self):
        """Handles paginated responses"""
        page1 = {
            "KeyCount": 1,
            "IsTruncated": True,
            "NextContinuationToken": "token-abc",
            "Contents": [
                self._make_obj("experiment.yaml"),
            ],
        }
        page2 = {
            "KeyCount": 1,
            "IsTruncated": False,
            "Contents": [
                self._make_obj("workflow.yaml"),
            ],
        }
        mock_client = self._make_s3_client([page1, page2])

        with patch.object(
            self.controller,
            "_S3StorageController__get_s3_client",
            return_value=mock_client,
        ):
            result = await self.controller.validate_experiment_in_s3("ws1", "uid1")

        assert result["valid"] is True
        # Second call should include ContinuationToken
        second_call = mock_client.list_objects_v2.call_args_list[1]
        assert second_call[1]["ContinuationToken"] == "token-abc"

    @pytest.mark.asyncio
    async def test_max_objects_limit(self):
        """Stops listing after VALIDATION_MAX_OBJECTS=500"""
        # Single page claiming 500 objects (hits the limit)
        contents = [self._make_obj(f"file_{i}.dat") for i in range(500)]
        resp = {
            "KeyCount": 500,
            "IsTruncated": True,
            "NextContinuationToken": "more",
            "Contents": contents,
        }
        mock_client = self._make_s3_client(resp)

        with patch.object(
            self.controller,
            "_S3StorageController__get_s3_client",
            return_value=mock_client,
        ):
            result = await self.controller.validate_experiment_in_s3("ws1", "uid1")

        # Should stop after one page (500 >= VALIDATION_MAX_OBJECTS)
        assert mock_client.list_objects_v2.call_count == 1
        # No required files in dummy data
        assert result["valid"] is False

    @pytest.mark.asyncio
    async def test_none_response(self):
        """Handles None response from list_objects_v2"""
        mock_client = self._make_s3_client(None)

        with patch.object(
            self.controller,
            "_S3StorageController__get_s3_client",
            return_value=mock_client,
        ):
            result = await self.controller.validate_experiment_in_s3("ws1", "uid1")

        assert result["valid"] is False
        assert "No objects found" in result["error"]

    @pytest.mark.asyncio
    async def test_has_extra_files_still_valid(self):
        """Extra files beyond required don't affect validity"""
        resp = {
            "KeyCount": 4,
            "Contents": [
                self._make_obj("experiment.yaml"),
                self._make_obj("workflow.yaml"),
                self._make_obj("cell_roi.json"),
                self._make_obj("data.pkl"),
            ],
        }
        mock_client = self._make_s3_client(resp)

        with patch.object(
            self.controller,
            "_S3StorageController__get_s3_client",
            return_value=mock_client,
        ):
            result = await self.controller.validate_experiment_in_s3("ws1", "uid1")

        assert result["valid"] is True

    @pytest.mark.asyncio
    async def test_zero_byte_required_file_invalid(self):
        """Zero-byte required file treated as missing"""
        resp = {
            "KeyCount": 2,
            "Contents": [
                self._make_obj("experiment.yaml", size=0),
                self._make_obj("workflow.yaml", size=100),
            ],
        }
        mock_client = self._make_s3_client(resp)

        with patch.object(
            self.controller,
            "_S3StorageController__get_s3_client",
            return_value=mock_client,
        ):
            result = await self.controller.validate_experiment_in_s3("ws1", "uid1")

        assert result["valid"] is False
        assert "experiment.yaml" in result["error"]

    @pytest.mark.asyncio
    async def test_both_zero_byte_files_invalid(self):
        """Both required files at 0 bytes -> invalid"""
        resp = {
            "KeyCount": 2,
            "Contents": [
                self._make_obj("experiment.yaml", size=0),
                self._make_obj("workflow.yaml", size=0),
            ],
        }
        mock_client = self._make_s3_client(resp)

        with patch.object(
            self.controller,
            "_S3StorageController__get_s3_client",
            return_value=mock_client,
        ):
            result = await self.controller.validate_experiment_in_s3("ws1", "uid1")

        assert result["valid"] is False
        # Both 0-byte -> found_files empty -> "No objects found"
        assert result["error"] is not None


class TestDownloadS3AtomicRename:
    """_download_s3_with_update_check writes to a tmp path then atomic-renames."""

    def setup_method(self):
        self.controller = S3StorageController("test-bucket")

    @pytest.mark.asyncio
    async def test_writes_via_tmp_then_replaces(self, tmp_path):
        """Successful download lands at the final path; no tmp left behind."""
        final_path = str(tmp_path / "sub" / "data.bin")
        payload = b"hello-s3"

        async def fake_download(bucket, key, dest):
            # Simulate s3transfer writing the object body to the given dest.
            with open(dest, "wb") as f:
                f.write(payload)

        mock_client = MagicMock()
        mock_client.download_file = AsyncMock(side_effect=fake_download)

        result = await self.controller._download_s3_with_update_check(
            mock_client, "s3/key/data.bin", final_path, file_size=len(payload)
        )

        assert result is True
        # The call to download_file received a tmp path, not the final path.
        called_dest = mock_client.download_file.call_args.args[2]
        assert called_dest != final_path
        assert called_dest.startswith(final_path + ".tmp.")
        # Final file is in place with the full payload.
        assert open(final_path, "rb").read() == payload
        # Tmp file was renamed away, not left behind.
        assert not os.path.exists(called_dest)

    @pytest.mark.asyncio
    async def test_download_failure_cleans_up_tmp_and_no_final(self, tmp_path):
        """If S3 raises mid-download, tmp is removed and final path stays absent."""
        final_path = str(tmp_path / "data.bin")
        observed_tmp = {}

        async def failing_download(bucket, key, dest):
            # Simulate partial write before the transfer raises.
            with open(dest, "wb") as f:
                f.write(b"partial")
            observed_tmp["path"] = dest
            raise RuntimeError("s3 boom")

        mock_client = MagicMock()
        mock_client.download_file = AsyncMock(side_effect=failing_download)

        with pytest.raises(RuntimeError, match="s3 boom"):
            await self.controller._download_s3_with_update_check(
                mock_client, "s3/key/data.bin", final_path, file_size=7
            )

        assert not os.path.exists(final_path), "no partial file at the final path"
        assert not os.path.exists(observed_tmp["path"]), "tmp must be cleaned up"

    @pytest.mark.asyncio
    async def test_skip_when_local_size_matches(self, tmp_path):
        """Existing local file with matching size short-circuits — no S3 call."""
        final_path = str(tmp_path / "data.bin")
        with open(final_path, "wb") as f:
            f.write(b"xxxxx")

        mock_client = MagicMock()
        mock_client.download_file = AsyncMock()

        result = await self.controller._download_s3_with_update_check(
            mock_client, "s3/key/data.bin", final_path, file_size=5
        )

        assert result is False
        mock_client.download_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_overwrites_when_local_size_mismatches(self, tmp_path):
        """Existing file with wrong size is overwritten via tmp+rename."""
        final_path = str(tmp_path / "data.bin")
        with open(final_path, "wb") as f:
            f.write(b"stale")

        new_payload = b"fresh-content"

        async def fake_download(bucket, key, dest):
            with open(dest, "wb") as f:
                f.write(new_payload)

        mock_client = MagicMock()
        mock_client.download_file = AsyncMock(side_effect=fake_download)

        result = await self.controller._download_s3_with_update_check(
            mock_client, "s3/key/data.bin", final_path, file_size=len(new_payload)
        )

        assert result is True
        assert open(final_path, "rb").read() == new_payload


class TestS3DownloadInputDataLocking:
    """download_input_data takes the per-(workspace, file) InputFileLock so
    concurrent callers across paths (ensure_input_file_synced,
    download_experiment, download_thumbnail_source) are serialized."""

    @pytest.fixture(autouse=True)
    def fast_lock_poll(self, monkeypatch):
        from studio.app.common.core.storage.remote_storage_controller import (
            InputFileLock,
        )

        monkeypatch.setattr(InputFileLock, "LOCK_WAIT_POLL_INTERVAL", 0.01)

    @pytest.fixture
    def input_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "studio.app.common.core.storage."
            "remote_storage_controller.DIRPATH.INPUT_DIR",
            str(tmp_path),
        )
        return str(tmp_path)

    @pytest.mark.asyncio
    async def test_blocks_while_lock_held(self, input_dir):
        """If another caller holds the lock for (ws, fn), download_input_data
        cannot make progress until it is released."""
        from studio.app.common.core.storage.remote_storage_controller import (
            InputFileLock,
        )

        controller = S3StorageController("test-bucket")

        # Patch __get_s3_client to a sentinel that would raise if reached —
        # the lock should block before we ever touch S3.
        unreachable_client = MagicMock(
            side_effect=AssertionError("S3 client must not be reached")
        )
        with patch.object(
            controller,
            "_S3StorageController__get_s3_client",
            new=unreachable_client,
        ):
            async with InputFileLock.acquire("ws1", "data.tif"):
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        controller.download_input_data("ws1", "data.tif"),
                        timeout=0.3,
                    )

        # After releasing, the lock file should exist (acquire creates it,
        # release does not delete it).
        assert os.path.exists(os.path.join(input_dir, "ws1", ".locks", "data.tif.lock"))

    @pytest.mark.asyncio
    async def test_releases_lock_on_error(self, input_dir):
        """If the body raises, the lock must be released so the next caller
        is not parked."""
        from studio.app.common.core.storage.remote_storage_controller import (
            InputFileLock,
        )

        controller = S3StorageController("test-bucket")

        class BoomClient:
            def __call__(self):
                return self

            async def __aenter__(self):
                raise RuntimeError("s3 boom")

            async def __aexit__(self, *exc):
                return False

        with patch.object(
            controller,
            "_S3StorageController__get_s3_client",
            new=BoomClient(),
        ):
            with pytest.raises(RuntimeError, match="s3 boom"):
                await controller.download_input_data("ws1", "data.tif")

        # A fresh acquire should not hang.
        await asyncio.wait_for(
            _acquire_then_release(InputFileLock, "ws1", "data.tif"),
            timeout=1.0,
        )


async def _acquire_then_release(lock_cls, ws: str, fn: str) -> None:
    async with lock_cls.acquire(ws, fn):
        pass
