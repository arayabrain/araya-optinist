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
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Dict

import boto3
import pymysql

# Shared constants from Lambda Layer (mounted at /opt/python by AWS Lambda)
from aws_constants import (
    DatabaseConfig,
    EnvironmentConfig,
    PremiumAssignment,
    SubscriptionType,
)

if TYPE_CHECKING:
    from mypy_boto3_elbv2 import ElasticLoadBalancingv2Client

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


SSL_ARGS = {"check_hostname": False}


def _get_db_params():
    """Parse RDS connection params from environment."""
    rds_host = get_required_env_var("RDS_HOST")
    if ":" in rds_host:
        host, port_str = rds_host.split(":", 1)
        port = int(port_str)
    else:
        host = rds_host
        port = DatabaseConfig.DEFAULT_PORT
    return {
        "host": host,
        "port": port,
        "user": get_required_env_var("RDS_USER"),
        "password": get_required_env_var("RDS_PASSWORD"),
        "database": get_required_env_var("RDS_DATABASE"),
    }


def _build_mysql_url(params):
    """Build SQLAlchemy MySQL connection URL."""
    return (
        f"mysql+pymysql://{params['user']}:"
        f"{params['password']}@{params['host']}:"
        f"{params['port']}/{params['database']}"
        f"?charset=utf8mb4"
    )


def _create_ssl_connection(params):
    """Create a pymysql connection with SSL enforcement."""
    return pymysql.connect(
        host=params["host"],
        port=params["port"],
        user=params["user"],
        password=params["password"],
        database=params["database"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        ssl=SSL_ARGS,
    )


def get_db_connection():
    """Get pymysql connection for direct SQL queries."""

    @contextmanager
    def connection_context():
        conn = None
        try:
            conn = _create_ssl_connection(_get_db_params())
            yield conn
        finally:
            if conn:
                conn.close()

    return connection_context()


@contextmanager
def get_sqlalchemy_session():
    """
    Get SQLAlchemy session for type-safe database operations.

    Uses creator= bypass because SQLAlchemy's normal SSL
    params cause Access denied with RDS Proxy.
    """
    params = _get_db_params()
    engine = create_engine(
        _build_mysql_url(params),
        pool_pre_ping=True,
        creator=lambda: _create_ssl_connection(params),
    )

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
            now = datetime.now(timezone.utc)
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
                SubscriptionType.FREE: free_affected,
                SubscriptionType.PREMIUM: premium_affected,
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
                user_ids = [u["user_id"] for u in inactive_users]
                placeholders = ",".join(["%s"] * len(user_ids))
                cursor.execute(
                    "DELETE FROM free_user_assignments "
                    "WHERE user_id IN (" + placeholders + ")",
                    user_ids,
                )
                conn.commit()

                print(
                    f"Logged out {len(inactive_users)} inactive free users: {user_ids}"
                )
                return {"logged_out": len(inactive_users)}

    except Exception as e:
        print(f"Failed to check free user inactivity: {e}")
        traceback.print_exc()
        return {"logged_out": 0, "error": str(e)}


# Mirrored in premium_manager.py & premium_cleanup.py — keep all three in sync.
def _tg_unhealthy_alarm_name(tg_arn: str) -> "str | None":
    """Derive the UnHealthyHostCount alarm name for a premium target group ARN."""
    idx = tg_arn.find(":targetgroup/")
    suffix = tg_arn[idx + 1 :] if idx != -1 else tg_arn
    parts = suffix.split("/")
    if len(parts) < 2:
        return None
    return f"{EnvironmentConfig.get_env_prefix()}-{parts[1]}-unhealthy-hosts"


def _delete_tg_unhealthy_alarm(cw: Any, tg_arn: str) -> None:
    """Best-effort, idempotent delete of a target group's UnHealthyHostCount alarm."""
    try:
        alarm_name = _tg_unhealthy_alarm_name(tg_arn)
        if alarm_name:
            cw.delete_alarms(AlarmNames=[alarm_name])
    except Exception as e:
        print(f"WARNING: Failed to delete unhealthy-host alarm for {tg_arn}: {e}")


def check_premium_user_inactivity() -> Dict[str, int]:
    """Check and logout inactive premium users"""
    try:
        timeout_hours = int(
            get_required_env_var(
                "PREMIUM_IDLE_TIMEOUT_HOURS", str(PREMIUM_IDLE_TIMEOUT_HOURS_DEFAULT)
            )
        )
        elbv2: "ElasticLoadBalancingv2Client" = boto3.client("elbv2")
        cw = boto3.client("cloudwatch")

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
                        # Delete from database FIRST
                        # This ensures user appears "not assigned" immediately
                        # If ALB cleanup fails later, orphaned resources are cleaned
                        # by hourly cleanup job
                        cursor.execute(
                            "DELETE FROM premium_user_assignments WHERE user_id = %s",
                            (user_id,),
                        )

                        # Clean up ALB resources (after DB deletion)
                        # Skip marker values (standby/reserving placeholders)
                        if user["alb_rule_arn"] and user[
                            "alb_rule_arn"
                        ].lower() not in [
                            PremiumAssignment.STANDBY,
                            PremiumAssignment.RESERVING,
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
                            and user["target_group_arn"].lower()
                            not in [
                                PremiumAssignment.STANDBY,
                                PremiumAssignment.RESERVING,
                            ]
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
                            _delete_tg_unhealthy_alarm(cw, user["target_group_arn"])

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
        traceback.print_exc()
        return {"logged_out": 0, "error": str(e)}


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Common user manager handler - runs every 10 minutes

    Performs:
    1. Recover stale workflow counts
    2. Check and logout inactive users (both tiers)
    """
    print(f"Common user manager triggered: {json.dumps(event)}")
    print(f"Lambda request ID: {context.aws_request_id if context else 'N/A'}")

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
        traceback.print_exc()
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }
