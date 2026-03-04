from dataclasses import dataclass
from enum import IntEnum
from typing import Optional


class SyncTier(IntEnum):
    """Hierarchy of download completeness. Higher tiers subsume lower ones.

    Gap values (10, 20, 30...) allow future insertion without renumbering.
    Matches the existing SubscriptionUserStatus(IntEnum) pattern.
    """

    NONE = 0
    METADATA_ONLY = 10  # experiment.yaml, workflow.yaml
    THUMBNAILS_ONLY = 20  # + PNG thumbnails
    ESSENTIAL_ONLY = 30  # + all yaml + json (no large binary files)
    VISUALIZATION = 40  # + input TIFF/CSV (for viewing results)
    ALL = 50  # + PKL, NWB, everything

    def to_sync_mode(self) -> str:
        """Map to RemoteStorageController.download_experiment() sync_mode.

        METADATA_ONLY has no direct sync_mode equivalent -- the coordinator
        calls download_experiment_meta() instead of download_experiment().
        """
        if self not in _TIER_TO_SYNC_MODE:
            raise ValueError(
                f"SyncTier.{self.name} has no sync_mode mapping. "
                "Use download_experiment_meta() for METADATA_ONLY."
            )
        return _TIER_TO_SYNC_MODE[self]


# Maps SyncTier to the sync_mode parameter accepted by
# RemoteStorageController.download_experiment() and S3StorageController.
_TIER_TO_SYNC_MODE = {
    SyncTier.THUMBNAILS_ONLY: "thumbnails_only",
    SyncTier.ESSENTIAL_ONLY: "essential_only",
    SyncTier.VISUALIZATION: "visualization",
    SyncTier.ALL: "all",
}


@dataclass
class DownloadResult:
    """Result of a coordinator ensure_synced() call.

    ensure_synced() never raises -- it always returns a DownloadResult.
    Callers check result.success and decide how to respond.
    """

    success: bool
    achieved_tier: SyncTier
    was_skipped: bool = False  # True if already at required tier
    was_deduplicated: bool = False  # True if another worker was already downloading
    error: Optional[str] = None
    is_lock_error: bool = False  # True if failure was due to remote storage lock
    duration_ms: Optional[int] = None
