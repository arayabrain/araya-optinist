"""
Batch Path Handler Module

Centralizes all path handling logic for AWS Batch execution with S3/EFS storage.
This module handles:
- Detection of S3 and remote storage modes
- Path conversion between absolute and relative formats
- Extraction of IDs from S3 storage paths
"""

import os

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.mode import MODE
from studio.app.dir_path import DIRPATH

logger = AppLogger.get_logger()


class BatchPathHandler:
    """Handles path operations for AWS Batch with S3/EFS storage"""

    @classmethod
    def is_s3_storage_mode(cls) -> bool:
        """Check if we're in S3 storage mode by checking environment variables"""
        use_aws_batch = os.environ.get("USE_AWS_BATCH", "")
        s3_bucket = os.environ.get("S3_DEFAULT_BUCKET_NAME")

        is_s3_mode = use_aws_batch.lower() == "true" and s3_bucket is not None

        logger.info(
            f"S3 storage mode check: USE_AWS_BATCH='{use_aws_batch}', "
            f"S3_DEFAULT_BUCKET_NAME='{s3_bucket}', is_s3_mode={is_s3_mode}"
        )

        # If not in S3 mode, log all relevant environment variables for debugging
        if not is_s3_mode:
            logger.debug("Not in S3 mode. Current environment:")
            for key in [
                "USE_AWS_BATCH",
                "S3_DEFAULT_BUCKET_NAME",
                "OPTINIST_DIR",
                "AWS_DEFAULT_REGION",
                "EFS_MOUNT_TARGET",
            ]:
                value = os.environ.get(key, "NOT_SET")
                logger.debug(f"{key}={value}")

        return is_s3_mode

    @classmethod
    def is_remote_storage_mode(cls) -> bool:
        """Check if we're in remote storage mode (S3 or EFS with AWS Batch)

        Both S3 and EFS storage modes in AWS Batch require relative paths
        to work properly with Snakemake's storage settings.
        """
        use_aws_batch = os.environ.get("USE_AWS_BATCH", "")
        s3_bucket = os.environ.get("S3_DEFAULT_BUCKET_NAME")
        efs_mount = os.environ.get("EFS_MOUNT_TARGET")

        # S3 storage mode: AWS Batch + S3 bucket configured
        is_s3_mode = use_aws_batch.lower() == "true" and s3_bucket is not None

        # EFS storage mode: AWS Batch + EFS mount configured (but no S3)
        is_efs_mode = (
            use_aws_batch.lower() == "true"
            and efs_mount is not None
            and s3_bucket is None
        )

        is_remote_mode = is_s3_mode or is_efs_mode

        logger.debug(
            f"Remote storage mode check: USE_AWS_BATCH='{use_aws_batch}', "
            f"S3_DEFAULT_BUCKET_NAME='{s3_bucket}', EFS_MOUNT_TARGET='{efs_mount}', "
            f"is_s3_mode={is_s3_mode}, is_efs_mode={is_efs_mode}, "
            f"is_remote_mode={is_remote_mode}"
        )

        return is_remote_mode

    @classmethod
    def make_relative_path(cls, absolute_path: str) -> str:
        """Convert absolute path to relative path for remote storage compatibility

        For S3 and EFS storage in AWS Batch, Snakemake expects relative paths that
        will be prefixed with the storage prefix.
        Absolute paths cause double slash issues.
        """
        logger.debug(f"make_relative_path called with: {absolute_path}")

        if not cls.is_remote_storage_mode():
            logger.debug(
                f"Not in remote storage mode, returning absolute path: {absolute_path}"
            )
            return absolute_path

        # Strip the DATA_DIR prefix to make paths relative
        # DIRPATH.DATA_DIR = "/app/studio_data" in container
        data_dir = DIRPATH.DATA_DIR
        logger.debug(f"DATA_DIR: {data_dir}")

        if absolute_path.startswith(data_dir + "/"):
            # Remove "/app/studio_data/" prefix, leaving "output/1/abc123/file.pkl"
            relative_path = absolute_path[len(data_dir) + 1 :]
            logger.debug(f"Converted path (case 1): {absolute_path} -> {relative_path}")
            return relative_path
        elif absolute_path.startswith(data_dir):
            # Remove "/app/studio_data" prefix, handle case without trailing slash
            remaining = absolute_path[len(data_dir) :]
            # If remaining starts with "/", remove it to avoid empty path
            relative_path = remaining.lstrip("/")
            logger.debug(f"Converted path (case 2): {absolute_path} -> {relative_path}")
            return relative_path

        # If path doesn't start with DATA_DIR, might be
        # already relative or different structure
        # Remove leading slash if present to ensure relative path
        relative_path = absolute_path.lstrip("/")
        logger.debug(f"Converted path (case 3): {absolute_path} -> {relative_path}")
        return relative_path

    @classmethod
    def extract_ids_from_s3_path(cls, output_dir_normalized: str) -> tuple:
        """Extract workspace_id, unique_id, and function_id from S3 storage path

        In AWS Batch with S3 storage, paths look like:
          .snakemake/storage/s3/{bucket}/app/studio_data/output/{workspace_id}/{unique_id}/{function_id}

        Returns:
            tuple: (workspace_id, unique_id, function_id)
            or partial tuple with None values
        """
        # Find the "output/" segment and extract everything after it
        output_marker = "/output/"
        if output_marker in output_dir_normalized:
            # After "/output/" is: {workspace_id}/{unique_id}/{function_id}
            ids_part = output_dir_normalized.split(output_marker, 1)[1]
            splitted_ids = ids_part.rstrip("/").split("/")
            return splitted_ids
        else:
            # Fallback: couldn't find expected structure
            return []

    @classmethod
    def is_batch_s3_path(cls, output_dir_normalized: str) -> bool:
        """Check if the given path is a Batch S3 storage path"""
        return (
            MODE.IN_SNAKEMAKE_BATCH
            and ".snakemake/storage/s3/" in output_dir_normalized
        )
