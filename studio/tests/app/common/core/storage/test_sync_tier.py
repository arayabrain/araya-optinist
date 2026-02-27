"""
Unit tests for SyncTier enum and DownloadResult dataclass.

Tests ordering, gap values, to_sync_mode mapping, and DownloadResult defaults.
"""

import pytest

from studio.app.common.core.storage.sync_tier import DownloadResult, SyncTier


class TestSyncTierOrdering:
    """SyncTier is an IntEnum; higher tiers subsume lower ones."""

    def test_none_is_lowest(self):
        assert SyncTier.NONE < SyncTier.METADATA_ONLY

    def test_ordering_chain(self):
        assert (
            SyncTier.NONE
            < SyncTier.METADATA_ONLY
            < SyncTier.THUMBNAILS_ONLY
            < SyncTier.ESSENTIAL_ONLY
            < SyncTier.VISUALIZATION
            < SyncTier.ALL
        )

    def test_equal_comparison(self):
        assert SyncTier.METADATA_ONLY == SyncTier.METADATA_ONLY

    def test_gte_comparison(self):
        assert SyncTier.ALL >= SyncTier.VISUALIZATION
        assert SyncTier.METADATA_ONLY >= SyncTier.METADATA_ONLY
        assert not (SyncTier.NONE >= SyncTier.METADATA_ONLY)

    def test_gap_values_allow_future_insertion(self):
        """Values use gaps of 10 for future tier insertion."""
        assert SyncTier.NONE == 0
        assert SyncTier.METADATA_ONLY == 10
        assert SyncTier.THUMBNAILS_ONLY == 20
        assert SyncTier.ESSENTIAL_ONLY == 30
        assert SyncTier.VISUALIZATION == 40
        assert SyncTier.ALL == 50


class TestSyncTierToSyncMode:
    """to_sync_mode() maps to RemoteStorageController download sync_mode."""

    def test_thumbnails_only_maps_to_sync_mode(self):
        assert SyncTier.THUMBNAILS_ONLY.to_sync_mode() == "thumbnails_only"

    def test_essential_only_maps_to_sync_mode(self):
        assert SyncTier.ESSENTIAL_ONLY.to_sync_mode() == "essential_only"

    def test_visualization_maps_to_sync_mode(self):
        assert SyncTier.VISUALIZATION.to_sync_mode() == "visualization"

    def test_all_maps_to_sync_mode(self):
        assert SyncTier.ALL.to_sync_mode() == "all"

    def test_metadata_only_raises(self):
        """METADATA_ONLY has no sync_mode -- uses download_experiment_meta()."""
        with pytest.raises(ValueError, match="no sync_mode mapping"):
            SyncTier.METADATA_ONLY.to_sync_mode()

    def test_none_raises(self):
        with pytest.raises(ValueError, match="no sync_mode mapping"):
            SyncTier.NONE.to_sync_mode()


class TestSyncTierFromInt:
    """SyncTier can be constructed from raw int (e.g. from claim file)."""

    def test_construct_from_int(self):
        assert SyncTier(10) == SyncTier.METADATA_ONLY

    def test_construct_from_zero(self):
        assert SyncTier(0) == SyncTier.NONE

    def test_invalid_int_raises(self):
        with pytest.raises(ValueError):
            SyncTier(99)


class TestDownloadResult:
    """DownloadResult dataclass defaults and field access."""

    def test_success_result(self):
        result = DownloadResult(success=True, achieved_tier=SyncTier.ALL)
        assert result.success is True
        assert result.achieved_tier == SyncTier.ALL
        assert result.was_skipped is False
        assert result.was_deduplicated is False
        assert result.error is None
        assert result.duration_ms is None

    def test_failure_result(self):
        result = DownloadResult(
            success=False,
            achieved_tier=SyncTier.NONE,
            error="Insufficient disk space",
        )
        assert result.success is False
        assert result.achieved_tier == SyncTier.NONE
        assert result.error == "Insufficient disk space"

    def test_skipped_result(self):
        result = DownloadResult(
            success=True,
            achieved_tier=SyncTier.ALL,
            was_skipped=True,
            duration_ms=5,
        )
        assert result.was_skipped is True
        assert result.duration_ms == 5

    def test_deduplicated_result(self):
        result = DownloadResult(
            success=True,
            achieved_tier=SyncTier.ESSENTIAL_ONLY,
            was_deduplicated=True,
        )
        assert result.was_deduplicated is True
