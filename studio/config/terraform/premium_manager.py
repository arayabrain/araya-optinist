"""
Premium Manager Lambda Function

Handles assignment and release of premium users to/from spot fleet instances.
Triggered by API calls when premium users log in/out.
Manages ALB routing rules for premium user traffic.
"""

import json
import os
import time
from typing import Any, Dict

import boto3
import pymysql


def get_db_connection():
    """Create database connection"""
    return pymysql.connect(
        host=os.environ["RDS_HOST"].split(":")[0],
        port=3306,
        user=os.environ["RDS_USER"],
        password=os.environ["RDS_PASSWORD"],
        database=os.environ["RDS_DATABASE"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def store_user_assignment(
    user_id: str, instance_id: str, target_group_arn: str, rule_arn: str
):
    """Store user assignment in RDS"""
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                # Check if user already has assignment
                cursor.execute(
                    "SELECT user_id FROM premium_user_assignments WHERE user_id = %s",
                    (user_id,),
                )
                if cursor.fetchone():
                    raise Exception(f"User {user_id} already has a premium assignment")

                # Insert new assignment
                cursor.execute(
                    """
                    INSERT INTO premium_user_assignments
                    (user_id, instance_id, target_group_arn, alb_rule_arn, status)
                    VALUES (%s, %s, %s, %s, 'active')
                """,
                    (user_id, instance_id, target_group_arn, rule_arn),
                )

        print(f"Stored assignment in RDS: user {user_id} -> instance {instance_id}")
    except Exception as e:
        print(f"Error storing assignment in RDS: {str(e)}")
        raise e


def remove_user_assignment(user_id: str):
    """Remove user assignment from RDS"""
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                # Get assignment details before deletion
                cursor.execute(
                    """SELECT instance_id, target_group_arn, alb_rule_arn
                       FROM premium_user_assignments WHERE user_id = %s""",
                    (user_id,),
                )
                assignment = cursor.fetchone()

                if not assignment:
                    raise Exception(f"No assignment found for user {user_id}")

                # Delete assignment
                cursor.execute(
                    "DELETE FROM premium_user_assignments WHERE user_id = %s",
                    (user_id,),
                )

        print(
            f"Removed assignment from RDS: user {user_id} -> "
            f"instance {assignment['instance_id']}"
        )
        return assignment
    except Exception as e:
        print(f"Error removing assignment from RDS: {str(e)}")
        raise e


def get_assigned_users_for_instance(instance_id: str):
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


def get_premium_user_status(user_id: str) -> Dict[str, Any]:
    """Get premium user assignment status"""
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT instance_id, target_group_arn, alb_rule_arn, status,
                    assigned_at FROM premium_user_assignments WHERE user_id = %s""",
                    (user_id,),
                )
                assignment = cursor.fetchone()

                if not assignment:
                    return {
                        "statusCode": 404,
                        "body": json.dumps(
                            {"error": f"No premium assignment found for user {user_id}"}
                        ),
                    }

                return {
                    "statusCode": 200,
                    "body": json.dumps(
                        {
                            "user_id": user_id,
                            "instance_id": assignment["instance_id"],
                            "target_group_arn": assignment["target_group_arn"],
                            "alb_rule_arn": assignment["alb_rule_arn"],
                            "status": assignment["status"],
                            "assigned_at": assignment["assigned_at"].isoformat()
                            if assignment["assigned_at"]
                            else None,
                        }
                    ),
                }

    except Exception as e:
        print(f"Error getting premium user status: {str(e)}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Handle premium user assignment lifecycle events

    Event structure:
    {
        "action": "assign" | "release",
        "user_id": "123",
        "tier": "premium"
    }
    """

    print(f"Premium manager received event: {json.dumps(event)}")
    print(f"Lambda context: {context.function_name if context else 'No context'}")

    try:
        http_method = event.get("httpMethod", "POST")

        if http_method == "GET":
            # Handle status request (GET /premium/status?user_id=xxx)
            query_params = event.get("queryStringParameters") or {}
            user_id = query_params.get("user_id")

            if not user_id:
                return {
                    "statusCode": 400,
                    "body": json.dumps({"error": "Missing user_id query parameter"}),
                }

            return get_premium_user_status(user_id)

        else:
            # Handle POST requests (assign/release)
            # Parse body from API Gateway
            body = event.get("body", "{}")
            if isinstance(body, str):
                try:
                    body_data = json.loads(body)
                except json.JSONDecodeError:
                    body_data = {}
            else:
                body_data = body or {}

            action = body_data.get("action")
            user_id = body_data.get("user_id")

            if not action or not user_id:
                return {
                    "statusCode": 400,
                    "body": json.dumps({"error": "Missing action or user_id"}),
                }

            if action == "assign":
                return assign_premium_user(user_id, body_data)
            elif action == "release":
                return release_premium_user(user_id)
            else:
                return {
                    "statusCode": 400,
                    "body": json.dumps({"error": f"Unknown action: {action}"}),
                }

    except Exception as e:
        print(f"Error processing event: {str(e)}")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


def assign_premium_user(user_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
    """Assign premium user to available spot fleet instance with dynamic scaling"""

    ec2 = boto3.client("ec2")
    elbv2 = boto3.client("elbv2")

    vpc_id = os.environ["VPC_ID"]
    alb_listener_arn = os.environ["ALB_LISTENER_ARN"]
    premium_instance_ids = os.environ["PREMIUM_INSTANCE_IDS"].split(",")

    try:
        # 1. Find available standby instance or start a stopped one
        available_instance_id = None
        shared_instance_id = None
        stopped_instance_id = None

        # Get instance states
        instances_response = ec2.describe_instances(InstanceIds=premium_instance_ids)

        for reservation in instances_response["Reservations"]:
            for instance in reservation["Instances"]:
                instance_id = instance["InstanceId"]
                instance_state = instance["State"]["Name"]

                if instance_state == "stopped":
                    if not stopped_instance_id:  # Take first available stopped instance
                        stopped_instance_id = instance_id
                elif instance_state == "running":
                    # Check if instance is already assigned using RDS
                    assigned_users = get_assigned_users_for_instance(instance_id)

                    if not assigned_users:  # Instance is available
                        # Check if instance is ready (has running ECS tasks)
                        if check_instance_readiness(instance_id):
                            available_instance_id = instance_id
                            break
                    elif (
                        len(assigned_users) == 1
                    ):  # Instance has one user (can be shared temporarily)
                        shared_instance_id = instance_id

        # Strategy: Use available running instance, start stopped instance, or share
        if available_instance_id:
            instance_id = available_instance_id
            print(
                f"Assigning user {user_id} to available running instance {instance_id}"
            )
        elif stopped_instance_id:
            instance_id = stopped_instance_id
            print(f"Starting stopped instance {instance_id} for user {user_id}")

            # Start the stopped instance
            ec2.start_instances(InstanceIds=[instance_id])

            # Wait for instance to be in running state (may take 1-2 minutes)
            print(f"Waiting for instance {instance_id} to start...")
            waiter = ec2.get_waiter("instance_running")
            waiter.wait(
                InstanceIds=[instance_id], WaiterConfig={"Delay": 10, "MaxAttempts": 12}
            )

            # Wait for ECS tasks to be ready and set up session migration
            time.sleep(30)  # Additional time for ECS tasks to start
            print(f"Instance {instance_id} is running and ready for user {user_id}")

            # Trigger session migration from free tier (if needed)
            trigger_session_migration(user_id, instance_id)

        elif shared_instance_id:
            instance_id = shared_instance_id
            print(
                f"Temporarily assigning user {user_id} to shared instance {instance_id}"
            )
            # This will create a new stopped standby instance later
            create_additional_standby_if_needed()
        else:
            return {
                "statusCode": 503,
                "body": json.dumps(
                    {
                        "error": "No available premium instances. "
                        "All instances are in use.",
                        "message": "Please contact support or try again later.",
                    }
                ),
            }

        # 2. Create target group for the user
        target_group_response = elbv2.create_target_group(
            Name=f"premium-{user_id}-tg",
            Protocol="HTTP",
            Port=8000,
            VpcId=vpc_id,
            HealthCheckPath="/health",
            HealthCheckProtocol="HTTP",
            HealthCheckIntervalSeconds=30,
            HealthyThresholdCount=2,
            UnhealthyThresholdCount=3,
            Tags=[
                {"Key": "UserID", "Value": user_id},
                {"Key": "Type", "Value": "premium-user"},
                {"Key": "Service", "Value": "optinist-premium"},
            ],
        )

        target_group_arn = target_group_response["TargetGroups"][0]["TargetGroupArn"]

        # 3. Register instance to target group
        elbv2.register_targets(
            TargetGroupArn=target_group_arn, Targets=[{"Id": instance_id, "Port": 8000}]
        )

        # 4. Create ALB listener rule for user routing
        rule_response = elbv2.create_rule(
            ListenerArn=alb_listener_arn,
            Priority=10,  # All premium users get same base priority range
            Conditions=[
                {
                    "Field": "http-header",
                    "HttpHeaderConfig": {
                        "HttpHeaderName": "X-User-Tier",
                        "Values": ["premium"],
                    },
                },
                {
                    "Field": "http-header",
                    "HttpHeaderConfig": {
                        "HttpHeaderName": "X-User-ID",
                        "Values": [user_id],
                    },
                },
            ],
            Actions=[{"Type": "forward", "TargetGroupArn": target_group_arn}],
        )

        rule_arn = rule_response["Rules"][0]["RuleArn"]

        # 5. Store assignment in RDS
        store_user_assignment(user_id, instance_id, target_group_arn, rule_arn)

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": f"Premium user {user_id} assigned "
                    f"to instance {instance_id}",
                    "instance_id": instance_id,
                    "target_group_arn": target_group_arn,
                    "rule_arn": rule_arn,
                }
            ),
        }

    except Exception as e:
        print(f"Error assigning premium user: {str(e)}")
        # Cleanup on failure
        try:
            if "target_group_arn" in locals():
                elbv2.delete_target_group(TargetGroupArn=target_group_arn)
        except Exception:
            pass

        raise e


def create_additional_standby_if_needed():
    """Create additional standby instances if needed (placeholder for future scaling)"""
    print("Standby pool management: Current implementation uses fixed instances.")
    print(
        "For dynamic scaling, consider creating additional "
        "instances via Terraform or Lambda."
    )
    return False


def check_instance_readiness(instance_id: str) -> bool:
    """Check if an instance has a running ECS task and is ready for user assignment"""
    ecs = boto3.client("ecs")
    cluster_name = os.environ["CLUSTER_NAME"]

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


def migrate_user_to_dedicated_instance(user_id: str, new_instance_id: str) -> bool:
    """Migrate user from shared instance to dedicated instance"""
    elbv2 = boto3.client("elbv2")

    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                # Get current assignment
                cursor.execute(
                    """SELECT instance_id, target_group_arn, alb_rule_arn
                       FROM premium_user_assignments WHERE user_id = %s""",
                    (user_id,),
                )
                assignment = cursor.fetchone()

                if not assignment:
                    print(f"No assignment found for user {user_id}")
                    return False

                old_instance_id = assignment["instance_id"]
                target_group_arn = assignment["target_group_arn"]

                # Deregister from old instance
                elbv2.deregister_targets(
                    TargetGroupArn=target_group_arn,
                    Targets=[{"Id": old_instance_id, "Port": 8000}],
                )

                # Register to new instance
                elbv2.register_targets(
                    TargetGroupArn=target_group_arn,
                    Targets=[{"Id": new_instance_id, "Port": 8000}],
                )

                # Update RDS assignment
                cursor.execute(
                    """UPDATE premium_user_assignments SET instance_id = %s
                       WHERE user_id = %s""",
                    (new_instance_id, user_id),
                )

                print(
                    f"Migrated user {user_id} from {old_instance_id} to "
                    f"{new_instance_id}"
                )
                return True

    except Exception as e:
        print(f"Error migrating user {user_id}: {str(e)}")
        return False


def release_premium_user(user_id: str) -> Dict[str, Any]:
    """Release premium user from assigned instance"""

    _ = boto3.client("ec2")
    elbv2 = boto3.client("elbv2")

    try:
        # 1. Get assignment from RDS
        assignment = remove_user_assignment(user_id)
        instance_id = assignment["instance_id"]
        target_group_arn = assignment["target_group_arn"]
        rule_arn = assignment["alb_rule_arn"]

        # 2. Delete ALB listener rule
        try:
            elbv2.delete_rule(RuleArn=rule_arn)
            print(f"Deleted ALB rule: {rule_arn}")
        except Exception as rule_error:
            print(f"Error deleting ALB rule: {str(rule_error)}")

        # 3. Delete target group
        try:
            elbv2.delete_target_group(TargetGroupArn=target_group_arn)
            print(f"Deleted target group: {target_group_arn}")
        except Exception as tg_error:
            print(f"Error deleting target group: {str(tg_error)}")

        # 4. Check if we can stop idle instances to save costs
        stop_idle_instances_if_needed()

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": f"Premium user {user_id} released "
                    f"from instance {instance_id}",
                    "released_instance": instance_id,
                }
            ),
        }

    except Exception as e:
        print(f"Error releasing premium user: {str(e)}")
        raise e


def stop_idle_instances_if_needed():
    """Stop idle running instances to save costs while keeping at least 1 standby"""
    ec2 = boto3.client("ec2")
    premium_instance_ids = os.environ["PREMIUM_INSTANCE_IDS"].split(",")

    try:
        # Get current instance states
        instances_response = ec2.describe_instances(InstanceIds=premium_instance_ids)

        running_instances = []
        stopped_instances = 0

        for reservation in instances_response["Reservations"]:
            for instance in reservation["Instances"]:
                instance_id = instance["InstanceId"]
                instance_state = instance["State"]["Name"]

                if instance_state == "running":
                    assigned_users = get_assigned_users_for_instance(instance_id)
                    running_instances.append(
                        {
                            "instance_id": instance_id,
                            "assigned_users": len(assigned_users),
                        }
                    )
                elif instance_state == "stopped":
                    stopped_instances += 1

        # True standby pool: Stop ALL idle instances when no users present
        idle_instances = [
            inst for inst in running_instances if inst["assigned_users"] == 0
        ]

        # Stop ALL idle instances to achieve true standby pool behavior
        if len(idle_instances) > 0:
            print(
                f"Found {len(idle_instances)} idle premium instances, "
                f"stopping all to save costs"
            )
            for inst in idle_instances:
                print(f"Stopping idle instance {inst['instance_id']} to save costs")
                ec2.stop_instances(InstanceIds=[inst["instance_id"]])

                # Update database to mark as standby
                update_instance_as_standby(inst["instance_id"])
        else:
            print("No idle premium instances found, all instances have assigned users")

    except Exception as e:
        print(f"Error stopping idle instances: {str(e)}")


def update_instance_as_standby(instance_id: str):
    """Mark an instance as standby in the database"""
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO premium_user_assignments
                       (user_id, instance_id, target_group_arn, alb_rule_arn,
                        is_standby, standby_created_at, instance_state)
                       VALUES (%s, %s, %s, %s, %s, NOW(), %s)
                       ON DUPLICATE KEY UPDATE
                       is_standby = 1, standby_created_at = NOW(),
                       instance_state = 'launching'
                    """,
                    ("STANDBY", instance_id, "STANDBY", "STANDBY", 1, "launching"),
                )
        print(f"Marked instance {instance_id} as standby in database")
    except Exception as e:
        print(f"Error updating instance as standby: {str(e)}")


def trigger_session_migration(user_id: str, instance_id: str):
    """Trigger session migration from free tier to premium instance"""
    try:
        # Update database to mark migration as ready
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """UPDATE premium_user_assignments
                       SET instance_state = 'running', migration_ready = TRUE
                       WHERE user_id = %s AND instance_id = %s""",
                    (user_id, instance_id),
                )

        print(f"Session migration ready for user {user_id} on instance {instance_id}")

        # Note: The actual session data migration is handled by the frontend
        # migration service that monitors the migration_ready flag in the API

    except Exception as e:
        print(f"Error triggering session migration: {str(e)}")


def get_standby_pool_status() -> Dict[str, Any]:
    """Get detailed status of the premium standby pool"""
    try:
        ec2 = boto3.client("ec2")
        premium_instance_ids = os.environ["PREMIUM_INSTANCE_IDS"].split(",")

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

        # Note: In a production system, you might want to:
        # 1. Launch replacement instances for failed ones
        # 2. Terminate old failed instances
        # 3. Update Terraform state accordingly
        # For now, we'll just log and monitor

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


def process_migration_queue(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Comprehensive premium instance management:
    1. Check for users on shared instances and migrate to dedicated instances
    2. Proactively stop idle instances to maintain true standby pool
    3. Monitor instance health and maintain standby pool capacity
    """

    print(f"Migration queue check triggered by event: {json.dumps(event)}")
    print(f"Lambda context: {context.function_name if context else 'No context'}")

    try:
        ec2 = boto3.client("ec2")
        premium_instance_ids = os.environ["PREMIUM_INSTANCE_IDS"].split(",")

        # Get all premium instances with detailed state information
        instances_response = ec2.describe_instances(InstanceIds=premium_instance_ids)

        available_instances = []
        shared_instances = []
        idle_instances = []
        total_running = 0
        total_stopped = 0
        total_failed = 0

        for reservation in instances_response["Reservations"]:
            for instance in reservation["Instances"]:
                instance_id = instance["InstanceId"]
                instance_state = instance["State"]["Name"]

                if instance_state == "running":
                    total_running += 1
                    assigned_users = get_assigned_users_for_instance(instance_id)

                    if not assigned_users:
                        if check_instance_readiness(instance_id):
                            available_instances.append(instance_id)
                            idle_instances.append(instance_id)
                        else:
                            print(
                                f"Instance {instance_id} running but "
                                f"not ready, marking as failed"
                            )
                            total_failed += 1
                    elif len(assigned_users) > 1:  # Shared instance
                        shared_instances.append((instance_id, assigned_users))

                elif instance_state == "stopped":
                    total_stopped += 1
                elif instance_state in ["terminated", "terminating", "stopping"]:
                    total_failed += 1

        # 1. Proactive idle cleanup: Stop idle instances to maintain standby pool
        instances_stopped = 0
        if idle_instances:
            print(
                f"Found {len(idle_instances)} idle running instances, "
                f"stopping to maintain standby pool"
            )
            for instance_id in idle_instances:
                print(f"Stopping idle instance {instance_id} for cost optimization")
                ec2.stop_instances(InstanceIds=[instance_id])
                update_instance_as_standby(instance_id)
                instances_stopped += 1

        # 2. Migrate users from shared instances to dedicated instances
        migrations_performed = 0
        remaining_available = [
            inst for inst in available_instances if inst not in idle_instances
        ]

        for instance_id, users in shared_instances:
            if not remaining_available:
                break

            # Migrate all but one user from shared instance
            users_to_migrate = users[1:]  # Keep first user, migrate others

            for user_id in users_to_migrate:
                if not remaining_available:
                    break

                new_instance_id = remaining_available.pop(0)
                if migrate_user_to_dedicated_instance(user_id, new_instance_id):
                    migrations_performed += 1
                    print(
                        f"Migrated user {user_id} to dedicated instance "
                        f"{new_instance_id}"
                    )
                else:
                    # Return instance to available list if migration failed
                    remaining_available.append(new_instance_id)

        # 3. Health check and capacity management
        capacity_check = ensure_standby_pool_capacity()
        capacity_warnings = []

        if not capacity_check["success"]:
            capacity_warnings.append(
                f"Capacity check failed: {capacity_check.get('error', 'Unknown error')}"
            )
        else:
            capacity_warnings.extend(capacity_check.get("actions_taken", []))

        # Log comprehensive status
        print(
            f"Premium instance status: {total_running} running, "
            f"{total_stopped} stopped, {total_failed} failed"
        )
        print(
            f"Operations: {instances_stopped} stopped, {migrations_performed} migrated"
        )

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": f"Premium management complete. Stopped "
                    f"{instances_stopped} idle instances, performed "
                    f"{migrations_performed} migrations.",
                    "operations": {
                        "instances_stopped": instances_stopped,
                        "migrations_performed": migrations_performed,
                    },
                    "instance_status": {
                        "running": total_running - instances_stopped,  # After cleanup
                        "stopped": total_stopped + instances_stopped,
                        "failed": total_failed,
                        "available_for_assignment": len(remaining_available),
                        "shared_instances": len(shared_instances),
                    },
                    "capacity_warnings": capacity_warnings,
                }
            ),
        }

    except Exception as e:
        print(f"Error processing migration queue: {str(e)}")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
