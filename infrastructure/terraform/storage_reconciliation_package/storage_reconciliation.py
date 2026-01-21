"""
Storage Reconciliation Lambda - Periodic Storage Usage Reconciliation

Responsibilities:
- Reconcile incremental storage tracking with actual S3 storage
- Process users in batches to prevent OOM
- Use distributed locks to prevent concurrent scans
- Log significant drift for monitoring

Runs every 60 minutes to balance accuracy vs. cost/performance.
"""

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict

import boto3
import pymysql

# Shared constants from Lambda Layer (mounted at /opt/python by AWS Lambda)
from aws_constants import DatabaseConfig

# ============================================================================
# Constants
# ============================================================================

# Storage reconciliation configuration
BATCH_SIZE = 10  # Process 10 users at a time to prevent OOM
RATE_LIMIT_DELAY_SECONDS = 0.5  # 0.5s delay between users
ADVISORY_LOCK_NAMESPACE = 12345  # Namespace for distributed locks
DRIFT_THRESH_PERCENT = 5.0  # 5% drift threshold for logging
DRIFT_THRESH_BYTES = 100 * 1024 * 1024  # 100 MB drift threshold

# S3 pagination
S3_PAGE_SIZE = 1000  # Objects per page


# ============================================================================
# Helper Functions
# ============================================================================


def get_required_env_var(var_name: str, default_value: str = None) -> str:
    """Safely get required environment variable with helpful error message"""
    value = os.environ.get(var_name, default_value)
    if value is None or value == "":
        raise ValueError(
            f"Missing required environment variable: {var_name}. "
            "Check your Terraform configuration and Lambda environment settings."
        )
    return value


@contextmanager
def get_db_connection(auto_commit=False):
    """
    Create database connection with proper transaction management and auto-close.
    """
    conn = None
    try:
        rds_host = get_required_env_var("RDS_HOST")
        conn = pymysql.connect(
            host=rds_host.split(":")[0],
            port=int(rds_host.split(":")[1])
            if ":" in rds_host
            else DatabaseConfig.DEFAULT_PORT,
            user=get_required_env_var("RDS_USER"),
            password=get_required_env_var("RDS_PASSWORD"),
            database=get_required_env_var("RDS_DATABASE"),
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=auto_commit,
        )
        yield conn
    except Exception as e:
        print(f"Database connection failed: {str(e)}")
        raise
    finally:
        if conn is not None:
            try:
                conn.close()
                print("Database connection closed")
            except Exception as e:
                print(f"Warning: Error closing database connection: {str(e)}")


def stream_s3_objects(s3_client, bucket: str, prefix: str):
    """
    Generator that yields S3 objects one page at a time without accumulating metadata.

    This true streaming approach prevents boto3 paginator from accumulating
    internal state across all pages, which can cause OOM for large datasets.
    """
    continuation_token = None

    while True:
        # Build request parameters
        params = {
            "Bucket": bucket,
            "Prefix": prefix,
            "MaxKeys": S3_PAGE_SIZE,
        }

        if continuation_token:
            params["ContinuationToken"] = continuation_token

        try:
            # Fetch single page (no paginator state)
            response = s3_client.list_objects_v2(**params)

            # Yield the page immediately
            yield response

            # Check if more pages exist
            if not response.get("IsTruncated"):
                break

            continuation_token = response.get("NextContinuationToken")
            # Previous page automatically garbage collected

        except Exception as e:
            print(f"Error listing S3 objects in {bucket}/{prefix}: {e}")
            break


def calculate_user_s3_storage(user_id: int) -> int:
    """
    Calculate total S3 storage usage for a user across all their workspaces.

    Args:
        user_id: User ID to calculate storage for

    Returns:
        Total storage usage in bytes
    """
    try:
        bucket_name = get_required_env_var("S3_DEFAULT_BUCKET_NAME")
        s3_client = boto3.client("s3")

        total_size = 0

        # Get all workspaces for this user
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """SELECT unique_id FROM workspaces WHERE owner_id = %s""",
                    (user_id,),
                )
                workspaces = cursor.fetchall()

        if not workspaces:
            print(f"No workspaces found for user {user_id}")
            return 0

        workspace_count = len(workspaces)
        print(f"Scanning {workspace_count} workspace(s) for user {user_id}")

        # Scan each workspace's S3 storage
        for workspace in workspaces:
            workspace_id = workspace["unique_id"]
            # S3 prefix format: {workspace_id}/
            prefix = f"{workspace_id}/"

            workspace_size = 0
            page_count = 0

            # Use streaming to prevent memory accumulation
            for page in stream_s3_objects(s3_client, bucket_name, prefix):
                if "Contents" in page:
                    page_size = sum(obj["Size"] for obj in page["Contents"])
                    workspace_size += page_size
                    page_count += 1

                    if page_count % 10 == 0:  # Log progress every 10 pages
                        print(
                            f"Workspace {workspace_id}: {page_count} pages scanned, "
                            f"{workspace_size:,} bytes so far"
                        )

            total_size += workspace_size
            print(
                f"Workspace {workspace_id}: {workspace_size:,} bytes "
                f"({page_count} pages)"
            )

        print(
            f"User {user_id} total S3 storage: {total_size:,} bytes "
            f"across {workspace_count} workspace(s)"
        )

        return total_size

    except Exception as e:
        print(f"Error calculating S3 storage for user {user_id}: {e}")
        raise


def try_acquire_lock(conn, user_id: int) -> bool:
    """
    Try to acquire a distributed lock for scanning a user's storage.

    Uses MySQL GET_LOCK to prevent concurrent scans of the same user.

    Args:
        conn: Database connection
        user_id: User ID to lock

    Returns:
        True if lock acquired, False otherwise
    """
    try:
        lock_name = f"storage_scan_{ADVISORY_LOCK_NAMESPACE}_{user_id}"
        lock_timeout = 0  # Non-blocking

        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT GET_LOCK(%s, %s) as lock_result", (lock_name, lock_timeout)
            )
            result = cursor.fetchone()
            return result["lock_result"] == 1

    except Exception as e:
        print(f"Error acquiring lock for user {user_id}: {e}")
        return False


def release_lock(conn, user_id: int):
    """
    Release the distributed lock for a user's storage scan.

    Args:
        conn: Database connection
        user_id: User ID to unlock
    """
    try:
        lock_name = f"storage_scan_{ADVISORY_LOCK_NAMESPACE}_{user_id}"

        with conn.cursor() as cursor:
            cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))

    except Exception as e:
        print(f"Warning: Failed to release lock for user {user_id}: {e}")


def reconcile_user_storage(
    user_id: int,
    db_storage: int,
    delta: int,  # noqa: ARG001 (delta used for logging context)
) -> Dict[str, Any]:
    """
    Reconcile storage for a single user.

    Args:
        user_id: User ID to reconcile
        db_storage: Current storage value in database
        delta: Delta since last scan (passed for context, logged by caller)

    Returns:
        Dict with reconciliation results
    """
    lock_acquired = False
    conn = None

    try:
        # Acquire distributed lock to prevent concurrent scans
        with get_db_connection() as conn:
            lock_acquired = try_acquire_lock(conn, user_id)

        if not lock_acquired:
            print(f"⊘ Skipping user {user_id}: another process is already scanning")
            return {"user_id": user_id, "skipped": True, "reason": "locked"}

        print(f"Acquired lock for user {user_id}")

        # Calculate actual S3 storage
        actual_storage = calculate_user_s3_storage(user_id)

        # Calculate drift
        drift_bytes = abs(actual_storage - db_storage)
        drift_percent = (drift_bytes / db_storage * 100) if db_storage > 0 else 0

        # Update database with actual S3 value and reset delta
        # Use explicit UTC timestamp to match Studio app's get_current_datetime()
        now_utc = datetime.now(timezone.utc)

        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """UPDATE user_storage_usage
                       SET storage_usage_bytes = %s,
                           delta_since_last_scan = 0,
                           last_full_scan = %s,
                           last_updated = %s
                       WHERE user_id = %s""",
                    (actual_storage, now_utc, now_utc, user_id),
                )
                conn.commit()

        # Log drift if significant
        if drift_percent > DRIFT_THRESH_PERCENT or drift_bytes > DRIFT_THRESH_BYTES:
            print(
                f"Significant drift for user {user_id}: "
                f"DB={db_storage:,} → S3={actual_storage:,} bytes "
                f"(drift: {drift_bytes:,} bytes, {drift_percent:.1f}%)"
            )
            significant_drift = True
        else:
            print(
                f"User {user_id} reconciled: {db_storage:,} → {actual_storage:,} bytes "
                f"(drift: {drift_bytes:,} bytes, {drift_percent:.1f}%)"
            )
            significant_drift = False

        return {
            "user_id": user_id,
            "skipped": False,
            "db_storage": db_storage,
            "actual_storage": actual_storage,
            "drift_bytes": drift_bytes,
            "drift_percent": drift_percent,
            "significant_drift": significant_drift,
        }

    except Exception as e:
        print(f"Failed to reconcile user {user_id}: {e}")
        return {"user_id": user_id, "skipped": True, "reason": "error", "error": str(e)}

    finally:
        # Always release lock if acquired
        if lock_acquired and conn:
            release_lock(conn, user_id)
            print(f"🔓 Released lock for user {user_id}")


def run_storage_reconciliation() -> Dict[str, Any]:
    """
    Run storage reconciliation for all users with pending changes.

    Processes users in batches to prevent OOM and uses rate limiting
    to avoid S3 API throttling.

    Returns:
        Dict with reconciliation statistics
    """
    try:
        stats = {
            "total_users": 0,
            "reconciled": 0,
            "skipped": 0,
            "errors": 0,
            "significant_drifts": 0,
            "total_drift_bytes": 0,
        }

        # Get total count of users to process
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """SELECT COUNT(*) as count
                       FROM user_storage_usage
                       WHERE delta_since_last_scan > 0 OR last_full_scan IS NULL"""
                )
                result = cursor.fetchone()
                stats["total_users"] = result["count"] if result else 0

        if stats["total_users"] == 0:
            print("ℹ No users need reconciliation")
            return stats

        print(
            f"Starting reconciliation for {stats['total_users']} user(s) "
            f"(batch size: {BATCH_SIZE})"
        )

        # Process users in batches
        offset = 0
        batch_num = 0

        while True:
            # Fetch next batch
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """SELECT user_id, storage_usage_bytes,
                           delta_since_last_scan, last_full_scan
                           FROM user_storage_usage
                           WHERE delta_since_last_scan > 0 OR last_full_scan IS NULL
                           ORDER BY user_id
                           LIMIT %s OFFSET %s""",
                        (BATCH_SIZE, offset),
                    )
                    batch = cursor.fetchall()

            if not batch:
                break  # No more users to process

            batch_num += 1
            print(
                f"\nBatch {batch_num}: Processing {len(batch)} user(s) "
                f"(offset: {offset})"
            )

            # Process each user in the batch
            for user_row in batch:
                user_id = user_row["user_id"]
                db_storage = user_row["storage_usage_bytes"]
                delta = user_row["delta_since_last_scan"]
                last_scan = user_row["last_full_scan"]

                print(
                    f"\nUser {user_id}: current={db_storage:,} bytes, "
                    f"delta={delta:,} bytes, last_scan={last_scan or 'never'}"
                )

                # Reconcile this user
                result = reconcile_user_storage(user_id, db_storage, delta)

                if result.get("skipped"):
                    stats["skipped"] += 1
                    if result.get("reason") == "error":
                        stats["errors"] += 1
                else:
                    stats["reconciled"] += 1
                    stats["total_drift_bytes"] += result.get("drift_bytes", 0)
                    if result.get("significant_drift"):
                        stats["significant_drifts"] += 1

                # Rate limiting to avoid S3 throttling
                time.sleep(RATE_LIMIT_DELAY_SECONDS)

            # Move to next batch
            offset += BATCH_SIZE

            print(
                f"\nBatch {batch_num} complete. Progress: "
                f"{stats['reconciled'] + stats['skipped']}/{stats['total_users']}"
            )

        print(f"\n{'='*60}")
        print("Storage Reconciliation Complete")
        print(f"{'='*60}")
        print(f"Total users: {stats['total_users']}")
        print(f"Reconciled: {stats['reconciled']}")
        print(f"Skipped: {stats['skipped']}")
        print(f"Errors: {stats['errors']}")
        print(f"Significant drifts: {stats['significant_drifts']}")
        print(f"Total drift: {stats['total_drift_bytes']:,} bytes")
        print(f"{'='*60}")

        return stats

    except Exception as e:
        print(f"Storage reconciliation job failed: {e}")
        import traceback

        traceback.print_exc()
        raise


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Storage Reconciliation Lambda Handler

    Runs every 60 minutes to reconcile incremental storage with actual S3 storage.

    Event format (scheduled):
        {
            "source": "aws.events",
            "detail-type": "Scheduled Event"
        }
    """
    print(f"Storage reconciliation triggered by event: {json.dumps(event)}")
    print(f"Lambda context: {context.function_name if context else 'No context'}")

    start_time = time.time()

    try:
        # Run reconciliation
        stats = run_storage_reconciliation()

        elapsed_time = time.time() - start_time

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": f"Storage reconciliation completed successfully. "
                    f"Reconciled {stats['reconciled']}/{stats['total_users']} users "
                    f"in {elapsed_time:.1f}s",
                    "stats": stats,
                    "elapsed_seconds": elapsed_time,
                }
            ),
        }

    except Exception as e:
        elapsed_time = time.time() - start_time
        print(f"Error during storage reconciliation: {str(e)}")
        import traceback

        traceback.print_exc()

        return {
            "statusCode": 500,
            "body": json.dumps(
                {
                    "error": f"Storage reconciliation failed: {str(e)}",
                    "elapsed_seconds": elapsed_time,
                }
            ),
        }
