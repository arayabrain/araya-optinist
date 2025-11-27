"""
Utilities for logging AWS Batch configuration and runtime information.

This module provides functions to log details about the AWS Batch environment,
such as queue names, job definitions, and S3 bucket configurations, which are
useful for debugging and monitoring batch job executions.
"""
from studio.app.common.core.cloud_batch.batch_config import BATCH_CONFIG
from studio.app.common.core.logger import AppLogger

logger = AppLogger.get_logger()


def log_batch_config() -> None:
    """
    Logs AWS Batch configuration details for debugging purposes.
    """
    logger.debug("=================== AWS BATCH CONFIG ===================")
    logger.debug(f"aws_batch_free_queue = {BATCH_CONFIG.AWS_BATCH_FREE_QUEUE}")
    logger.debug(f"aws_batch_paid_queue = {BATCH_CONFIG.AWS_BATCH_PAID_QUEUE}")
    logger.debug(f"aws_batch_job_definition={BATCH_CONFIG.AWS_BATCH_JOB_DEFINITION}")
    logger.debug(f"aws_batch_s3_bucket_name={BATCH_CONFIG.AWS_BATCH_S3_BUCKET_NAME}")
    logger.debug(f"aws_default_provider = {BATCH_CONFIG.AWS_DEFAULT_PROVIDER}")
    logger.debug("====================================================")
