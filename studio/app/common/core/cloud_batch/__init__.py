"""
AWS Batch utilities for Snakemake workflow execution.
"""

# Only define __all__ to document the API, but don't import anything here
# to avoid circular dependency issues
__all__ = [
    "BATCH_CONFIG",
    "BatchConfig",
    "BatchUtils",
    "BatchDebug",
    "ensure_file_available_to_batch",
    "resolve_snakemake_storage_path",
    "get_batch_config_path",
    "is_running_in_batch",
    "log_batch_config",
    "debug_batch_jobs",
]
