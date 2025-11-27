"""
Utilities for detecting the AWS Batch execution context.

This module provides a function to determine if the current Python process
is running inside an AWS Batch job, based on environment variables set by AWS Batch.
"""
import os

from studio.app.common.core.logger import AppLogger

logger = AppLogger.get_logger()


def is_running_in_batch() -> bool:
    """
    Detect if the current execution is happening in AWS Batch container.
    """
    # Check for AWS Batch specific environment variables
    batch_job_id = os.environ.get("AWS_BATCH_JOB_ID")
    if batch_job_id:
        logger.info(f"Detected AWS Batch execution: Job ID {batch_job_id}")
        return True
    return False
