"""
Free User Utilities for Load Balancing

This module provides utility functions for the Free Manager Lambda to:
1. Detect idle free tier users
2. Count active free tier users
3. Validate safe migration conditions
"""

import os
from datetime import datetime, timedelta
from typing import Dict, List

import pymysql


def get_db_connection(auto_commit=False):
    """Create database connection with proper transaction management"""
    try:
        rds_host = os.environ.get("RDS_HOST")
        if not rds_host:
            raise ValueError("RDS_HOST environment variable not set")

        host = rds_host.split(":")[0] if ":" in rds_host else rds_host

        return pymysql.connect(
            host=host,
            port=3306,
            user=os.environ.get("RDS_USER"),
            password=os.environ.get("RDS_PASSWORD"),
            database=os.environ.get("RDS_DATABASE"),
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=auto_commit,
        )
    except Exception as e:
        print(f" Database connection failed: {str(e)}")
        raise


def is_user_idle(
    user_id: str,
    idle_threshold_minutes: int = 10,
) -> bool:
    """
    Check if a user is idle and safe to migrate.

    A user is considered idle if:
    1. active_workflow_count = 0 (no workflows running)
    2. last_activity is older than idle_threshold_minutes

    Args:
        user_id: User ID to check
        idle_threshold_minutes: Minutes of inactivity required (default: 10)

    Returns:
        True if user is idle and safe to migrate, False otherwise
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                query = """
                    SELECT active_workflow_count, last_activity
                    FROM free_user_assignments
                    WHERE user_id = %s
                """
                cursor.execute(query, (user_id,))
                result = cursor.fetchone()

                if not result:
                    # User not tracked yet, not idle
                    return False

                active_workflows = result["active_workflow_count"] or 0
                last_activity = result["last_activity"]

                # Must have no active workflows
                if active_workflows > 0:
                    return False

                # Check last activity time
                if last_activity:
                    idle_cutoff = datetime.now() - timedelta(
                        minutes=idle_threshold_minutes
                    )
                    if last_activity > idle_cutoff:
                        # User was active recently
                        return False

                # User is idle
                return True

    except Exception as e:
        print(f"Error checking if user {user_id} is idle: {e}")
        return False  # Conservative: don't migrate if uncertain


def get_idle_users_for_instance(
    instance_id: str,
    idle_threshold_minutes: int = 10,
) -> List[str]:
    """
    Get list of idle users on a specific instance.

    Args:
        instance_id: Instance ID to check
        idle_threshold_minutes: Minutes of inactivity required (default: 10)

    Returns:
        List of user IDs that are idle on this instance
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                idle_cutoff = datetime.now() - timedelta(minutes=idle_threshold_minutes)

                query = """
                    SELECT user_id
                    FROM free_user_assignments
                    WHERE instance_id = %s
                      AND active_workflow_count = 0
                      AND last_activity < %s
                """
                cursor.execute(query, (instance_id, idle_cutoff))
                results = cursor.fetchall()

                return [row["user_id"] for row in results]

    except Exception as e:
        print(f"Error getting idle users for instance {instance_id}: {e}")
        return []


def count_active_free_users(activity_threshold_minutes: int = 10) -> int:
    """
    Count total number of active free tier users across all instances.

    A user is considered active if they have had activity within
    the activity_threshold_minutes.

    Args:
        activity_threshold_minutes: Minutes to consider a user active (default: 10)

    Returns:
        Number of active free tier users
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                activity_cutoff = datetime.now() - timedelta(
                    minutes=activity_threshold_minutes
                )

                query = """
                    SELECT COUNT(*) as count
                    FROM free_user_assignments
                    WHERE last_activity >= %s
                """
                cursor.execute(query, (activity_cutoff,))
                result = cursor.fetchone()

                return result["count"] if result else 0

    except Exception as e:
        print(f"Error counting active free users: {e}")
        return 0


def get_users_per_instance(activity_threshold_minutes: int = 10) -> Dict[str, int]:
    """
    Get count of active users per instance.

    Args:
        activity_threshold_minutes: Minutes to consider a user active (default: 10)

    Returns:
        Dictionary mapping instance_id -> user count
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                activity_cutoff = datetime.now() - timedelta(
                    minutes=activity_threshold_minutes
                )

                query = """
                    SELECT instance_id, COUNT(*) as user_count
                    FROM free_user_assignments
                    WHERE last_activity >= %s
                    GROUP BY instance_id
                """
                cursor.execute(query, (activity_cutoff,))
                results = cursor.fetchall()

                return {row["instance_id"]: row["user_count"] for row in results}

    except Exception as e:
        print(f"Error getting users per instance: {e}")
        return {}


def migrate_user_to_instance(user_id: str, new_instance_id: str) -> bool:
    """
    Migrate a user to a new instance.

    This updates the database record and the user's next request
    will be routed to the new instance via load balancer.

    Args:
        user_id: User ID to migrate
        new_instance_id: New instance ID to assign

    Returns:
        True if migration successful, False otherwise
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # Update instance assignment
                query = """
                    UPDATE free_user_assignments
                    SET instance_id = %s,
                        migration_count = migration_count + 1,
                        last_migration = NOW()
                    WHERE user_id = %s
                      AND active_workflow_count = 0
                """
                cursor.execute(query, (new_instance_id, user_id))
                conn.commit()

                if cursor.rowcount > 0:
                    print(
                        f"Migrated user {user_id} from their current instance "
                        f"to {new_instance_id}"
                    )
                    return True
                else:
                    print(
                        f"Failed to migrate user {user_id}: "
                        f"user has active workflows or doesn't exist"
                    )
                    return False

    except Exception as e:
        print(f"Error migrating user {user_id} to instance {new_instance_id}: {e}")
        return False


def is_distribution_balanced(distribution: Dict[str, int], tolerance: int = 1) -> bool:
    """
    Check if user distribution across instances is reasonably balanced.

    Args:
        distribution: Dictionary mapping instance_id -> user count
        tolerance: Maximum allowed difference between most and least loaded (default: 1)

    Returns:
        True if balanced (max - min <= tolerance), False otherwise
    """
    if not distribution:
        return True

    counts = list(distribution.values())
    max_count = max(counts)
    min_count = min(counts)

    is_balanced = (max_count - min_count) <= tolerance

    print(
        f"Distribution check: max={max_count}, min={min_count}, "
        f"diff={max_count - min_count}, tolerance={tolerance}, "
        f"balanced={is_balanced}"
    )

    return is_balanced
