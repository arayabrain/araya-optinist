from studio.app.const import ESSENTIAL_SYNC_PATTERNS, LARGE_FILE_PATTERNS


class FileSyncFilter:
    """Filter files for selective syncing."""

    def should_sync_file(
        self, file_path: str, sync_mode: str = "all"
    ) -> tuple[bool, str]:
        """
        Determine if a file should be synced based on sync mode.

        Args:
            file_path: Path to the file
            sync_mode: 'all' (sync everything) or 'essential_only' (skip large files)

        Returns:
            (should_sync, reason)
        """
        if sync_mode == "all":
            return (True, "sync_mode=all")

        file_lower = file_path.lower()

        # Check if it's an essential file
        if any(file_lower.endswith(pattern) for pattern in ESSENTIAL_SYNC_PATTERNS):
            return (True, "essential file")

        # Check if it's a large file to skip
        if any(file_lower.endswith(pattern) for pattern in LARGE_FILE_PATTERNS):
            return (False, "large file - skipped")

        # Default: sync unknown file types for safety
        return (True, "unknown type - synced for safety")
