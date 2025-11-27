"""Utilities for handling file access in AWS Batch with S3 storage."""

import os
import re
from typing import List, Union

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.mode import MODE

logger = AppLogger.get_logger()


def ensure_file_available_to_batch(
    file_path: Union[str, List[str]]
) -> Union[str, List[str]]:
    """
    Ensure that file(s) are available locally in AWS Batch execution.

    When running in AWS Batch with Snakemake's S3 storage plugin, files referenced
    inside serialized objects (pkl files) are not automatically retrieved. This
    function detects such cases and downloads the files from S3 as needed.

    Per Snakemake documentation:
    - S3 storage plugin does NOT automatically retrieve file paths embedded within
      serialized objects (pkl files)
    - Recommended pattern: Explicitly declare all S3 objects in rules, OR
      download missing files explicitly (this function implements the latter)

    Args:
        file_path: Single file path or list of file paths

    Returns:
        Same path(s) after ensuring files exist locally

    Raises:
        FileNotFoundError: If file cannot be retrieved from S3
    """
    # Only process in batch mode
    if not MODE.IN_SNAKEMAKE_BATCH:
        return file_path

    # Handle list by recursing on each element
    if isinstance(file_path, list):
        return [ensure_file_available_to_batch(p) for p in file_path]

    # Only process string paths
    if not isinstance(file_path, str):
        return file_path

    # Check if file already exists
    if os.path.exists(file_path):
        return file_path

    # Check if path is in Snakemake storage area
    if ".snakemake/storage/s3/" not in file_path:
        # Not in Snakemake storage, can't help
        return file_path

    # Extract S3 path from local path
    # Format: /app/.snakemake/storage/s3/{bucket}/app/studio_data/...
    s3_path = _extract_s3_path(file_path)
    if not s3_path:
        logger.warning(
            f"Could not extract S3 path from Snakemake storage path: {file_path}"
        )
        return file_path

    # Download file from S3
    logger.info(f"Retrieving missing file from S3: {s3_path} -> {file_path}")
    _download_from_s3(s3_path, file_path)

    # Verify file now exists
    if not os.path.exists(file_path):
        raise FileNotFoundError(
            f"Failed to retrieve file from S3: {s3_path} to {file_path}"
        )

    logger.info(f"Successfully retrieved file from S3: {file_path}")
    return file_path


def _extract_s3_path(local_path: str) -> str:
    """
    Extract S3 URI from Snakemake storage local path.

    Args:
        local_path: Local path like
        /app/.snakemake/storage/s3/{bucket}/app/studio_data/...

    Returns:
        S3 URI like s3://{bucket}/app/studio_data/...
    """
    # Pattern: .snakemake/storage/s3/{bucket}/{key...}
    match = re.search(r"\.snakemake/storage/s3/([^/]+)/(.+)", local_path)
    if not match:
        return ""

    bucket = match.group(1)
    key = match.group(2)
    return f"s3://{bucket}/{key}"


def _download_from_s3(s3_uri: str, local_path: str) -> None:
    """
    Download file from S3 to local path.

    Args:
        s3_uri: S3 URI like s3://bucket/key
        local_path: Local file path to save to

    Raises:
        Exception: If S3 download fails
    """
    import boto3
    from botocore.exceptions import ClientError

    # Parse S3 URI
    if not s3_uri.startswith("s3://"):
        raise ValueError(f"Invalid S3 URI: {s3_uri}")

    parts = s3_uri[5:].split("/", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid S3 URI format: {s3_uri}")

    bucket, key = parts

    # Create parent directory if needed
    os.makedirs(os.path.dirname(local_path), exist_ok=True)

    # Download from S3
    s3_client = boto3.client("s3")
    try:
        s3_client.download_file(bucket, key, local_path)
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        logger.error(
            f"Failed to download from S3: {s3_uri} -> {local_path}. "
            f"Error: {error_code} - {str(e)}"
        )
        raise
