"""
Unit tests for S3StorageController class.

Tests S3 operations including download_experiment with sync_mode filtering.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
    async def test_download_experiment_empty_s3_returns_false(self):
        """Returns False when S3 prefix has no objects"""
        mock_client = AsyncMock()
        mock_client.list_objects_v2.return_value = {"KeyCount": 0}
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch.object(
            self.controller,
            "_S3StorageController__get_s3_client",
            return_value=mock_client,
        ):
            result = await self.controller.download_experiment("1", "exp123")

        assert result is False

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
            "studio.app.common.core.storage.s3_storage_controller.DIRPATH"
        ) as mock_dirpath:
            mock_dirpath.OUTPUT_DIR = "/app/studio_data/output"

            result = await self.controller.download_experiment(
                "1", "exp123", sync_mode="all"
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
            "studio.app.common.core.storage.s3_storage_controller.DIRPATH"
        ) as mock_dirpath:
            mock_dirpath.OUTPUT_DIR = "/app/studio_data/output"

            result = await self.controller.download_experiment(
                "1", "exp123", sync_mode="essential_only"
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
            "studio.app.common.core.storage.s3_storage_controller.DIRPATH"
        ) as mock_dirpath:
            mock_dirpath.OUTPUT_DIR = "/app/studio_data/output"

            result = await self.controller.download_experiment(
                "1", "exp123", sync_mode="thumbnails_only"
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
            "studio.app.common.core.storage.s3_storage_controller.DIRPATH"
        ) as mock_dirpath:
            mock_dirpath.OUTPUT_DIR = "/app/studio_data/output"

            result = await self.controller.download_experiment(
                "1", "exp123", sync_mode="visualization"
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
            _is_no_such_bucket_error,
        )

        error = ClientError(
            {"Error": {"Code": "NoSuchBucket", "Message": "Bucket does not exist"}},
            "ListObjectsV2",
        )
        assert _is_no_such_bucket_error(error) is True

    def test_is_no_such_bucket_error_with_other_client_error(self):
        """Returns False for other ClientError codes"""
        from botocore.exceptions import ClientError

        from studio.app.common.core.storage.s3_storage_controller import (
            _is_no_such_bucket_error,
        )

        error = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Access denied"}},
            "ListObjectsV2",
        )
        assert _is_no_such_bucket_error(error) is False

    def test_is_no_such_bucket_error_with_regular_exception(self):
        """Returns False for regular exceptions"""
        from studio.app.common.core.storage.s3_storage_controller import (
            _is_no_such_bucket_error,
        )

        error = Exception("Some other error")
        assert _is_no_such_bucket_error(error) is False


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
