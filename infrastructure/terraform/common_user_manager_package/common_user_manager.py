"""
Common User Manager Lambda - Shared User Lifecycle Operations

Responsibilities:
- Heartbeat-based inactivity logout (both free and premium users)
- Recover stale workflow counts (crash recovery)
- Run every 10 minutes

This consolidates common operations that were duplicated across
premium_manager and free_manager lambdas.
"""

import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Dict

import boto3
import pymysql
from sqlalchemy import (
    TIMESTAMP,
    Column,
    Integer,
    MetaData,
    Table,
    create_engine,
    or_,
    update,
)
from sqlalchemy.dialects.mysql import BIGINT
from sqlalchemy.orm import Session

# ============================================================================
# Constants
# ============================================================================

# Database connection
DB_PORT_DEFAULT = 3306

# Workflow recovery timeouts
WORKFLOW_USER_INACTIVITY_HOURS = 2  # User must be inactive for this long
WORKFLOW_VERY_OLD_HOURS = 4  # Workflows older than this are assumed crashed

# User inactivity logout timeouts (can be overridden by env vars)
FREE_IDLE_TIMEOUT_HOURS_DEFAULT = 2
PREMIUM_IDLE_TIMEOUT_HOURS_DEFAULT = 2

# ============================================================================
# Table Metadata Definitions (defined once at module level for efficiency)
# ============================================================================

metadata = MetaData()

# FreeUserAssignment table
FREE_USERS_TABLE = Table(
    "free_user_assignments",
    metadata,
    Column("id", BIGINT(unsigned=True), primary_key=True),
    Column("active_workflow_count", Integer),
    Column("last_activity", TIMESTAMP),
    Column("last_workflow_start", TIMESTAMP),
    Column("last_workflow_end", TIMESTAMP),
)

# PremiumUserAssignment table
PREMIUM_USERS_TABLE = Table(
    "premium_user_assignments",
    metadata,
    Column("id", BIGINT(unsigned=True), primary_key=True),
    Column("active_workflow_count", Integer),
    Column("last_activity", TIMESTAMP),
    Column("last_workflow_start", TIMESTAMP),
    Column("last_workflow_end", TIMESTAMP),
)


def get_required_env_var(var_name: str, default_value: str = None) -> str:
    value = os.environ.get(var_name, default_value)
    if value is None or value == "":
        raise ValueError(f"Missing required environment variable: {var_name}")
    return value


def get_db_connection():
    """
    Get pymysql connection (legacy).

    Note: This function uses pymysql directly for backward compatibility with
    existing inactivity check functions. New code should use get_sqlalchemy_session().
    """

    @contextmanager
    def connection_context():
        conn = None
        try:
            rds_host = get_required_env_var("RDS_HOST")
            # Parse host and port from RDS_HOST (format: "host:port" or "host")
            if ":" in rds_host:
                host, port_str = rds_host.split(":", 1)
                port = int(port_str)
            else:
                host = rds_host
                port = DB_PORT_DEFAULT

            conn = pymysql.connect(
                host=host,
                port=port,
                user=get_required_env_var("RDS_USER"),
                password=get_required_env_var("RDS_PASSWORD"),
                database=get_required_env_var("RDS_DATABASE"),
                cursorclass=pymysql.cursors.DictCursor,
            )
            yield conn
        finally:
            if conn:
                conn.close()

    return connection_context()


@contextmanager
def get_sqlalchemy_session():
    """
    Get SQLAlchemy session for type-safe database operations.

    Note: Creates a new engine per call for Lambda compatibility.
    Lambda functions should not maintain persistent connections across invocations.
    """
    rds_host = get_required_env_var("RDS_HOST")
    # Parse host and port from RDS_HOST (format: "host:port" or "host")
    if ":" in rds_host:
        host, port_str = rds_host.split(":", 1)
        port = int(port_str)
    else:
        host = rds_host
        port = DB_PORT_DEFAULT

    user = get_required_env_var("RDS_USER")
    password = get_required_env_var("RDS_PASSWORD")
    database = get_required_env_var("RDS_DATABASE")

    # Create SQLAlchemy engine (disposed after use for Lambda)
    connection_string = (
        f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}" "?charset=utf8mb4"
    )
    engine = create_engine(connection_string, pool_pre_ping=True)

    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        engine.dispose()


def recover_stale_workflow_counts() -> Dict[str, int]:
    """
    Reset stale workflow counts for both free and premium users using hybrid approach.

    Only resets counts when ANY of these conditions are met:
    1. User is inactive (last_activity >WORKFLOW_USER_INACTIVITY_HOURS) AND
       workflow has completed (last_workflow_end >= last_workflow_start)
    2. User is inactive (last_activity >WORKFLOW_USER_INACTIVITY_HOURS) AND
       workflow started >WORKFLOW_VERY_OLD_HOURS ago (very old, likely crashed)

    This ensures we don't reset counts for:
    - Long-running workflows with active user sessions (last_activity is recent)
    - Recent workflows where user is still working
    - Workflows that might legitimately take 2-4 hours with inactive user

    Uses existing heartbeat system (last_activity) to determine user activity.
    """
    try:
        with get_sqlalchemy_session() as session:
            now = datetime.utcnow()
            user_inactive_threshold = now - timedelta(
                hours=WORKFLOW_USER_INACTIVITY_HOURS
            )
            workflow_very_old_threshold = now - timedelta(hours=WORKFLOW_VERY_OLD_HOURS)

            # Recover free user workflow counts
            # Note: NULL handling - users with NULL
            # last_activity are excluded by < comparison
            free_stmt = (
                update(FREE_USERS_TABLE)
                .where(FREE_USERS_TABLE.c.active_workflow_count > 0)
                .where(FREE_USERS_TABLE.c.last_activity.isnot(None))
                .where(FREE_USERS_TABLE.c.last_activity < user_inactive_threshold)
                .where(
                    or_(
                        # Workflow has completed (decrement failed)
                        # Both timestamps must exist and end >= start
                        (
                            FREE_USERS_TABLE.c.last_workflow_end.isnot(None)
                            & FREE_USERS_TABLE.c.last_workflow_start.isnot(None)
                            & (
                                FREE_USERS_TABLE.c.last_workflow_end
                                >= FREE_USERS_TABLE.c.last_workflow_start
                            )
                        ),
                        # Workflow is very old - likely crashed
                        # Start timestamp must exist and be very old
                        (
                            FREE_USERS_TABLE.c.last_workflow_start.isnot(None)
                            & (
                                FREE_USERS_TABLE.c.last_workflow_start
                                < workflow_very_old_threshold
                            )
                        ),
                    )
                )
                .values(active_workflow_count=0)
            )

            free_result = session.execute(free_stmt)
            free_affected = free_result.rowcount

            # Recover premium user workflow counts
            premium_stmt = (
                update(PREMIUM_USERS_TABLE)
                .where(PREMIUM_USERS_TABLE.c.active_workflow_count > 0)
                .where(PREMIUM_USERS_TABLE.c.last_activity.isnot(None))
                .where(PREMIUM_USERS_TABLE.c.last_activity < user_inactive_threshold)
                .where(
                    or_(
                        # Workflow has completed (decrement failed)
                        (
                            PREMIUM_USERS_TABLE.c.last_workflow_end.isnot(None)
                            & PREMIUM_USERS_TABLE.c.last_workflow_start.isnot(None)
                            & (
                                PREMIUM_USERS_TABLE.c.last_workflow_end
                                >= PREMIUM_USERS_TABLE.c.last_workflow_start
                            )
                        ),
                        # Workflow is very old - likely crashed
                        (
                            PREMIUM_USERS_TABLE.c.last_workflow_start.isnot(None)
                            & (
                                PREMIUM_USERS_TABLE.c.last_workflow_start
                                < workflow_very_old_threshold
                            )
                        ),
                    )
                )
                .values(active_workflow_count=0)
            )

            premium_result = session.execute(premium_stmt)
            premium_affected = premium_result.rowcount

            # Note: session.commit() is called by context manager on line 76

            total_affected = free_affected + premium_affected

            if total_affected > 0:
                print(
                    f"Recovered {total_affected} stale workflow counts "
                    f"(free: {free_affected}, premium: {premium_affected})"
                )

            return {
                "recovered": total_affected,
                "free": free_affected,
                "premium": premium_affected,
            }

    except Exception as e:
        print(f"Failed to recover stale workflow counts: {e}")
        return {"recovered": 0, "error": str(e)}


def check_free_user_inactivity() -> Dict[str, int]:
    """Check and logout inactive free users"""
    try:
        timeout_hours = int(
            get_required_env_var(
                "FREE_IDLE_TIMEOUT_HOURS", str(FREE_IDLE_TIMEOUT_HOURS_DEFAULT)
            )
        )

        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # Find inactive users
                cursor.execute(
                    """
                    SELECT user_id, instance_id
                    FROM free_user_assignments
                    WHERE last_activity < DATE_SUB(NOW(), INTERVAL %s HOUR)
                    """,
                    (timeout_hours,),
                )
                inactive_users = cursor.fetchall()

                if not inactive_users:
                    print("No inactive free users found")
                    return {"logged_out": 0}

                # Delete inactive assignments
                user_ids = [str(u["user_id"]) for u in inactive_users]
                placeholders = ",".join(["%s"] * len(user_ids))
                cursor.execute(
                    f"DELETE FROM free_user_assignments "
                    f"WHERE user_id IN ({placeholders})",
                    user_ids,
                )
                conn.commit()

                print(
                    f"Logged out {len(inactive_users)} inactive free users: {user_ids}"
                )
                return {"logged_out": len(inactive_users)}

    except Exception as e:
        print(f"Failed to check free user inactivity: {e}")
        return {"logged_out": 0, "error": str(e)}


def check_premium_user_inactivity() -> Dict[str, int]:
    """Check and logout inactive premium users"""
    try:
        timeout_hours = int(
            get_required_env_var(
                "PREMIUM_IDLE_TIMEOUT_HOURS", str(PREMIUM_IDLE_TIMEOUT_HOURS_DEFAULT)
            )
        )
        elbv2 = boto3.client("elbv2")

        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # Find inactive users
                cursor.execute(
                    """
                    SELECT user_id, target_group_arn, alb_rule_arn
                    FROM premium_user_assignments
                    WHERE status = 'active'
                    AND is_standby = 0
                    AND last_activity < DATE_SUB(NOW(), INTERVAL %s HOUR)
                    """,
                    (timeout_hours,),
                )
                inactive_users = cursor.fetchall()

                if not inactive_users:
                    print("No inactive premium users found")
                    return {"logged_out": 0}

                logged_out = 0
                failed_users = []

                for user in inactive_users:
                    user_id = user["user_id"]
                    try:
                        # Clean up ALB resources first (before DB deletion)
                        if user["alb_rule_arn"] and user["alb_rule_arn"] not in [
                            "STANDBY",
                            "standby",
                            "reserving",
                        ]:
                            try:
                                elbv2.delete_rule(RuleArn=user["alb_rule_arn"])
                            except elbv2.exceptions.RuleNotFoundException:
                                print(f"ALB rule already deleted for user {user_id}")

                        autoscaling_tg = os.environ.get(
                            "AUTOSCALING_TARGET_GROUP_ARN", ""
                        )
                        if (
                            user["target_group_arn"]
                            and user["target_group_arn"]
                            not in ["STANDBY", "standby", "reserving"]
                            and user["target_group_arn"] != autoscaling_tg
                        ):
                            try:
                                elbv2.delete_target_group(
                                    TargetGroupArn=user["target_group_arn"]
                                )
                            except elbv2.exceptions.TargetGroupNotFoundException:
                                print(
                                    f"Target group already deleted for user {user_id}"
                                )

                        # Delete assignment from database
                        cursor.execute(
                            "DELETE FROM premium_user_assignments WHERE user_id = %s",
                            (user_id,),
                        )
                        logged_out += 1

                    except Exception as e:
                        print(f"Failed to logout user {user_id}: {e}")
                        failed_users.append(user_id)
                        # Continue with other users even if one fails

                # Commit all successful deletions at once
                conn.commit()

                if failed_users:
                    print(
                        f"Logged out {logged_out} inactive premium users, "
                        f"{len(failed_users)} failed: {failed_users}"
                    )
                else:
                    print(f"Logged out {logged_out} inactive premium users")

                return {"logged_out": logged_out, "failed": len(failed_users)}

    except Exception as e:
        print(f"Failed to check premium user inactivity: {e}")
        return {"logged_out": 0, "error": str(e)}


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Common user manager handler - runs every 10 minutes

    Performs:
    1. Recover stale workflow counts
    2. Check and logout inactive users (both tiers)
    """
    print(f"Common user manager triggered: {json.dumps(event)}")
    print(f"Lambda request ID: {context.request_id if context else 'N/A'}")

    try:
        results = {}

        # 1. Recover stale workflow counts
        print("\n=== Step 1: Recovering stale workflow counts ===")
        results["workflow_recovery"] = recover_stale_workflow_counts()

        # 2. Check free user inactivity
        print("\n=== Step 2: Checking free user inactivity ===")
        results["free_inactivity"] = check_free_user_inactivity()

        # 3. Check premium user inactivity
        print("\n=== Step 3: Checking premium user inactivity ===")
        results["premium_inactivity"] = check_premium_user_inactivity()

        free_logged_out = results["free_inactivity"].get("logged_out", 0)
        premium_logged_out = results["premium_inactivity"].get("logged_out", 0)
        total_logged_out = free_logged_out + premium_logged_out

        workflows_recovered = results["workflow_recovery"].get("recovered", 0)

        print("\n=== Summary ===")
        print(f"Workflows recovered: {workflows_recovered}")
        print(
            f"Free users logged out: {results['free_inactivity'].get('logged_out', 0)}"
        )
        print(
            f"Premium users logged out: "
            f"{results['premium_inactivity'].get('logged_out', 0)}"
        )
        print(f"Total users logged out: {total_logged_out}")

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": f"Common user management complete. "
                    f"Recovered {workflows_recovered} workflows, logged out "
                    f"{total_logged_out} inactive users.",
                    "results": results,
                }
            ),
        }

    except Exception as e:
        print(f"Error in common user manager: {str(e)}")
        import traceback

        traceback.print_exc()
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }
