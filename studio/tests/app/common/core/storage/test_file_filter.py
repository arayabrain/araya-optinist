"""
Unit tests for FileSyncFilter class.

Tests file filtering logic for selective S3 sync operations.
"""

from studio.app.common.core.storage.file_filter import FileSyncFilter
from studio.app.common.core.storage.remote_storage_controller import (
    RemoteExperimentSyncMode,
)


class TestFileSyncFilterAllMode:
    """Tests for sync_mode=ALL"""

    def setup_method(self):
        self.filter = FileSyncFilter()

    def test_all_mode_syncs_everything(self):
        """All mode syncs all files regardless of type"""
        test_files = [
            "experiment.yaml",
            "workflow.json",
            "image.tiff",
            "data.pkl",
            "random_file.xyz",
        ]

        for file_path in test_files:
            should_sync, reason = self.filter.should_sync_file(
                file_path, RemoteExperimentSyncMode.ALL
            )
            assert should_sync is True
            assert reason == "sync_mode=all"


class TestFileSyncFilterThumbnailsOnlyMode:
    """Tests for sync_mode=THUMBNAILS_ONLY"""

    def setup_method(self):
        self.filter = FileSyncFilter()

    def test_thumbnails_only_syncs_thumb_png(self):
        """Thumbnails only mode syncs *_thumb.png files"""
        thumb_files = [
            "input_thumb.png",
            "roi_thumb.png",
            "thumbnails/input_thumb.png",
        ]

        for file_path in thumb_files:
            should_sync, reason = self.filter.should_sync_file(
                file_path, RemoteExperimentSyncMode.THUMBNAILS_ONLY
            )
            assert should_sync is True, f"Expected {file_path} to sync"
            assert reason == "thumbnail file"

    def test_thumbnails_only_skips_non_thumbs(self):
        """Thumbnails only mode skips non-thumbnail files"""
        non_thumb_files = [
            "experiment.yaml",
            "workflow.json",
            "image.tiff",
            "data.pkl",
            "regular.png",  # Not a _thumb.png
            "screenshot.png",
        ]

        for file_path in non_thumb_files:
            should_sync, reason = self.filter.should_sync_file(
                file_path, RemoteExperimentSyncMode.THUMBNAILS_ONLY
            )
            assert should_sync is False, f"Expected {file_path} to be skipped"
            assert reason == "not needed for thumbnails"


class TestFileSyncFilterVisualizationMode:
    """Tests for sync_mode=VISUALIZATION"""

    def setup_method(self):
        self.filter = FileSyncFilter()

    def test_visualization_syncs_json_files(self):
        """Visualization mode syncs JSON files"""
        json_files = [
            "cell_roi.json",
            "timeseries.json",
            "output/func1/data.json",
        ]

        for file_path in json_files:
            should_sync, reason = self.filter.should_sync_file(
                file_path, RemoteExperimentSyncMode.VISUALIZATION
            )
            assert should_sync is True, f"Expected {file_path} to sync"
            assert reason == "visualization file"

    def test_visualization_syncs_tiff_files(self):
        """Visualization mode syncs TIFF files"""
        tiff_files = [
            "image.tif",
            "image.tiff",
            "IMAGE.TIF",  # Test case insensitivity
            "IMAGE.TIFF",
        ]

        for file_path in tiff_files:
            should_sync, reason = self.filter.should_sync_file(
                file_path, RemoteExperimentSyncMode.VISUALIZATION
            )
            assert should_sync is True, f"Expected {file_path} to sync"
            assert reason == "visualization file"

    def test_visualization_syncs_yaml_files(self):
        """Visualization mode syncs YAML files for snakemake config"""
        yaml_files = [
            "experiment.yaml",
            "workflow.yaml",
            "snakemake_config.yaml",
        ]

        for file_path in yaml_files:
            should_sync, reason = self.filter.should_sync_file(
                file_path, RemoteExperimentSyncMode.VISUALIZATION
            )
            assert should_sync is True, f"Expected {file_path} to sync"
            assert reason == "visualization file"

    def test_visualization_skips_large_files(self):
        """Visualization mode skips large binary files"""
        large_files = [
            "data.pkl",
            "model.h5",
            "output.npy",
            "data.mat",
        ]

        for file_path in large_files:
            should_sync, reason = self.filter.should_sync_file(
                file_path, RemoteExperimentSyncMode.VISUALIZATION
            )
            assert should_sync is False, f"Expected {file_path} to be skipped"
            assert reason == "not needed for visualization"


class TestFileSyncFilterEssentialOnlyMode:
    """Tests for sync_mode=ESSENTIAL_ONLY"""

    def setup_method(self):
        self.filter = FileSyncFilter()

    def test_essential_syncs_yaml_files(self):
        """Essential mode syncs YAML files"""
        yaml_files = [
            "experiment.yaml",
            "workflow.yaml",
            "config.yml",
        ]

        for file_path in yaml_files:
            should_sync, reason = self.filter.should_sync_file(
                file_path, RemoteExperimentSyncMode.ESSENTIAL_ONLY
            )
            assert should_sync is True, f"Expected {file_path} to sync"
            assert reason == "essential file"

    def test_essential_syncs_json_files(self):
        """Essential mode syncs JSON files"""
        json_files = [
            "cell_roi.json",
            "metadata.json",
            "output/func1/data.json",
        ]

        for file_path in json_files:
            should_sync, reason = self.filter.should_sync_file(
                file_path, RemoteExperimentSyncMode.ESSENTIAL_ONLY
            )
            assert should_sync is True, f"Expected {file_path} to sync"
            assert reason == "essential file"

    def test_essential_skips_large_files(self):
        """Essential mode skips known large file types"""
        # These are the actual file extensions in LARGE_FILE_PATTERNS
        large_files = [
            "data.pkl",
            "image.tif",
            "image.tiff",
            "data.hdf5",
            "data.h5",
            "data.mat",
            "data.nd2",
        ]

        for file_path in large_files:
            should_sync, reason = self.filter.should_sync_file(
                file_path, RemoteExperimentSyncMode.ESSENTIAL_ONLY
            )
            assert should_sync is False, f"Expected {file_path} to be skipped"
            assert reason == "large file - skipped"

    def test_essential_syncs_unknown_files_for_safety(self):
        """Essential mode syncs unknown file types for safety"""
        unknown_files = [
            "data.xyz",
            "random.unknown",
            "file.custom",
        ]

        for file_path in unknown_files:
            should_sync, reason = self.filter.should_sync_file(
                file_path, RemoteExperimentSyncMode.ESSENTIAL_ONLY
            )
            assert should_sync is True, f"Expected {file_path} to sync"
            assert reason == "unknown type - synced for safety"


class TestFileSyncFilterCaseInsensitivity:
    """Tests for case-insensitive file matching"""

    def setup_method(self):
        self.filter = FileSyncFilter()

    def test_case_insensitive_thumbnails(self):
        """Thumbnail pattern matching is case-insensitive"""
        files = [
            "INPUT_THUMB.PNG",
            "Input_Thumb.Png",
            "input_thumb.png",
        ]

        for file_path in files:
            should_sync, reason = self.filter.should_sync_file(
                file_path, RemoteExperimentSyncMode.THUMBNAILS_ONLY
            )
            assert should_sync is True, f"Expected {file_path} to match"

    def test_case_insensitive_extensions(self):
        """Extension matching is case-insensitive"""
        files_by_mode = {
            RemoteExperimentSyncMode.VISUALIZATION: [
                ("file.JSON", True),
                ("file.Json", True),
                ("file.TIFF", True),
                ("file.Yaml", True),
            ],
            RemoteExperimentSyncMode.ESSENTIAL_ONLY: [
                ("config.YAML", True),
                ("data.JSON", True),
                ("config.YML", True),
            ],
        }

        for mode, test_cases in files_by_mode.items():
            for file_path, expected in test_cases:
                should_sync, _ = self.filter.should_sync_file(file_path, mode)
                assert should_sync is expected, f"{file_path} in {mode} mode"


class TestFileSyncFilterPathHandling:
    """Tests for file path handling"""

    def setup_method(self):
        self.filter = FileSyncFilter()

    def test_handles_full_s3_paths(self):
        """Filter works with full S3-style paths"""
        s3_paths = [
            "app/studio_data/output/1/exp1/thumbnails/input_thumb.png",
            "app/studio_data/output/1/exp1/cell_roi.json",
            "app/studio_data/output/1/exp1/experiment.yaml",
        ]

        expected = [
            (RemoteExperimentSyncMode.THUMBNAILS_ONLY, True),
            (RemoteExperimentSyncMode.VISUALIZATION, True),
            (RemoteExperimentSyncMode.ESSENTIAL_ONLY, True),
        ]

        for path, (mode, should_sync_expected) in zip(s3_paths, expected):
            should_sync, _ = self.filter.should_sync_file(path, mode)
            assert should_sync is should_sync_expected, f"{path} in {mode}"

    def test_handles_nested_paths(self):
        """Filter works with deeply nested paths"""
        nested_paths = [
            "output/workspace1/exp1/function1/subfolder/data.json",
            "output/workspace1/exp1/function1/result.tiff",
        ]

        for path in nested_paths:
            should_sync, reason = self.filter.should_sync_file(
                path, RemoteExperimentSyncMode.VISUALIZATION
            )
            assert should_sync is True
            assert reason == "visualization file"
