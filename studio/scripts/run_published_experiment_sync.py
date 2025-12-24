#!/usr/bin/env python3
"""
CLI script to run the Published Experiment Sync background job.

This script can be run directly via cron or manually to sync published
experiments from S3 to local storage.

Usage:
    python studio/scripts/run_published_experiment_sync.py

Cron example (every 5 minutes):
    */5 * * * * cd /path/to/optinist-for-cloud &&
    python studio/scripts/run_published_experiment_sync.py
    >> /var/log/optinist/sync_job.log 2>&1
"""

import asyncio
import sys
from pathlib import Path

# Add the project root directory to the Python path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Import after path modification to avoid E402 linting errors
try:
    from studio.app.common.core.background.sync_job import PublishedExperimentSyncJob
    from studio.app.common.core.logger import AppLogger
except ImportError as e:
    print(f"Import error: {e}")
    print(
        "Make sure you're running from the correct "
        "directory with dependencies installed"
    )
    sys.exit(1)

logger = AppLogger.get_logger()


async def main():
    """Run the published experiment sync job"""
    logger.info("=== Published Experiment Sync Job Started ===")

    try:
        await PublishedExperimentSyncJob.run()
        logger.info("=== Published Experiment Sync Job Completed Successfully ===")
        sys.exit(0)

    except Exception as e:
        logger.error(
            f"=== Published Experiment Sync Job Failed: {e} ===", exc_info=True
        )
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
