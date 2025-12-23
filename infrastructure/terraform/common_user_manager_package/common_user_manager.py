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
from typing import Any, Dict

import boto3
import pymysql


def get_required_env_var(var_name: str, default_value: str = None) -> str:
    value = os.environ.get(var_name, default_value)
    if value is None or value == "":
        raise ValueError(f"Missing required environment variable: {var_name}")
    return value


def get_db_connection():
    from contextlib import contextmanager

    @contextmanager
    def connection_context():
        conn = None
        try:
            rds_host = get_required_env_var("RDS_HOST")
            host = rds_host.split(":")[0] if ":" in rds_host else rds_host

            conn = pymysql.connect(
                host=host,
                port=3306,
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


def recover_stale_workflow_counts() -> Dict[str, int]:
    """Reset stale workflow counts (>30 min old) for both free and premium users."""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # Recover free user workflow counts
                free_sql = """
                    UPDATE free_user_assignments
                    SET active_workflow_count = 0
                    WHERE active_workflow_count > 0
                    AND last_workflow_start < DATE_SUB(NOW(), INTERVAL 30 MINUTE)
                """
                free_affected = cursor.execute(free_sql)

                # Recover premium user workflow counts
                premium_sql = """
                    UPDATE premium_user_assignments
                    SET active_workflow_count = 0
                    WHERE active_workflow_count > 0
                    AND last_workflow_start < DATE_SUB(NOW(), INTERVAL 30 MINUTE)
                """
                premium_affected = cursor.execute(premium_sql)

                conn.commit()

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
    """Check and logout inactive free users (>2 hours)"""
    try:
        timeout_hours = int(get_required_env_var("FREE_IDLE_TIMEOUT_HOURS", "2"))

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
    """Check and logout inactive premium users (>2 hours)"""
    try:
        timeout_hours = int(get_required_env_var("PREMIUM_IDLE_TIMEOUT_HOURS", "2"))
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
