"""
Premium User Utilities for Safe Migration

This module provides utility functions for the Premium Manager Lambda to:
1. Detect idle premium tier users (safe to migrate)
2. Validate safe migration conditions
3. Prevent workflow interruption during reassignment
"""

import os
from typing import List

import pymysql

# Shared constants from Lambda Layer (mounted at /opt/python by AWS Lambda)
from aws_constants import DatabaseConfig


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
                port=DatabaseConfig.DEFAULT_PORT,
                user=os.environ.get("RDS_USER"),
                password=os.environ.get("RDS_PASSWORD"),
                database=os.environ.get("RDS_DATABASE"),
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=auto_commit,
            )
            yield conn
        except Exception as e:
            print(f"💥 Database connection failed: {str(e)}")
            raise
        finally:
            # CRITICAL: Always close the connection to prevent leaks
            if conn is not None:
                try:
                    conn.close()
                    print("🔌 Database connection closed")
                except Exception as e:
                    print(f"⚠️  Warning: Error closing database connection: {str(e)}")

    return connection_context()


def is_premium_user_idle(user_id: str) -> bool:
    """
    Check if a premium user is idle and safe to migrate/reassign.

    A user is considered idle if:
    - active_workflow_count = 0 (no workflows running)

    Note: We don't check last_activity time. Users without active workflows
    can be migrated/reassigned regardless of when they were last active.

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
                    FROM premium_user_assignments
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
        print(f"Error checking if premium user {user_id} is idle: {e}")
        return False  # Conservative: don't migrate if uncertain


def get_idle_premium_users_for_instance(instance_id: str) -> List[str]:
    """
    Get list of idle premium users on a specific instance.

    Idle users are those who are logged in but NOT running any workflows.
    They are safe to migrate/reassign to another instance without disrupting work.

    Args:
        instance_id: Instance ID to check

    Returns:
        List of user IDs that are idle on this instance
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # Idle = assigned to instance but no active workflows
                # No time-based restriction - users without workflows can be migrated
                # regardless of their last activity time
                query = """
                    SELECT user_id
                    FROM premium_user_assignments
                    WHERE instance_id = %s
                      AND active_workflow_count = 0
                      AND status = 'active'
                """
                cursor.execute(query, (instance_id,))
                results = cursor.fetchall()

                return [row["user_id"] for row in results]

    except Exception as e:
        print(f"Error getting idle premium users for instance {instance_id}: {e}")
        return []


def get_users_on_autoscaling_pool() -> List[dict]:
    """
    Get all premium users currently on the autoscaling pool.

    These users need to be migrated to dedicated instances.

    Returns:
        List of dicts with user_id and other assignment info
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                query = """
                    SELECT user_id, active_workflow_count, last_activity
                    FROM premium_user_assignments
                    WHERE instance_id = 'autoscaling-pool'
                      AND status = 'active'
                """
                cursor.execute(query)
                results = cursor.fetchall()

                return results

    except Exception as e:
        print(f"Error getting users on autoscaling pool: {e}")
        return []


def get_users_on_shared_instance(instance_id: str) -> List[dict]:
    """
    Get all premium users on a shared instance (multiple users on same instance).

    Args:
        instance_id: Instance ID to check

    Returns:
        List of dicts with user_id and other assignment info
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                query = """
                    SELECT user_id, active_workflow_count, last_activity
                    FROM premium_user_assignments
                    WHERE instance_id = %s
                      AND status = 'active'
                      AND is_shared = 1
                """
                cursor.execute(query, (instance_id,))
                results = cursor.fetchall()

                return results

    except Exception as e:
        print(f"Error getting users on shared instance {instance_id}: {e}")
        return []


def can_migrate_user(user_id: str) -> bool:
    """
    Check if a user can be safely migrated right now.

    Safe migration requires:
    - User exists in premium_user_assignments
    - User has no active workflows (active_workflow_count = 0)

    Args:
        user_id: User ID to check

    Returns:
        True if user can be safely migrated, False otherwise
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                query = """
                    SELECT active_workflow_count, instance_id
                    FROM premium_user_assignments
                    WHERE user_id = %s
                      AND status = 'active'
                """
                cursor.execute(query, (user_id,))
                result = cursor.fetchone()

                if not result:
                    print(f"User {user_id} not found in premium_user_assignments")
                    return False

                active_workflows = result["active_workflow_count"] or 0

                if active_workflows > 0:
                    print(
                        f"User {user_id} has {active_workflows} active workflows, "
                        f"cannot migrate"
                    )
                    return False

                print(f"User {user_id} can be safely migrated (no active workflows)")
                return True

    except Exception as e:
        print(f"Error checking if user {user_id} can be migrated: {e}")
        return False  # Conservative: don't migrate if uncertain
