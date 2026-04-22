#!/usr/bin/env python3
"""
CLI script to run the Data Cleanup background job.

This script can be run directly via cron or manually to clean up workspace
data for logged-out free tier users.

Usage:
    python studio/scripts/run_data_cleanup.py

Cron example (every hour):
    0 * * * * cd /path/to/optinist-for-cloud &&
    python studio/scripts/run_data_cleanup.py >> /var/log/araya-optinist/cleanup_job.log 2>&1
"""

import asyncio
import sys
from pathlib import Path

# Add the project root directory to the Python path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Import after path modification to avoid E402 linting errors
try:
    from studio.app.common.core.background.cleanup_job import DataCleanupJob
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
    """Run the data cleanup job"""
    logger.info("=== Data Cleanup Job Started ===")

    try:
        await DataCleanupJob.run()
        logger.info("=== Data Cleanup Job Completed Successfully ===")
        sys.exit(0)

    except Exception as e:
        logger.error(f"=== Data Cleanup Job Failed: {e} ===", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
