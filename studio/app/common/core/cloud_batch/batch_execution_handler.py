"""
Batch Execution Handler Module

Handles batch-specific execution logic for AWS Batch jobs.
This module handles:
- Batch context detection and logging
- Batch-specific output handling
- S3 upload preparation for batch results
"""

import os

from studio.app.common.core.cloud_batch.batch_config import BATCH_CONFIG
from studio.app.common.core.cloud_batch.batch_context import is_running_in_batch
from studio.app.common.core.cloud_batch.batch_logging import log_batch_config
from studio.app.common.core.logger import AppLogger

logger = AppLogger.get_logger()


class BatchExecutionHandler:
    """Handles AWS Batch execution-specific operations"""

    @classmethod
    def detect_and_log_execution_context(cls) -> bool:
        """Detect execution context (batch vs local) and log relevant information

        Returns:
            bool: True if running in batch context, False otherwise
        """
        # Detect execution context
        is_batch = is_running_in_batch()
        if is_batch:
            logger.info("Running in AWS Batch context")
            # Log batch job information
            logger.info(f"Batch Job ID: {os.environ.get('AWS_BATCH_JOB_ID')}")
            logger.info(f"Batch Queue: {os.environ.get('AWS_BATCH_JOB_QUEUE')}")
        else:
            logger.info("Running in local/ECS context")

        # Determine if this should run on AWS Batch
        if BATCH_CONFIG.USE_AWS_BATCH and not is_batch:
            # We're in the main process, not in batch yet
            log_batch_config()
        else:
            logger.debug(
                "AWS Batch disabled or already in batch - using local execution"
            )

        return is_batch

    @classmethod
    def should_upload_to_s3(cls, is_batch: bool) -> bool:
        """Determine if results should be uploaded to S3

        Args:
            is_batch: Whether currently running in batch context

        Returns:
            bool: True if should upload to S3, False otherwise
        """
        return is_batch and BATCH_CONFIG.AWS_BATCH_S3_BUCKET_NAME is not None

    @classmethod
    def log_output_handling(cls, is_batch: bool) -> None:
        """Log information about how output will be handled

        Args:
            is_batch: Whether currently running in batch context
        """
        if cls.should_upload_to_s3(is_batch):
            logger.info(
                "Batch execution detected - "
                "if needed S3 upload will be implemented later"
            )
        else:
            logger.debug("Output will be saved locally (EFS or local filesystem)")
