"""
Unit tests for SyncStateTracker.

Tests filesystem tier detection, sync probe, stale record invalidation,
reconciliation, and staleness spot-check.
"""

from unittest.mock import MagicMock, patch

import pytest

from studio.app.common.core.storage.sync_state_tracker import (
    SyncProbeResult,
    SyncStateTracker,
)
from studio.app.common.core.storage.sync_tier import SyncTier


class TestSyncProbeResult:
    """SyncProbeResult dataclass basics."""

    def test_fields(self):
        result = SyncProbeResult(
            tier=SyncTier.METADATA_ONLY,
            file_status=None,
        )
        assert result.tier == SyncTier.METADATA_ONLY
        assert result.file_status is None


class TestDetectTierFromFilesystem:
    """_detect_tier_from_filesystem inspects files on disk."""

    def _make_experiment_dir(self, tmp_path, files=None, subdirs=None):
        """Helper to create an experiment dir with specific files."""
        exp_dir = tmp_path / "output" / "ws1" / "uid1"
        exp_dir.mkdir(parents=True, exist_ok=True)
        for f in files or []:
            filepath = exp_dir / f
            filepath.parent.mkdir(parents=True, exist_ok=True)
            filepath.write_text("test")
        for d in subdirs or []:
            (exp_dir / d).mkdir(parents=True, exist_ok=True)
        return str(exp_dir)

    @patch(
        "studio.app.common.core.storage.sync_state_tracker.DIRPATH",
    )
    def test_no_directory_returns_none(self, mock_dirpath, tmp_path):
        mock_dirpath.OUTPUT_DIR = str(tmp_path / "output")
        tier = SyncStateTracker._detect_tier_from_filesystem("ws1", "uid1")
        assert tier == SyncTier.NONE

    @patch(
        "studio.app.common.core.storage.sync_state_tracker.DIRPATH",
    )
    def test_empty_directory_returns_none(self, mock_dirpath, tmp_path):
        mock_dirpath.OUTPUT_DIR = str(tmp_path / "output")
        exp_dir = tmp_path / "output" / "ws1" / "uid1"
        exp_dir.mkdir(parents=True)
        tier = SyncStateTracker._detect_tier_from_filesystem("ws1", "uid1")
        assert tier == SyncTier.NONE

    @patch(
        "studio.app.common.core.storage.sync_state_tracker.DIRPATH",
    )
    def test_only_experiment_yaml_returns_none(self, mock_dirpath, tmp_path):
        """Both metadata files required for METADATA_ONLY."""
        mock_dirpath.OUTPUT_DIR = str(tmp_path / "output")
        self._make_experiment_dir(tmp_path, files=["experiment.yaml"])
        tier = SyncStateTracker._detect_tier_from_filesystem("ws1", "uid1")
        assert tier == SyncTier.NONE

    @patch(
        "studio.app.common.core.storage.sync_state_tracker.DIRPATH",
    )
    def test_both_yamls_returns_metadata_only(self, mock_dirpath, tmp_path):
        mock_dirpath.OUTPUT_DIR = str(tmp_path / "output")
        self._make_experiment_dir(tmp_path, files=["experiment.yaml", "workflow.yaml"])
        tier = SyncStateTracker._detect_tier_from_filesystem("ws1", "uid1")
        assert tier == SyncTier.METADATA_ONLY

    @patch(
        "studio.app.common.core.storage.sync_state_tracker.DIRPATH",
    )
    def test_with_thumbnails_returns_thumbnails_only(self, mock_dirpath, tmp_path):
        mock_dirpath.OUTPUT_DIR = str(tmp_path / "output")
        self._make_experiment_dir(
            tmp_path,
            files=["experiment.yaml", "workflow.yaml", "thumb.png"],
        )
        tier = SyncStateTracker._detect_tier_from_filesystem("ws1", "uid1")
        assert tier == SyncTier.THUMBNAILS_ONLY

    @patch(
        "studio.app.common.core.storage.sync_state_tracker.DIRPATH",
    )
    def test_with_snakemake_config_and_json_returns_essential(
        self, mock_dirpath, tmp_path
    ):
        mock_dirpath.OUTPUT_DIR = str(tmp_path / "output")
        mock_dirpath.DATA_DIR = str(tmp_path / "data")
        self._make_experiment_dir(
            tmp_path,
            files=[
                "experiment.yaml",
                "workflow.yaml",
                "thumb.png",
                "snakemake_config.yaml",
                "func1/output.json",
            ],
        )
        # Mock SmkUtils to say no input files needed
        with patch("studio.app.common.core.snakemake.smk_utils.SmkUtils") as mock_smk:
            mock_smk.get_datatypes_inputs.return_value = []
            tier = SyncStateTracker._detect_tier_from_filesystem("ws1", "uid1")

        # No input files required → jumps to VISUALIZATION check
        assert tier >= SyncTier.ESSENTIAL_ONLY

    @patch(
        "studio.app.common.core.storage.sync_state_tracker.DIRPATH",
    )
    def test_missing_snakemake_config_stays_at_thumbnails(self, mock_dirpath, tmp_path):
        mock_dirpath.OUTPUT_DIR = str(tmp_path / "output")
        self._make_experiment_dir(
            tmp_path,
            files=[
                "experiment.yaml",
                "workflow.yaml",
                "thumb.png",
                # no snakemake_config.yaml
            ],
        )
        tier = SyncStateTracker._detect_tier_from_filesystem("ws1", "uid1")
        assert tier == SyncTier.THUMBNAILS_ONLY

    @patch(
        "studio.app.common.core.storage.sync_state_tracker.DIRPATH",
    )
    def test_no_json_outputs_stays_at_thumbnails(self, mock_dirpath, tmp_path):
        """snakemake_config present but no JSON in subdirs = THUMBNAILS_ONLY."""
        mock_dirpath.OUTPUT_DIR = str(tmp_path / "output")
        self._make_experiment_dir(
            tmp_path,
            files=[
                "experiment.yaml",
                "workflow.yaml",
                "thumb.png",
                "snakemake_config.yaml",
            ],
        )
        tier = SyncStateTracker._detect_tier_from_filesystem("ws1", "uid1")
        assert tier == SyncTier.THUMBNAILS_ONLY

    @patch(
        "studio.app.common.core.storage.sync_state_tracker.DIRPATH",
    )
    def test_missing_inputs_returns_essential(self, mock_dirpath, tmp_path):
        """Has JSON outputs but input files are missing."""
        mock_dirpath.OUTPUT_DIR = str(tmp_path / "output")
        mock_dirpath.DATA_DIR = str(tmp_path / "data")
        self._make_experiment_dir(
            tmp_path,
            files=[
                "experiment.yaml",
                "workflow.yaml",
                "thumb.png",
                "snakemake_config.yaml",
                "func1/output.json",
            ],
        )
        with patch("studio.app.common.core.snakemake.smk_utils.SmkUtils") as mock_smk:
            mock_smk.get_datatypes_inputs.return_value = ["data.tiff"]
            tier = SyncStateTracker._detect_tier_from_filesystem("ws1", "uid1")

        assert tier == SyncTier.ESSENTIAL_ONLY

    @patch(
        "studio.app.common.core.storage.sync_state_tracker.DIRPATH",
    )
    def test_all_inputs_present_returns_visualization(self, mock_dirpath, tmp_path):
        mock_dirpath.OUTPUT_DIR = str(tmp_path / "output")
        mock_dirpath.DATA_DIR = str(tmp_path / "data")

        self._make_experiment_dir(
            tmp_path,
            files=[
                "experiment.yaml",
                "workflow.yaml",
                "thumb.png",
                "snakemake_config.yaml",
                "func1/output.json",
            ],
        )

        # Create input file
        input_dir = tmp_path / "data" / "input" / "ws1"
        input_dir.mkdir(parents=True)
        (input_dir / "data.tiff").write_text("tiff data")

        with patch("studio.app.common.core.snakemake.smk_utils.SmkUtils") as mock_smk:
            mock_smk.get_datatypes_inputs.return_value = ["data.tiff"]
            with patch(
                "studio.app.common.core.storage.sync_state_tracker."
                "RemoteSyncStatusFileUtil"
            ) as mock_status:
                mock_status.check_sync_status_success.return_value = False
                tier = SyncStateTracker._detect_tier_from_filesystem("ws1", "uid1")

        assert tier == SyncTier.VISUALIZATION

    @patch(
        "studio.app.common.core.storage.sync_state_tracker.DIRPATH",
    )
    def test_full_sync_with_success_status_returns_all(self, mock_dirpath, tmp_path):
        mock_dirpath.OUTPUT_DIR = str(tmp_path / "output")
        mock_dirpath.DATA_DIR = str(tmp_path / "data")

        self._make_experiment_dir(
            tmp_path,
            files=[
                "experiment.yaml",
                "workflow.yaml",
                "thumb.png",
                "snakemake_config.yaml",
                "func1/output.json",
            ],
        )

        input_dir = tmp_path / "data" / "input" / "ws1"
        input_dir.mkdir(parents=True)
        (input_dir / "data.tiff").write_text("tiff data")

        with patch("studio.app.common.core.snakemake.smk_utils.SmkUtils") as mock_smk:
            mock_smk.get_datatypes_inputs.return_value = ["data.tiff"]
            with patch(
                "studio.app.common.core.storage.sync_state_tracker."
                "RemoteSyncStatusFileUtil"
            ) as mock_status:
                mock_status.check_sync_status_success.return_value = True
                tier = SyncStateTracker._detect_tier_from_filesystem("ws1", "uid1")

        assert tier == SyncTier.ALL

    @patch(
        "studio.app.common.core.storage.sync_state_tracker.DIRPATH",
    )
    def test_smk_utils_exception_returns_essential(self, mock_dirpath, tmp_path):
        """If SmkUtils raises, fall back to ESSENTIAL_ONLY."""
        mock_dirpath.OUTPUT_DIR = str(tmp_path / "output")
        self._make_experiment_dir(
            tmp_path,
            files=[
                "experiment.yaml",
                "workflow.yaml",
                "thumb.png",
                "snakemake_config.yaml",
                "func1/output.json",
            ],
        )
        with patch("studio.app.common.core.snakemake.smk_utils.SmkUtils") as mock_smk:
            mock_smk.get_datatypes_inputs.side_effect = Exception("config error")
            tier = SyncStateTracker._detect_tier_from_filesystem("ws1", "uid1")

        assert tier == SyncTier.ESSENTIAL_ONLY


class TestGetSyncProbe:
    """get_sync_probe combines filesystem detection with file status."""

    @patch("studio.app.common.core.storage.sync_state_tracker.RemoteSyncStatusFileUtil")
    @patch.object(SyncStateTracker, "_detect_tier_from_filesystem")
    def test_returns_probe_result(self, mock_detect, mock_status_util):
        mock_detect.return_value = SyncTier.METADATA_ONLY
        mock_status_util.check_sync_status_file.return_value = None

        probe = SyncStateTracker.get_sync_probe("ws1", "uid1")
        assert isinstance(probe, SyncProbeResult)
        assert probe.tier == SyncTier.METADATA_ONLY
        assert probe.file_status is None

    @patch("studio.app.common.core.storage.sync_state_tracker.RemoteSyncStatusFileUtil")
    @patch.object(SyncStateTracker, "_detect_tier_from_filesystem")
    def test_passes_through_file_status(self, mock_detect, mock_status_util):
        mock_detect.return_value = SyncTier.ALL
        mock_status_util.check_sync_status_file.return_value = "SUCCESS"

        probe = SyncStateTracker.get_sync_probe("ws1", "uid1")
        assert probe.file_status == "SUCCESS"


class TestGetSyncProbeAsync:
    """Async version offloads to thread pool."""

    @pytest.mark.asyncio
    @patch.object(SyncStateTracker, "get_sync_probe")
    async def test_calls_sync_version(self, mock_probe):
        expected = SyncProbeResult(tier=SyncTier.ALL, file_status=None)
        mock_probe.return_value = expected

        result = await SyncStateTracker.get_sync_probe_async("ws1", "uid1")
        assert result == expected
        mock_probe.assert_called_once_with("ws1", "uid1")


class TestInvalidateStaleRecords:
    """invalidate_stale_records resets DB records missing local files."""

    @patch("studio.app.common.core.storage.sync_state_tracker.DIRPATH")
    def test_invalidates_missing_directory(self, mock_dirpath, tmp_path):
        mock_dirpath.OUTPUT_DIR = str(tmp_path / "output")

        # Mock record with no local directory
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.workspace_id = "ws1"
        mock_record.uid = "uid1"
        mock_record.local_sync_status = "synced"

        mock_db = MagicMock()
        query = mock_db.query.return_value.filter.return_value
        query.order_by.return_value.limit.return_value.all.side_effect = [
            [mock_record],
            [],  # Second batch empty
        ]

        with patch("studio.app.common.db.database.session_scope") as mock_scope:
            mock_scope.return_value.__enter__.return_value = mock_db
            count = SyncStateTracker.invalidate_stale_records()

        assert count == 1
        # Bulk update() is used instead of ORM attribute assignment
        mock_db.execute.assert_called_once()
        mock_db.commit.assert_called()

    @patch("studio.app.common.core.storage.sync_state_tracker.DIRPATH")
    def test_keeps_valid_records(self, mock_dirpath, tmp_path):
        """Records with existing files should not be invalidated."""
        mock_dirpath.OUTPUT_DIR = str(tmp_path / "output")

        # Create actual files
        exp_dir = tmp_path / "output" / "ws1" / "uid1"
        exp_dir.mkdir(parents=True)
        (exp_dir / "experiment.yaml").write_text("test")
        (exp_dir / "workflow.yaml").write_text("test")

        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.workspace_id = "ws1"
        mock_record.uid = "uid1"
        mock_record.local_sync_status = "synced"

        mock_db = MagicMock()
        query = mock_db.query.return_value.filter.return_value
        query.order_by.return_value.limit.return_value.all.side_effect = [
            [mock_record],
            [],
        ]

        with patch("studio.app.common.db.database.session_scope") as mock_scope:
            mock_scope.return_value.__enter__.return_value = mock_db
            count = SyncStateTracker.invalidate_stale_records()

        assert count == 0
        assert mock_record.local_sync_status == "synced"

    @patch("studio.app.common.core.storage.sync_state_tracker.DIRPATH")
    def test_pagination_processes_multiple_batches(self, mock_dirpath, tmp_path):
        """Cursor-based pagination processes records across batches."""
        mock_dirpath.OUTPUT_DIR = str(tmp_path / "output")

        record1 = MagicMock()
        record1.id = 1
        record1.workspace_id = "ws1"
        record1.uid = "uid1"

        record2 = MagicMock()
        record2.id = 2
        record2.workspace_id = "ws1"
        record2.uid = "uid2"

        mock_db = MagicMock()
        query = mock_db.query.return_value.filter.return_value
        query.order_by.return_value.limit.return_value.all.side_effect = [
            [record1],
            [record2],
            [],
        ]

        with patch("studio.app.common.db.database.session_scope") as mock_scope:
            mock_scope.return_value.__enter__.return_value = mock_db
            count = SyncStateTracker.invalidate_stale_records()

        # Both records missing → both invalidated
        assert count == 2


class TestReconcile:
    """reconcile ensures DB and file status agree after download."""

    def test_partial_sync_is_noop(self):
        """Non-ALL tiers don't trigger reconciliation."""
        with patch(
            "studio.app.common.core.storage.sync_state_tracker."
            "RemoteSyncStatusFileUtil"
        ) as mock_status:
            SyncStateTracker.reconcile("ws1", "uid1", SyncTier.ESSENTIAL_ONLY, "bucket")
            mock_status.check_sync_status_success.assert_not_called()

    @patch("studio.app.common.core.storage.sync_state_tracker.RemoteSyncStatusFileUtil")
    def test_all_tier_creates_success_status(self, mock_status):
        mock_status.check_sync_status_success.return_value = False

        mock_db = MagicMock()
        mock_db.execute.return_value.rowcount = 1

        with patch("studio.app.common.db.database.session_scope") as mock_scope:
            mock_scope.return_value.__enter__.return_value = mock_db
            SyncStateTracker.reconcile("ws1", "uid1", SyncTier.ALL, "bucket")

        mock_status.create_sync_status_file_for_success.assert_called_once()
        # Bulk update() is used instead of ORM attribute assignment
        mock_db.execute.assert_called_once()
        mock_db.commit.assert_called_once()

    @patch("studio.app.common.core.storage.sync_state_tracker.RemoteSyncStatusFileUtil")
    def test_all_tier_skips_if_already_success(self, mock_status):
        """If file status already SUCCESS, don't re-create."""
        mock_status.check_sync_status_success.return_value = True

        mock_record = MagicMock()
        mock_record.local_sync_status = "synced"
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_record

        with patch("studio.app.common.db.database.session_scope") as mock_scope:
            mock_scope.return_value.__enter__.return_value = mock_db
            SyncStateTracker.reconcile("ws1", "uid1", SyncTier.ALL, "bucket")

        mock_status.create_sync_status_file_for_success.assert_not_called()

    @patch("studio.app.common.core.storage.sync_state_tracker.RemoteSyncStatusFileUtil")
    def test_reconcile_no_db_record(self, mock_status):
        """If no DB record exists, only file status is updated."""
        mock_status.check_sync_status_success.return_value = False

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        with patch("studio.app.common.db.database.session_scope") as mock_scope:
            mock_scope.return_value.__enter__.return_value = mock_db
            SyncStateTracker.reconcile("ws1", "uid1", SyncTier.ALL, "bucket")

        mock_status.create_sync_status_file_for_success.assert_called_once()

    @patch("studio.app.common.core.storage.sync_state_tracker.RemoteSyncStatusFileUtil")
    def test_reconcile_db_error_doesnt_raise(self, mock_status):
        """DB errors are caught and logged (standalone mode)."""
        mock_status.check_sync_status_success.return_value = False

        with patch("studio.app.common.db.database.session_scope") as mock_scope:
            mock_scope.return_value.__enter__.side_effect = Exception("DB unavailable")
            # Should not raise
            SyncStateTracker.reconcile("ws1", "uid1", SyncTier.ALL, "bucket")


class TestCheckSyncedStalenessSpotCheck:
    """spot-check samples random 'synced' records and verifies local files."""

    @patch("studio.app.common.core.storage.sync_state_tracker.DIRPATH")
    def test_invalidates_missing_experiment(self, mock_dirpath, tmp_path):
        mock_dirpath.OUTPUT_DIR = str(tmp_path / "output")

        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.workspace_id = "ws1"
        mock_record.uid = "uid1"
        mock_record.local_sync_status = "synced"

        mock_db = MagicMock()
        # count query
        mock_db.query.return_value.filter.return_value.scalar.return_value = 1
        # record query
        query = mock_db.query.return_value.filter.return_value
        ordered = query.order_by.return_value.offset.return_value
        ordered.limit.return_value.first.return_value = mock_record

        with patch("studio.app.common.db.database.session_scope") as mock_scope:
            mock_scope.return_value.__enter__.return_value = mock_db
            count = SyncStateTracker.check_synced_staleness_spot_check(sample_size=1)

        assert count == 1
        # Bulk update() is used instead of ORM attribute assignment
        mock_db.execute.assert_called_once()
        mock_db.commit.assert_called()

    @patch("studio.app.common.core.storage.sync_state_tracker.DIRPATH")
    def test_keeps_valid_record(self, mock_dirpath, tmp_path):
        mock_dirpath.OUTPUT_DIR = str(tmp_path / "output")

        # Create actual files (both yamls required to match invalidate_stale_records)
        exp_dir = tmp_path / "output" / "ws1" / "uid1"
        exp_dir.mkdir(parents=True)
        (exp_dir / "experiment.yaml").write_text("test")
        (exp_dir / "workflow.yaml").write_text("test")

        mock_record = MagicMock()
        mock_record.workspace_id = "ws1"
        mock_record.uid = "uid1"
        mock_record.local_sync_status = "synced"

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.scalar.return_value = 1
        query = mock_db.query.return_value.filter.return_value
        ordered = query.order_by.return_value.offset.return_value
        ordered.limit.return_value.first.return_value = mock_record

        with patch("studio.app.common.db.database.session_scope") as mock_scope:
            mock_scope.return_value.__enter__.return_value = mock_db
            count = SyncStateTracker.check_synced_staleness_spot_check(sample_size=1)

        assert count == 0
        assert mock_record.local_sync_status == "synced"

    def test_zero_count_returns_zero(self):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.scalar.return_value = 0

        with patch("studio.app.common.db.database.session_scope") as mock_scope:
            mock_scope.return_value.__enter__.return_value = mock_db
            count = SyncStateTracker.check_synced_staleness_spot_check()

        assert count == 0

    def test_db_exception_returns_zero(self):
        """DB exceptions are caught and return 0."""
        with patch("studio.app.common.db.database.session_scope") as mock_scope:
            mock_scope.return_value.__enter__.side_effect = Exception("DB error")
            count = SyncStateTracker.check_synced_staleness_spot_check()

        assert count == 0
