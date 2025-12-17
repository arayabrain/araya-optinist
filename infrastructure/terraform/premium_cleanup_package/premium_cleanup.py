"""
Premium Cleanup Lambda - Data & Resource Hygiene

Responsibilities:
- Remove stale assignments from database (>2 hours inactive)
- Clean up orphaned ALB resources (rules/target groups with no DB entry)
- Reconcile instance states (ensure DB matches AWS reality)
- Monitor standby pool health (read-only)

Does NOT:
- Make scaling decisions (premium_manager handles that)
- Stop or start instances (premium_manager handles that)
- Update ECS service count (premium_manager handles that)

Triggered by CloudWatch Events hourly.
Coordinates with premium_manager which handles all compute/capacity decisions.
"""

import json
import os
import time
from typing import Any, Dict, List

import boto3
import pymysql


def get_required_env_var(var_name: str, default_value: str = None) -> str:
    """Safely get required environment variable with helpful error message"""
    value = os.environ.get(var_name, default_value)
    if value is None or value == "":
        raise ValueError(
            f"Missing required environment variable: {var_name}. "
            "Check your Terraform configuration and Lambda environment settings."
        )
    return value


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
            rds_host = get_required_env_var("RDS_HOST")
            conn = pymysql.connect(
                host=rds_host.split(":")[0],
                port=int(rds_host.split(":")[1]) if ":" in rds_host else 3306,
                user=get_required_env_var("RDS_USER"),
                password=get_required_env_var("RDS_PASSWORD"),
                database=get_required_env_var("RDS_DATABASE"),
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=auto_commit,  # Default False for transactions
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


def with_transaction(func):
    """Decorator to wrap database operations in transactions"""

    def wrapper(*args, **kwargs):
        with get_db_connection() as conn:
            try:
                result = func(conn, *args, **kwargs)
                conn.commit()
                return result
            except Exception as e:
                conn.rollback()
                print(f"Transaction rolled back due to error: {e}")
                raise e

    return wrapper


def get_assigned_users_for_instance(instance_id: str) -> List[str]:
    """Get list of users assigned to an instance"""
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT user_id FROM premium_user_assignments
                       WHERE instance_id = %s AND status = 'active'""",
                    (instance_id,),
                )
                users = cursor.fetchall()
                return [user["user_id"] for user in users]
    except Exception as e:
        print(f"Error getting assigned users: {str(e)}")
        return []


def check_instance_readiness(instance_id: str) -> bool:
    """Check if an instance has a running ECS task and is ready for user assignment"""
    ecs = boto3.client("ecs")
    cluster_name = get_required_env_var("CLUSTER_NAME")

    try:
        # Get ECS tasks running on this instance
        tasks_response = ecs.list_tasks(
            cluster=cluster_name, containerInstance=instance_id
        )

        if not tasks_response["taskArns"]:
            print(f"No tasks running on instance {instance_id}")
            return False

        # Check task status
        task_details = ecs.describe_tasks(
            cluster=cluster_name, tasks=tasks_response["taskArns"]
        )

        for task in task_details["tasks"]:
            if (
                task.get("taskDefinitionArn", "").find("premium") != -1
                and task.get("lastStatus") == "RUNNING"
            ):
                print(f"Premium task running and ready on instance {instance_id}")
                return True

        return False

    except Exception as e:
        print(f"Error checking instance readiness: {str(e)}")
        return False


@with_transaction
def cleanup_stale_assignments(connection) -> Dict[str, Any]:
    """
    Clean up stale premium user assignments based on last activity.
    Uses transactions to prevent race conditions.
    """
    try:
        stale_threshold_hours = int(
            get_required_env_var("PREMIUM_IDLE_TIMEOUT_HOURS", "2")
        )

        print(f"Starting cleanup of assignments idle for >{stale_threshold_hours}h")

        with connection.cursor() as cursor:
            # Use SELECT FOR UPDATE to prevent race conditions
            cursor.execute(
                """
                SELECT user_id, instance_id, target_group_arn,
                alb_rule_arn, last_activity
                FROM premium_user_assignments
                WHERE status = 'active'
                AND is_standby = 0
                AND last_activity < DATE_SUB(NOW(), INTERVAL %s HOUR)
                FOR UPDATE
            """,
                (stale_threshold_hours,),
            )

            stale_assignments = cursor.fetchall()

            if not stale_assignments:
                print("No stale assignments found")
                return {
                    "cleaned_assignments": 0,
                    "message": "No stale assignments to clean",
                }

            print(f"Found {len(stale_assignments)} stale assignments to clean")

            # Clean up AWS resources for each stale assignment
            elbv2 = boto3.client("elbv2")
            cleaned_count = 0

            for assignment in stale_assignments:
                user_id = assignment["user_id"]
                target_group_arn = assignment["target_group_arn"]
                alb_rule_arn = assignment["alb_rule_arn"]

                try:
                    print(f"Cleaning stale assignment for user {user_id}")

                    # Delete ALB rule and target group
                    if alb_rule_arn and alb_rule_arn != "STANDBY":
                        try:
                            elbv2.delete_rule(RuleArn=alb_rule_arn)
                            print(f"Deleted ALB rule: {alb_rule_arn}")
                        except Exception as e:
                            print(f"Warning: Failed to delete ALB rule: {e}")

                    if target_group_arn and target_group_arn != "STANDBY":
                        try:
                            elbv2.delete_target_group(TargetGroupArn=target_group_arn)
                            print(f"Deleted target group: {target_group_arn}")
                        except Exception as e:
                            print(f"Warning: Failed to delete target group: {e}")

                    # Remove from database
                    cursor.execute(
                        "DELETE FROM premium_user_assignments WHERE user_id = %s",
                        (user_id,),
                    )

                    cleaned_count += 1
                    print(f"Cleaned assignment for user {user_id}")

                except Exception as e:
                    print(f"Error cleaning assignment for user {user_id}: {e}")
                    # Continue with other assignments

            print(
                f"Cleanup complete: {cleaned_count}/{len(stale_assignments)} "
                f"assignments cleaned"
            )

            return {
                "cleaned_assignments": cleaned_count,
                "total_stale": len(stale_assignments),
                "message": f"Cleaned {cleaned_count} stale assignments",
            }

    except Exception as e:
        print(f"Error during stale assignment cleanup: {str(e)}")
        raise e


def cleanup_orphaned_alb_resources() -> Dict[str, Any]:
    """
    Clean up orphaned ALB listener rules and target groups that have no database entry.

    This handles cases where Lambda failed after creating ALB resources but before
    storing the assignment in the database, leaving orphaned resources.
    """
    try:
        print("Scanning for orphaned ALB resources...")

        elbv2 = boto3.client("elbv2")
        alb_listener_arn = get_required_env_var("ALB_LISTENER_ARN")

        # Get all ALB listener rules
        rules_response = elbv2.describe_rules(ListenerArn=alb_listener_arn)
        alb_rules = rules_response.get("Rules", [])

        # Filter for premium user rules (exclude default rule)
        premium_rules = []
        for rule in alb_rules:
            if rule.get("Priority") == "default":
                continue

            # Check if rule has premium user conditions
            # (X-User-ID and X-User-Tier headers)
            conditions = rule.get("Conditions", [])
            has_user_id = any(
                c.get("Field") == "http-header"
                and c.get("HttpHeaderConfig", {}).get("HttpHeaderName") == "X-User-ID"
                for c in conditions
            )
            has_user_tier = any(
                c.get("Field") == "http-header"
                and c.get("HttpHeaderConfig", {}).get("HttpHeaderName") == "X-User-Tier"
                for c in conditions
            )

            if has_user_id and has_user_tier:
                premium_rules.append(rule)

        print(f"Found {len(premium_rules)} premium user ALB rules")

        # Get all active assignments from database
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT alb_rule_arn, target_group_arn, user_id
                       FROM premium_user_assignments
                       WHERE status = 'active' AND is_standby = 0"""
                )
                db_assignments = cursor.fetchall()

        db_rule_arns = {
            a["alb_rule_arn"] for a in db_assignments if a["alb_rule_arn"] != "STANDBY"
        }
        print(f"Found {len(db_rule_arns)} active assignments in database")

        # Find orphaned rules (in ALB but not in database)
        orphaned_rules = [
            rule for rule in premium_rules if rule["RuleArn"] not in db_rule_arns
        ]

        if not orphaned_rules:
            print("No orphaned ALB resources found")
            return {
                "orphaned_rules_deleted": 0,
                "orphaned_target_groups_deleted": 0,
                "message": "No orphaned resources to clean",
            }

        print(f"Found {len(orphaned_rules)} orphaned ALB rules to clean up")

        # Clean up orphaned resources
        rules_deleted = 0
        target_groups_deleted = 0

        for rule in orphaned_rules:
            rule_arn = rule["RuleArn"]
            priority = rule.get("Priority")

            try:
                # Get target group ARN from rule actions
                target_group_arn = None
                for action in rule.get("Actions", []):
                    if action.get("Type") == "forward":
                        target_group_arn = action.get("TargetGroupArn")
                        break

                print(f"Deleting orphaned rule (priority {priority}): {rule_arn}")

                # Delete the ALB rule
                elbv2.delete_rule(RuleArn=rule_arn)
                rules_deleted += 1
                print("Deleted ALB rule")

                # Delete the target group if it exists
                if target_group_arn:
                    try:
                        elbv2.delete_target_group(TargetGroupArn=target_group_arn)
                        target_groups_deleted += 1
                        print(f"Deleted target group: {target_group_arn}")
                    except Exception as tg_error:
                        print(f"Warning: Failed to delete target group: {tg_error}")

            except Exception as e:
                print(f"Error deleting orphaned rule {rule_arn}: {e}")
                # Continue with other rules

        print(
            f"Orphaned resource cleanup complete: {rules_deleted} rules, "
            f"{target_groups_deleted} target groups deleted"
        )

        return {
            "orphaned_rules_deleted": rules_deleted,
            "orphaned_target_groups_deleted": target_groups_deleted,
            "total_orphaned": len(orphaned_rules),
            "message": f"Cleaned {rules_deleted} orphaned ALB rules and "
            f"{target_groups_deleted} target groups",
        }

    except Exception as e:
        print(f"Error during orphaned resource cleanup: {str(e)}")
        return {
            "orphaned_rules_deleted": 0,
            "orphaned_target_groups_deleted": 0,
            "error": str(e),
        }


def get_standby_pool_status() -> Dict[str, Any]:
    """Get detailed status of the premium standby pool"""
    try:
        ec2 = boto3.client("ec2")
        premium_instance_ids = get_required_env_var("PREMIUM_INSTANCE_IDS").split(",")

        instances_response = ec2.describe_instances(InstanceIds=premium_instance_ids)

        status = {
            "total_instances": len(premium_instance_ids),
            "running": 0,
            "stopped": 0,
            "failed": 0,
            "assigned_users": 0,
            "idle_running": 0,
            "health_issues": [],
        }

        for reservation in instances_response["Reservations"]:
            for instance in reservation["Instances"]:
                instance_id = instance["InstanceId"]
                instance_state = instance["State"]["Name"]

                if instance_state == "running":
                    status["running"] += 1
                    assigned_users = get_assigned_users_for_instance(instance_id)
                    status["assigned_users"] += len(assigned_users)

                    if not assigned_users:
                        status["idle_running"] += 1
                        if not check_instance_readiness(instance_id):
                            status["health_issues"].append(
                                f"Instance {instance_id} running but not ready"
                            )

                elif instance_state == "stopped":
                    status["stopped"] += 1
                else:
                    status["failed"] += 1
                    status["health_issues"].append(
                        f"Instance {instance_id} in {instance_state} state"
                    )

        return status

    except Exception as e:
        print(f"Error getting standby pool status: {str(e)}")
        return {"error": str(e)}


def ensure_standby_pool_capacity() -> Dict[str, Any]:
    """Ensure standby pool maintains required capacity and handle failed instances"""
    try:
        status = get_standby_pool_status()

        if "error" in status:
            return {"success": False, "error": status["error"]}

        target_stopped = 1
        actions_taken = []

        # Log current status
        print(
            f"Standby pool status: {status['running']} running, "
            f"{status['stopped']} stopped, {status['failed']} failed"
        )

        # Check for capacity issues
        if status["stopped"] < target_stopped and status["idle_running"] == 0:
            actions_taken.append("Low standby capacity detected")

        if status["failed"] > 0:
            actions_taken.append(f"Found {status['failed']} failed instances")

        if status["health_issues"]:
            actions_taken.extend([f"{issue}" for issue in status["health_issues"]])

        return {
            "success": True,
            "status": status,
            "actions_taken": actions_taken,
            "recommendations": [
                f"Target standby capacity: {target_stopped} stopped instances",
                f"Current capacity: {status['stopped']} "
                f"stopped, {status['running']} running",
            ],
        }

    except Exception as e:
        print(f"Error ensuring standby pool capacity: {str(e)}")
        return {"success": False, "error": str(e)}


def reconcile_instance_states() -> Dict[str, Any]:
    """
    Reconcile database instance states with actual AWS instance states
    Moved from premium_manager as this is maintenance, not real-time operation
    """
    try:
        # Get all instances from AWS
        ec2 = boto3.client("ec2")
        premium_instance_ids = get_required_env_var("PREMIUM_INSTANCE_IDS").split(",")
        instances_response = ec2.describe_instances(InstanceIds=premium_instance_ids)

        aws_instance_map = {}
        for reservation in instances_response["Reservations"]:
            for instance in reservation["Instances"]:
                aws_instance_map[instance["InstanceId"]] = {
                    "instance_id": instance["InstanceId"],
                    "state": instance["State"]["Name"],
                }

        # Get all assignments from database
        cleanup_count = 0
        update_count = 0

        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT user_id, instance_id, instance_state, status
                       FROM premium_user_assignments WHERE status = 'active'"""
                )
                db_assignments = cursor.fetchall()

                for assignment in db_assignments:
                    user_id = assignment["user_id"]
                    instance_id = assignment["instance_id"]
                    db_state = assignment["instance_state"]
                    aws_instance = aws_instance_map.get(instance_id)

                    if not aws_instance:
                        # Instance no longer exists in AWS - cleanup
                        print(
                            f"Cleaning up assignment for terminated instance "
                            f"{instance_id} (user {user_id})"
                        )
                        cursor.execute(
                            "DELETE FROM premium_user_assignments WHERE user_id = %s",
                            (user_id,),
                        )
                        cleanup_count += 1
                        connection.commit()
                    elif aws_instance["state"] != db_state:
                        # Update database state to match AWS
                        aws_state = aws_instance["state"]
                        print(
                            f"Updating instance state for user "
                            f"{user_id}: {db_state} → {aws_state}"
                        )
                        cursor.execute(
                            """UPDATE premium_user_assignments
                               SET instance_state = %s, last_state_check = NOW()
                               WHERE user_id = %s""",
                            (aws_state, user_id),
                        )
                        update_count += 1
                        connection.commit()

        return {
            "cleanup_count": cleanup_count,
            "update_count": update_count,
            "total_aws_instances": len(aws_instance_map),
            "total_db_assignments": len(db_assignments),
        }

    except Exception as e:
        print(f"Error reconciling instance states: {str(e)}")
        return {"error": str(e)}


@with_transaction
def cleanup_test_user_assignments(connection, user_emails: List[str]) -> Dict[str, Any]:
    """
    Clean up premium assignments for specific test users by email.
    Designed to be called by test scripts that need to clean up test data.

    This function performs complete cleanup of premium user assignments:
    1. Looks up user IDs from email addresses
    2. Finds all premium assignments for those users
    3. Deletes AWS resources (ALB rules, target groups)
    4. Removes database records from premium_user_assignments table

    Usage (from test scripts):
        # Invoke this Lambda with:
        lambda_client = boto3.client('lambda')
        response = lambda_client.invoke(
            FunctionName='subscr-premium-cleanup',
            InvocationType='RequestResponse',
            Payload=json.dumps({
                "action": "cleanup_test_users",
                "user_emails": ["user1@test.com", "user2@test.com"]
            })
        )

    Args:
        connection: Database connection (provided by @with_transaction decorator)
        user_emails: List of user email addresses to clean up assignments for

    Returns:
        Dict with cleanup statistics:
        {
            "success": True/False,
            "message": "Description of what happened",
            "assignments_deleted": 3,
            "users_cleaned": 2
        }

    Note:
        - This is a transactional operation (uses @with_transaction)
        - AWS resource deletion is best-effort (continues on errors)
        - Database changes are rolled back if any critical error occurs
    """
    try:
        if not user_emails:
            return {
                "success": False,
                "message": "No user emails provided",
                "assignments_deleted": 0,
            }

        print(f"Cleaning up premium assignments for {len(user_emails)} test users")

        with connection.cursor() as cursor:
            # First, get user IDs for the given emails
            placeholders = ", ".join(["%s"] * len(user_emails))
            cursor.execute(
                f"""SELECT id, email FROM users
                    WHERE email IN ({placeholders})""",
                user_emails,
            )
            users = cursor.fetchall()

            if not users:
                return {
                    "success": True,
                    "message": "No users found with provided emails",
                    "assignments_deleted": 0,
                }

            user_ids = [user["id"] for user in users]
            print(f"Found {len(user_ids)} users to clean up")

            # Get assignments for these users (including ALB resources to clean)
            user_id_placeholders = ", ".join(["%s"] * len(user_ids))
            cursor.execute(
                f"""SELECT user_id, instance_id, target_group_arn, alb_rule_arn
                    FROM premium_user_assignments
                    WHERE user_id IN ({user_id_placeholders})""",
                user_ids,
            )
            assignments = cursor.fetchall()

            if not assignments:
                return {
                    "success": True,
                    "message": "No assignments found for these users",
                    "assignments_deleted": 0,
                }

            print(f"Found {len(assignments)} assignments to clean up")

            # Clean up AWS resources for each assignment
            elbv2 = boto3.client("elbv2")
            cleaned_count = 0

            for assignment in assignments:
                user_id = assignment["user_id"]
                target_group_arn = assignment["target_group_arn"]
                alb_rule_arn = assignment["alb_rule_arn"]

                try:
                    # Delete ALB rule and target group (skip STANDBY markers)
                    if alb_rule_arn and alb_rule_arn != "STANDBY":
                        try:
                            elbv2.delete_rule(RuleArn=alb_rule_arn)
                            print(f"Deleted ALB rule for user {user_id}")
                        except Exception as e:
                            print(f"Warning: Failed to delete ALB rule: {e}")

                    if target_group_arn and target_group_arn != "STANDBY":
                        try:
                            elbv2.delete_target_group(TargetGroupArn=target_group_arn)
                            print(f"Deleted target group for user {user_id}")
                        except Exception as e:
                            print(f"Warning: Failed to delete target group: {e}")

                    # Remove from database
                    cursor.execute(
                        "DELETE FROM premium_user_assignments WHERE user_id = %s",
                        (user_id,),
                    )
                    cleaned_count += 1
                    print(f"Deleted assignment for user {user_id}")

                except Exception as e:
                    print(f"Error cleaning assignment for user {user_id}: {e}")
                    # Continue with other assignments

            print(
                f"Test cleanup complete: "
                f"{cleaned_count}/{len(assignments)} assignments cleaned"
            )

            return {
                "success": True,
                "message": f"Cleaned {cleaned_count} test user assignments",
                "assignments_deleted": cleaned_count,
                "users_cleaned": len(user_ids),
            }

    except Exception as e:
        print(f"Error during test user cleanup: {str(e)}")
        raise e


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Premium Cleanup Lambda Handler - Data & Resource Hygiene

    Responsibilities:
    1. Remove stale assignments (>2 hours inactive)
    2. Clean up orphaned ALB resources
    3. Reconcile instance states with AWS reality
    4. Monitor standby pool health

    Does NOT make scaling decisions - that's premium_manager's job.

    Also supports manual invocations:
    - cleanup_test_users: Clean up premium assignments for specific test users
      Event format:
      {"action": "cleanup_test_users", "user_emails": ["email1@example.com", ...]}
    """

    print(f"Premium cleanup triggered by event: {json.dumps(event)}")
    print(f"Lambda context: {context.function_name if context else 'No context'}")

    try:
        # Check if this is a manual test cleanup invocation
        action = event.get("action")
        if action == "cleanup_test_users":
            user_emails = event.get("user_emails", [])
            print(f"Manual test cleanup invocation for {len(user_emails)} users")
            cleanup_result = cleanup_test_user_assignments(user_emails)
            return {
                "statusCode": 200,
                "body": json.dumps(
                    {"message": cleanup_result.get("message"), "result": cleanup_result}
                ),
            }

        # Otherwise, proceed with normal scheduled cleanup
        # Initialize results
        results = {
            "cleanup_stats": {},
            "orphaned_cleanup_stats": {},
            "reconciliation_stats": {},
            "capacity_check": {},
            "timestamp": time.time(),
        }

        # 1. Cleanup stale assignments from database
        print("Step 1: Cleaning up stale assignments...")
        results["cleanup_stats"] = cleanup_stale_assignments()

        # 2. Cleanup orphaned ALB resources (rules/target groups with no DB entry)
        print("Step 2: Cleaning up orphaned ALB resources...")
        results["orphaned_cleanup_stats"] = cleanup_orphaned_alb_resources()

        # 3. Reconcile instance states (update DB to match AWS reality)
        print("Step 3: Reconciling instance states...")
        results["reconciliation_stats"] = reconcile_instance_states()

        # 4. Monitor standby pool capacity (read-only check)
        print("Step 4: Checking standby pool capacity...")
        results["capacity_check"] = ensure_standby_pool_capacity()

        # Summary
        total_operations = (
            results["cleanup_stats"].get("cleaned_assignments", 0)
            + results["orphaned_cleanup_stats"].get("orphaned_rules_deleted", 0)
            + results["reconciliation_stats"].get("cleanup_count", 0)
            + results["reconciliation_stats"].get("update_count", 0)
        )

        print(
            f"Premium cleanup complete: {total_operations} total operations performed"
        )

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": f"Premium cleanup completed successfully. "
                    f"{total_operations} operations performed.",
                    "results": results,
                }
            ),
        }

    except Exception as e:
        print(f"Error during premium cleanup: {str(e)}")
        return {
            "statusCode": 500,
            "body": json.dumps(
                {
                    "error": f"Premium cleanup failed: {str(e)}",
                    "results": results if "results" in locals() else {},
                }
            ),
        }
