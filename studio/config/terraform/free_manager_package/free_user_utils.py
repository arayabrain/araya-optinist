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
    """
    Create database connection with proper transaction management and auto-close.

    This function returns a context manager that ensures connections are properly
    closed when exiting the context, preventing connection leaks.

    Usage:
        with get_db_connection() as conn:
            # Use connection
            # Connection will be automatically closed on exit
    """
    from contextlib import contextmanager

    @contextmanager
    def connection_context():
        conn = None
        try:
            rds_host = os.environ.get("RDS_HOST")
            if not rds_host:
                raise ValueError("RDS_HOST environment variable not set")

            host = rds_host.split(":")[0] if ":" in rds_host else rds_host

            conn = pymysql.connect(
                host=host,
                port=3306,
                user=os.environ.get("RDS_USER"),
                password=os.environ.get("RDS_PASSWORD"),
                database=os.environ.get("RDS_DATABASE"),
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=auto_commit,
            )
            yield conn
        except Exception as e:
            print(f" Database connection failed: {str(e)}")
            raise
        finally:
            # CRITICAL: Always close the connection to prevent leaks
            if conn is not None:
                try:
                    conn.close()
                    print(" Database connection closed")
                except Exception as e:
                    print(f" Warning: Error closing database connection: {str(e)}")

    return connection_context()


def is_user_idle(user_id: str) -> bool:
    """
    Check if a user is idle and safe to migrate.

    A user is considered idle if:
    - active_workflow_count = 0 (no workflows running)

    Note: We no longer check last_activity time. Users without active workflows
    can be migrated regardless of when they were last active.

    Args:
        user_id: User ID to check

    Returns:
        True if user is idle and safe to migrate, False otherwise
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                query = """
                    SELECT active_workflow_count
                    FROM free_user_assignments
                    WHERE user_id = %s
                """
                cursor.execute(query, (user_id,))
                result = cursor.fetchone()

                if not result:
                    # User not tracked yet, not idle
                    return False

                active_workflows = result["active_workflow_count"] or 0

                # User is idle if no active workflows
                return active_workflows == 0

    except Exception as e:
        print(f"Error checking if user {user_id} is idle: {e}")
        return False  # Conservative: don't migrate if uncertain


def get_idle_users_for_instance(instance_id: str) -> List[str]:
    """
    Get list of idle users on a specific instance.

    Idle users are those who are logged in but NOT running any workflows.
    They are safe to migrate to another instance without disrupting work.

    Args:
        instance_id: Instance ID to check

    Returns:
        List of user IDs that are idle on this instance
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # Idle = logged in (has a session) but no active workflows
                # No time-based restriction - users without workflows can be migrated
                # regardless of their last activity time
                query = """
                    SELECT user_id
                    FROM free_user_assignments
                    WHERE instance_id = %s
                      AND active_workflow_count = 0
                """
                cursor.execute(query, (instance_id,))
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
