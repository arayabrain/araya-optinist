"""
Utilities for handling Snakemake storage paths in AWS Batch.

This module provides functionality to resolve Snakemake temporary storage paths
(e.g., from S3) to permanent local locations, ensuring files are accessible
to subsequent batch jobs even after Snakemake's cleanup.
"""
import os
import shutil
from typing import List, Union

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.mode import MODE
from studio.app.dir_path import DIRPATH

logger = AppLogger.get_logger()


def resolve_snakemake_storage_path(
    input_path: Union[str, List[str], tuple]
) -> Union[str, List[str]]:
    """
    Resolve Snakemake storage paths to permanent locations.

    When running in AWS Batch with S3 storage, Snakemake downloads files to
    .snakemake/storage/s3/... and cleans them up after each job. This method
    copies those files to permanent locations so downstream jobs can access
    them.

    Args:
        input_path: Single path or list of paths (may be in Snakemake storage)

    Returns:
        Permanent path(s) - copied from Snakemake storage or original path

    Raises:
        ValueError: If workspace_id cannot be extracted from storage path
    """
    # Early exit if not running in batch mode
    # No need to copy files from Snakemake storage in non-batch contexts
    if not MODE.IN_SNAKEMAKE_BATCH:
        return input_path

    # Handle list/tuple by recursing on each element
    if isinstance(input_path, (list, tuple)):
        resolved = [resolve_snakemake_storage_path(p) for p in input_path]
        return resolved if isinstance(input_path, list) else tuple(resolved)

    # Now handle single path case
    if not isinstance(input_path, str) or ".snakemake/storage" not in input_path:
        return input_path

    # Extract filename and workspace_id
    filename = os.path.basename(input_path)
    path_parts = input_path.split("/")

    # Find the workspace_id (should be after 'input' directory)
    try:
        input_idx = path_parts.index("input")
        workspace_id = path_parts[input_idx + 1]
    except (ValueError, IndexError):
        raise ValueError(
            f"Cannot extract workspace_id from Snakemake storage path: "
            f"{input_path}. Expected format: .../input/WORKSPACE_ID/filename"
        )

    # Create permanent directory and copy file
    permanent_dir = os.path.join(DIRPATH.INPUT_DIR, workspace_id)
    os.makedirs(permanent_dir, exist_ok=True)
    permanent_path = os.path.join(permanent_dir, filename)

    # Copy file if it doesn't already exist at permanent location
    if not os.path.exists(permanent_path):
        logger.info(
            "Copying file from Snakemake storage to permanent location: "
            f"{input_path} -> {permanent_path}"
        )
        shutil.copy2(input_path, permanent_path)
    else:
        logger.info(
            "File already exists at permanent location, skipping copy: "
            f"{permanent_path}"
        )

    return permanent_path
