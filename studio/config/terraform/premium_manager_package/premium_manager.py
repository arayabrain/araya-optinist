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
    user_id: str,
    instance_id: str,
    target_group_arn: str,
    rule_arn: str,
    instance_state: str = "launching",
    is_shared: bool = False,
):
    """Store user assignment in RDS with transaction isolation"""
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                # Use SELECT FOR UPDATE to prevent race conditions
                cursor.execute("START TRANSACTION")

                # Check if user already has assignment with lock
                cursor.execute(
                    """SELECT user_id FROM premium_user_assignments
                       WHERE user_id = %s FOR UPDATE""",
                    (user_id,),
                )
                existing = cursor.fetchone()

                if existing:
                    cursor.execute("ROLLBACK")
                    raise Exception(f"User {user_id} already has a premium assignment")

                # Insert new assignment with enhanced tracking
                cursor.execute(
                    """
                    INSERT INTO premium_user_assignments
                    (user_id, instance_id, target_group_arn, alb_rule_arn, status,
                     instance_state, is_shared, assignment_attempts, last_state_check)
                    VALUES (%s, %s, %s, %s, 'active', %s, %s, 1, NOW())
                """,
                    (
                        user_id,
                        instance_id,
                        target_group_arn,
                        rule_arn,
                        instance_state,
                        is_shared,
                    ),
                )

                cursor.execute("COMMIT")

        print(
            f"Stored assignment in RDS: user {user_id} -> instance {instance_id} "
            f"(state: {instance_state}, shared: {is_shared})"
        )
    except Exception as e:
        print(f"Error storing assignment in RDS: {str(e)}")
        # Ensure rollback on any error
        try:
            with get_db_connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("ROLLBACK")
        except Exception as e2:
            print(f"Error during rollback: {str(e2)}")
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
                    """SELECT user_id, is_shared, instance_state
                       FROM premium_user_assignments
                       WHERE instance_id = %s AND status = 'active'""",
                    (instance_id,),
                )
                users = cursor.fetchall()
                return users
    except Exception as e:
        print(f"Error getting assigned users: {str(e)}")
        return []


def get_all_premium_instances_with_states():
    """Get all premium instances with their AWS states"""
    ec2 = boto3.client("ec2")
    try:
        # Get instances with premium tag
        response = ec2.describe_instances(
            Filters=[
                {"Name": "tag:Name", "Values": ["*premium*"]},
                {
                    "Name": "instance-state-name",
                    "Values": ["pending", "running", "stopping", "stopped"],
                },
            ]
        )

        instances = []
        for reservation in response["Reservations"]:
            for instance in reservation["Instances"]:
                instances.append(
                    {
                        "instance_id": instance["InstanceId"],
                        "instance_type": instance["InstanceType"],
                        "state": instance["State"]["Name"],
                        "launch_time": instance.get("LaunchTime"),
                    }
                )

        return instances
    except Exception as e:
        print(f"Error getting premium instances: {str(e)}")
        return []


def count_active_premium_users():
    """Count users with active premium assignments"""
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) as count FROM premium_user_assignments "
                    "WHERE status = 'active'"
                )
                result = cursor.fetchone()
                return result["count"] if result else 0
    except Exception as e:
        print(f"Error counting active premium users: {str(e)}")
        return 0


def count_total_premium_users():
    """Count total premium users in the database (regardless of assignment status)"""
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                # Query for users with premium subscription
                cursor.execute(
                    """SELECT COUNT(*) as count FROM users
                       WHERE subscription_type = 'premium'
                       AND subscription_status IN ('active', 'trialing')"""
                )
                result = cursor.fetchone()
                total_premium = result["count"] if result else 0

                print(f"Total premium users in database: {total_premium}")
                return total_premium
    except Exception as e:
        print(f"Error counting total premium users: {str(e)}")
        # Fallback to a reasonable default if we can't query the database
        return 5


def get_dynamic_max_capacity():
    """Get dynamic maximum capacity based on premium users with safety buffer"""
    total_premium_users = count_total_premium_users()

    # Configuration - use existing environment variables where possible
    SAFETY_BUFFER = int(
        os.environ.get("PREMIUM_SAFETY_BUFFER", "1")
    )  # Extra instances for quick response
    ABSOLUTE_MAX = int(os.environ.get("ABSOLUTE_MAX", "20"))  # Use existing variable
    STANDBY_POOL_SIZE = int(
        os.environ.get("PREMIUM_STANDBY_POOL_SIZE", "1")
    )  # New: stopped instances ready

    # Get current standby count from existing table
    standby_count = get_standby_count()

    # Calculate dynamic max capacity including standby pool
    if total_premium_users == 0:
        max_capacity = 1 + STANDBY_POOL_SIZE  # Minimum 1 running + standby
    else:
        # Running instances needed: total_premium_users + SAFETY_BUFFER
        # Total capacity: running_needed + STANDBY_POOL_SIZE
        running_needed = total_premium_users + SAFETY_BUFFER
        total_needed = running_needed + STANDBY_POOL_SIZE
        max_capacity = min(total_needed, ABSOLUTE_MAX)

    print(
        f"Dynamic capacity calculation: {total_premium_users} premium users + "
        f"{SAFETY_BUFFER} buffer + {STANDBY_POOL_SIZE} standby = {max_capacity} total "
        f"(current standby: {standby_count}, max: {ABSOLUTE_MAX})"
    )

    return max_capacity


def update_instance_state(user_id: str, new_state: str):
    """Update instance state for a user assignment"""
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """UPDATE premium_user_assignments
                       SET instance_state = %s, last_state_check = NOW()
                       WHERE user_id = %s""",
                    (new_state, user_id),
                )
        print(f"Updated instance state for user {user_id} to {new_state}")
    except Exception as e:
        print(f"Error updating instance state: {str(e)}")


# ===== SIMPLIFIED STANDBY POOL FUNCTIONS =====


def get_standby_count():
    """Get count of standby instances from existing assignments table"""
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT COUNT(*) as count FROM premium_user_assignments
                       WHERE is_standby = 1 AND status = 'active'"""
                )
                result = cursor.fetchone()
                return result["count"] if result else 0
    except Exception as e:
        print(f"Error getting standby count: {str(e)}")
        return 0


def get_available_standby_instances():
    """Get available stopped standby instances"""
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT instance_id, standby_created_at
                        FROM premium_user_assignments
                       WHERE is_standby = 1 AND status = 'active'
                       AND instance_state = 'stopped'
                       ORDER BY standby_created_at ASC
                       """
                )
                return cursor.fetchall()
    except Exception as e:
        print(f"Error getting available standby instances: {str(e)}")
        return []


def create_and_stop_standby_instance():
    """Create instance and immediately stop it for standby use"""
    ec2 = boto3.client("ec2")

    try:
        # Get launch template from spot fleet config
        spot_fleet_id = os.environ["SPOT_FLEET_ID"]
        spot_fleet_response = ec2.describe_spot_fleet_requests(
            SpotFleetRequestIds=[spot_fleet_id]
        )

        # Get launch template configuration
        launch_template_config = spot_fleet_response["SpotFleetRequestConfigs"][0][
            "SpotFleetRequestConfig"
        ]["LaunchTemplateConfigs"][0]

        launch_template_spec = launch_template_config["LaunchTemplateSpecification"]
        overrides = launch_template_config.get("Overrides", [{}])[0]

        # Launch instance
        response = ec2.run_instances(
            LaunchTemplate={
                "LaunchTemplateId": launch_template_spec["Id"],
                "Version": launch_template_spec["Version"],
            },
            InstanceType=overrides.get("InstanceType", "t3.large"),
            SubnetId=overrides.get("SubnetId"),
            MinCount=1,
            MaxCount=1,
            TagSpecifications=[
                {
                    "ResourceType": "instance",
                    "Tags": [
                        {"Key": "Name", "Value": "subscr-optinist-premium-standby"},
                        {"Key": "Type", "Value": "premium-standby"},
                        {"Key": "Service", "Value": "optinist-premium"},
                    ],
                }
            ],
        )

        instance_id = response["Instances"][0]["InstanceId"]
        print(f"Created standby instance {instance_id}, waiting to stop...")

        # Wait for running then stop
        waiter = ec2.get_waiter("instance_running")
        waiter.wait(
            InstanceIds=[instance_id], WaiterConfig={"Delay": 15, "MaxAttempts": 40}
        )

        ec2.stop_instances(InstanceIds=[instance_id])

        waiter = ec2.get_waiter("instance_stopped")
        waiter.wait(
            InstanceIds=[instance_id], WaiterConfig={"Delay": 15, "MaxAttempts": 20}
        )

        # Store in assignments table as standby
        store_user_assignment(
            user_id=f"standby-{instance_id}",
            instance_id=instance_id,
            target_group_arn="standby",
            rule_arn="standby",
            instance_state="stopped",
            is_shared=False,
        )

        # Mark as standby
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """UPDATE premium_user_assignments
                       SET is_standby = 1, standby_created_at = NOW()
                       WHERE instance_id = %s""",
                    (instance_id,),
                )

        print(f"Successfully created and stopped standby instance {instance_id}")
        return instance_id

    except Exception as e:
        print(f"Error creating standby instance: {str(e)}")
        return None


def start_standby_instance(instance_id: str):
    """Start a stopped standby instance and prepare for user assignment"""
    ec2 = boto3.client("ec2")

    try:
        print(f"Starting standby instance {instance_id}")

        # Start the instance
        ec2.start_instances(InstanceIds=[instance_id])

        # Wait for running state
        waiter = ec2.get_waiter("instance_running")
        waiter.wait(
            InstanceIds=[instance_id],
            WaiterConfig={"Delay": 5, "MaxAttempts": 24},  # 2 minutes max
        )

        # Update state in database
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """UPDATE premium_user_assignments
                       SET instance_state = 'running', is_standby = 0
                       WHERE instance_id = %s""",
                    (instance_id,),
                )

        print(f"Standby instance {instance_id} started successfully")
        return True

    except Exception as e:
        print(f"Error starting standby instance {instance_id}: {str(e)}")
        return False


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
    """Enhanced assignment with standby pool support -
    prefer stopped instances for fast startup"""

    ec2 = boto3.client("ec2")
    elbv2 = boto3.client("elbv2")

    vpc_id = os.environ["VPC_ID"]
    alb_listener_arn = os.environ["ALB_LISTENER_ARN"]

    try:
        # 1. Get comprehensive instance state information
        all_instances = get_all_premium_instances_with_states()
        running_instances = [i for i in all_instances if i["state"] == "running"]
        launching_instances = [i for i in all_instances if i["state"] in ["pending"]]
        active_users = count_active_premium_users()

        # Get standby pool status
        standby_instances = get_available_standby_instances()
        standby_count = len(standby_instances)

        print(
            f"Assignment context: {len(running_instances)} running, "
            f"{len(launching_instances)} launching, {active_users} users, "
            f"{standby_count} standby available"
        )

        # 2. PRIORITY 1: Available running instances (immediate assignment)
        available_dedicated = None
        least_loaded_instance = None
        min_users = float("inf")

        for instance in running_instances:
            instance_id = instance["instance_id"]

            # Skip if not ready
            if not check_instance_readiness(instance_id):
                continue

            assigned_users = get_assigned_users_for_instance(instance_id)
            user_count = len(assigned_users)

            if user_count == 0:
                # Found dedicated instance
                available_dedicated = instance
                break
            elif user_count < min_users:
                # Track least loaded for sharing
                least_loaded_instance = instance
                min_users = user_count

        # 3. PRIORITY 2: Start standby instance (5-15 second assignment)
        if not available_dedicated and standby_instances:
            print("No dedicated instances available, starting standby instance")

            # Use oldest standby instance
            standby_to_start = standby_instances[0]
            standby_instance_id = standby_to_start["instance_id"]

            # Start the standby instance
            if start_standby_instance(standby_instance_id):
                # Create replacement standby instance asynchronously
                print("Creating replacement standby instance")
                maintain_standby_pool()  # This will create a new standby

                # Proceed with assignment to the started instance
                instance_to_use = {"instance_id": standby_instance_id}
                is_shared = False
                instance_state = "running"
                assignment_source = "standby"

                print(
                    f"Assigning user {user_id} to started standby "
                    f"instance {standby_instance_id}"
                )
            else:
                print(
                    f"Failed to start standby instance {standby_instance_id}, "
                    f"falling back to other options"
                )
                available_dedicated = None  # Force fallback logic

        # 4. PRIORITY 3: Use existing running instances with sharing
        if not available_dedicated and not ("instance_to_use" in locals()):
            if least_loaded_instance and len(running_instances) < active_users + 1:
                # Temporary sharing during scale-up
                instance_to_use = least_loaded_instance
                is_shared = True
                instance_state = "running"
                assignment_source = "shared"
                print(
                    f"Temporarily sharing instance "
                    f"{instance_to_use['instance_id']} for user {user_id}"
                )

                # Trigger scaling if no instances are launching
                # and no standby being created
                if len(launching_instances) == 0:
                    scaled = scale_spot_fleet_if_needed()
                    if scaled:
                        print("Triggered spot fleet scaling for dedicated instance")

        # 5. PRIORITY 4: Use dedicated running instance (best case)
        if available_dedicated and not ("instance_to_use" in locals()):
            instance_to_use = available_dedicated
            is_shared = False
            instance_state = "running"
            assignment_source = "dedicated"
            print(
                f"Assigning user {user_id} to dedicated instance "
                f"{instance_to_use['instance_id']}"
            )

        # 6. PRIORITY 5: Scale up (last resort)
        if not ("instance_to_use" in locals()):
            if len(launching_instances) > 0:
                # Already scaling
                return {
                    "statusCode": 202,
                    "body": json.dumps(
                        {
                            "message": f"Premium capacity scaling in progress "
                            f"({len(launching_instances)} instances launching). "
                            f"Please retry in 2-3 minutes.",
                            "retry_after": 180,
                        }
                    ),
                }
            else:
                # Try to scale
                scaled = scale_spot_fleet_if_needed()
                if scaled:
                    return {
                        "statusCode": 202,
                        "body": json.dumps(
                            {
                                "message": "Scaling premium capacity. "
                                "Please retry in 2-3 minutes.",
                                "retry_after": 180,
                            }
                        ),
                    }
                else:
                    return {
                        "statusCode": 503,
                        "body": json.dumps(
                            {
                                "error": "No available premium instances "
                                "and cannot scale further"
                            }
                        ),
                    }

        # 7. Create target group for the user
        instance_id = instance_to_use["instance_id"]
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
                {"Key": "Shared", "Value": str(is_shared)},
                {"Key": "Source", "Value": assignment_source},
            ],
        )

        target_group_arn = target_group_response["TargetGroups"][0]["TargetGroupArn"]

        # 8. Register instance to target group
        elbv2.register_targets(
            TargetGroupArn=target_group_arn, Targets=[{"Id": instance_id, "Port": 8000}]
        )

        # 9. Create ALB listener rule for user routing
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

        # 10. Store assignment in RDS with state tracking
        store_user_assignment(
            user_id, instance_id, target_group_arn, rule_arn, instance_state, is_shared
        )

        # If this was a standby instance, clean up the dummy standby assignment
        if "assignment_source" in locals() and assignment_source == "standby":
            with get_db_connection() as connection:
                with connection.cursor() as cursor:
                    # Remove the dummy standby assignment
                    cursor.execute(
                        "DELETE FROM premium_user_assignments WHERE user_id = %s",
                        (f"standby-{instance_id}",),
                    )

        # 11. Create replacement standby if needed
        if get_standby_count() < int(os.environ.get("PREMIUM_STANDBY_POOL_SIZE", "1")):
            create_and_stop_standby_instance()

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": f"Premium user {user_id} "
                    f"assigned to instance {instance_id} "
                    f"({assignment_source}{' shared' if is_shared else ''})",
                    "instance_id": instance_id,
                    "target_group_arn": target_group_arn,
                    "rule_arn": rule_arn,
                    "is_shared": is_shared,
                    "assignment_source": assignment_source,
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


def scale_spot_fleet_if_needed():
    """Scale up spot fleet with dynamic max capacity based on premium users"""
    ec2 = boto3.client("ec2")
    spot_fleet_id = os.environ["SPOT_FLEET_ID"]

    try:
        # Get dynamic capacity limits based on premium users
        max_capacity = get_dynamic_max_capacity()

        # Get all premium instances (not just spot fleet) with their states
        all_instances = get_all_premium_instances_with_states()
        active_users = count_active_premium_users()

        running_instances = [i for i in all_instances if i["state"] == "running"]
        launching_instances = [
            i for i in all_instances if i["state"] in ["pending", "launching"]
        ]

        running_count = len(running_instances)
        launching_count = len(launching_instances)
        total_instances = len(all_instances)

        print(
            f"Enhanced spot fleet status: {running_count} running, "
            f"{launching_count} launching, {total_instances} total, "
            f"{active_users} active users, max_capacity={max_capacity}"
        )

        # NO SCALING CONDITIONS:
        if launching_count > 0:
            print(f"Scaling blocked: {launching_count} instances already launching")
            return False

        if running_count >= active_users:
            print(
                f"Scaling not needed: {running_count} running >= "
                f"{active_users} active users"
            )
            return False

        # Get current spot fleet capacity
        spot_fleet_response = ec2.describe_spot_fleet_requests(
            SpotFleetRequestIds=[spot_fleet_id]
        )
        current_capacity = spot_fleet_response["SpotFleetRequestConfigs"][0][
            "SpotFleetRequestConfig"
        ]["TargetCapacity"]

        # SCALE UP CONDITIONS:
        needed_capacity = active_users - running_count

        if needed_capacity > 0 and current_capacity < max_capacity:
            # Scale conservatively - only add what's needed
            new_capacity = min(current_capacity + needed_capacity, max_capacity)

            print(
                f"Scaling spot fleet from {current_capacity} to {new_capacity} "
                f"(need {needed_capacity} more instances, max allowed: {max_capacity})"
            )

            ec2.modify_spot_fleet_request(
                SpotFleetRequestId=spot_fleet_id, TargetCapacity=new_capacity
            )

            return True

        elif current_capacity >= max_capacity:
            print(
                f"Scaling blocked: already at maximum capacity "
                f"({current_capacity}/{max_capacity})"
            )
            return False

        print(f"No scaling needed: capacity={current_capacity}, max={max_capacity}")
        return False

    except Exception as e:
        print(f"Error scaling spot fleet: {str(e)}")
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

        # 4. Check if we can scale down the spot fleet and convert idle instances
        scale_down_if_possible()

        # 5. Immediately convert idle instances to standby if no premium users are left
        active_users = count_active_premium_users()
        if active_users == 0:
            print(
                "No premium users remaining, converting idle "
                "instances to standby immediately"
            )
            converted_count = cleanup_idle_running_instances(
                idle_timeout_hours=0
            )  # No timeout - immediate conversion
            if converted_count > 0:
                print(
                    f"Immediately converted {converted_count} idle instances "
                    f"to standby after user logout"
                )

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


def scale_down_if_possible():
    """Scale down spot fleet if there are idle instances, respecting dynamic capacity"""
    ec2 = boto3.client("ec2")
    spot_fleet_id = os.environ["SPOT_FLEET_ID"]

    try:
        # Get dynamic capacity settings
        max_capacity = get_dynamic_max_capacity()
        active_users = count_active_premium_users()
        total_premium_users = count_total_premium_users()

        spot_instances_response = ec2.describe_spot_fleet_instances(
            SpotFleetRequestId=spot_fleet_id
        )

        total_instances = len(spot_instances_response["ActiveInstances"])
        occupied_instances = 0

        # Count occupied instances
        for instance in spot_instances_response["ActiveInstances"]:
            instance_id = instance["InstanceId"]
            assigned_users = get_assigned_users_for_instance(instance_id)
            if assigned_users:
                occupied_instances += 1

        idle_instances = total_instances - occupied_instances

        print(
            f"Scale-down analysis: {total_instances} total, "
            f"{occupied_instances} occupied, "
            f"{idle_instances} idle, {active_users} active users, "
            f"{total_premium_users} total premium users"
            f" (max capacity: {max_capacity})"
        )

        # Conservative scale-down logic:
        # - Keep at least 1 instance always
        # - Keep enough capacity for quick assignment (active users + 1)
        # - Only scale down if we have significantly more than needed
        min_needed = max(1, active_users + 1)  # Active users + 1 for quick response

        if total_instances > min_needed and idle_instances >= 2:
            # Scale down conservatively
            new_capacity = max(min_needed, occupied_instances + 1)

            print(
                f"Scaling spot fleet down from {total_instances} to {new_capacity} "
                f"(min needed: {min_needed})"
            )

            ec2.modify_spot_fleet_request(
                SpotFleetRequestId=spot_fleet_id, TargetCapacity=new_capacity
            )

        else:
            print(
                f"No scale-down: total={total_instances}, min_needed={min_needed}, "
                f"idle={idle_instances}"
            )

    except Exception as e:
        print(f"Error scaling down spot fleet: {str(e)}")


def reconcile_instance_states() -> Dict[str, Any]:
    """
    Reconcile database instance states with actual AWS instance states
    """
    try:
        # Get all instances from AWS
        aws_instances = get_all_premium_instances_with_states()
        aws_instance_map = {i["instance_id"]: i for i in aws_instances}

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

                    elif aws_instance["state"] != db_state:
                        # Update database state to match AWS
                        aws_state = aws_instance["state"]
                        print(
                            f"Updating instance state for user {user_id}: "
                            f"{db_state} -> {aws_state}"
                        )
                        cursor.execute(
                            """UPDATE premium_user_assignments
                               SET instance_state = %s, last_state_check = NOW()
                               WHERE user_id = %s""",
                            (aws_state, user_id),
                        )
                        update_count += 1

        return {
            "cleanup_count": cleanup_count,
            "update_count": update_count,
            "total_aws_instances": len(aws_instances),
            "total_db_assignments": len(db_assignments),
        }

    except Exception as e:
        print(f"Error reconciling instance states: {str(e)}")
        return {"error": str(e)}


def get_standby_pool_count():
    """Get count of standby instances by status"""
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT instance_state, COUNT(*) as count
                       FROM premium_user_assignments
                       WHERE is_standby = 1 AND status = 'active'
                       GROUP BY instance_state"""
                )
                results = cursor.fetchall()

                # Convert to dictionary with default values
                status_counts = {
                    "stopped": 0,
                    "running": 0,
                    "pending": 0,
                    "stopping": 0,
                    "starting": 0,
                }
                for result in results:
                    status_counts[result["instance_state"]] = result["count"]

                return status_counts
    except Exception as e:
        print(f"Error getting standby pool count: {str(e)}")
        return {"stopped": 0, "running": 0, "pending": 0, "stopping": 0, "starting": 0}


def maintain_standby_pool():
    """Maintain the correct number of standby instances and
    clean up idle running instances"""
    try:
        required_standby_size = int(os.environ.get("PREMIUM_STANDBY_POOL_SIZE", "1"))
        idle_timeout_hours = int(os.environ.get("PREMIUM_IDLE_TIMEOUT_HOURS", "3"))

        print(
            f"Maintaining standby pool: required={required_standby_size}, "
            f"idle_timeout={idle_timeout_hours}h"
        )

        # 1. Clean up idle running instances first
        cleanup_idle_running_instances(idle_timeout_hours)

        # 2. Get current standby pool status
        standby_instances = get_available_standby_instances()
        current_standby_count = len(standby_instances)

        # 3. Maintain required standby pool size
        if current_standby_count < required_standby_size:
            # Create additional standby instances
            needed = required_standby_size - current_standby_count
            created_count = 0

            for i in range(needed):
                instance_id = create_and_stop_standby_instance()
                if instance_id:
                    created_count += 1
                    print(
                        f"Created standby instance {instance_id} "
                        f"({created_count}/{needed})"
                    )
                else:
                    print(f"Failed to create standby instance {i+1}/{needed}")
                    break

            print(
                f"Standby pool maintenance: created {created_count} "
                f"new standby instances"
            )

        elif current_standby_count > required_standby_size:
            # Remove excess standby instances (terminate oldest ones)
            excess = current_standby_count - required_standby_size
            removed_count = cleanup_excess_standby_instances(excess)
            print(
                f"Standby pool maintenance: removed {removed_count} "
                f"excess standby instances"
            )

        else:
            print(
                f"Standby pool is at correct size: {current_standby_count}"
                f"/{required_standby_size}"
            )

        # 4. Clean up failed/terminated standby instances from database
        cleanup_failed_standby_instances()

        return True

    except Exception as e:
        print(f"Error maintaining standby pool: {str(e)}")
        return False


def cleanup_idle_running_instances(idle_timeout_hours: int = 3):
    """Convert idle running instances to standby or terminate them"""
    cleanup_count = 0

    try:
        # Get all running premium instances
        all_instances = get_all_premium_instances_with_states()
        running_instances = [i for i in all_instances if i["state"] == "running"]

        current_time = time.time()

        for instance in running_instances:
            instance_id = instance["instance_id"]

            # Check if instance has any assigned users
            assigned_users = get_assigned_users_for_instance(instance_id)

            if len(assigned_users) == 0:
                # Instance is idle
                if idle_timeout_hours == 0:
                    # Immediate conversion (user logout scenario)
                    print(
                        f"Converting idle instance {instance_id} to standby "
                        f"(immediate - no premium users)"
                    )
                    if convert_running_instance_to_standby(instance_id):
                        cleanup_count += 1
                else:
                    # Time-based conversion (scheduled maintenance)
                    launch_time = instance.get("launch_time")
                    if launch_time:
                        # Convert launch time to timestamp for comparison
                        if hasattr(launch_time, "timestamp"):
                            launch_timestamp = launch_time.timestamp()
                        else:
                            # If it's already a timestamp or string,
                            # handle appropriately
                            launch_timestamp = current_time - (
                                idle_timeout_hours * 3600
                            )  # Default to timeout

                        idle_duration_hours = (current_time - launch_timestamp) / 3600

                        if idle_duration_hours >= idle_timeout_hours:
                            print(
                                f"Converting idle instance {instance_id} to standby "
                                f"(idle for {idle_duration_hours:.1f}h)"
                            )

                            # Convert to standby instance
                            if convert_running_instance_to_standby(instance_id):
                                cleanup_count += 1

        print(f"Cleaned up {cleanup_count} idle running instances")
        return cleanup_count

    except Exception as e:
        print(f"Error cleaning up idle instances: {str(e)}")
        return 0


def convert_running_instance_to_standby(instance_id: str):
    """Convert a running instance with no users to a standby instance"""
    ec2 = boto3.client("ec2")

    try:
        # Stop the instance
        print(f"Stopping instance {instance_id} to convert to standby")
        ec2.stop_instances(InstanceIds=[instance_id])

        # Wait for it to stop
        waiter = ec2.get_waiter("instance_stopped")
        waiter.wait(
            InstanceIds=[instance_id], WaiterConfig={"Delay": 15, "MaxAttempts": 20}
        )

        # Add to standby pool in database
        store_user_assignment(
            user_id=f"standby-{instance_id}",
            instance_id=instance_id,
            target_group_arn="standby",
            rule_arn="standby",
            instance_state="stopped",
            is_shared=False,
        )

        # Mark as standby
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """UPDATE premium_user_assignments
                       SET is_standby = 1, standby_created_at = NOW()
                       WHERE instance_id = %s""",
                    (instance_id,),
                )

        print(f"Successfully converted instance {instance_id} to standby")
        return True

    except Exception as e:
        print(f"Error converting instance {instance_id} to standby: {str(e)}")
        return False


def cleanup_excess_standby_instances(excess_count: int):
    """Remove excess standby instances (terminate oldest ones)"""
    removed_count = 0

    try:
        # Get all standby instances ordered by creation time (oldest first)
        standby_instances = get_available_standby_instances()

        # Terminate the oldest excess instances
        for i in range(min(excess_count, len(standby_instances))):
            instance_data = standby_instances[i]
            instance_id = instance_data["instance_id"]

            if terminate_standby_instance(instance_id):
                removed_count += 1

        return removed_count

    except Exception as e:
        print(f"Error cleaning up excess standby instances: {str(e)}")
        return 0


def terminate_standby_instance(instance_id: str):
    """Terminate a standby instance and clean up database entry"""
    ec2 = boto3.client("ec2")

    try:
        print(f"Terminating excess standby instance {instance_id}")

        # Terminate the instance
        ec2.terminate_instances(InstanceIds=[instance_id])

        # Remove from database
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM premium_user_assignments "
                    "WHERE instance_id = %s AND is_standby = 1",
                    (instance_id,),
                )

        print(f"Successfully terminated standby instance {instance_id}")
        return True

    except Exception as e:
        print(f"Error terminating standby instance {instance_id}: {str(e)}")
        return False


def cleanup_failed_standby_instances():
    """Clean up database entries for standby instances that no longer exist in AWS"""
    try:
        # Get all AWS premium instances
        aws_instances = get_all_premium_instances_with_states()
        aws_instance_ids = {i["instance_id"] for i in aws_instances}

        # Get all standby assignments from database
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT instance_id FROM premium_user_assignments
                       WHERE is_standby = 1 AND status = 'active'"""
                )
                db_standby_instances = [row["instance_id"] for row in cursor.fetchall()]

                # Remove database entries for instances that no longer exist
                cleanup_count = 0
                for instance_id in db_standby_instances:
                    if instance_id not in aws_instance_ids:
                        print(
                            f"Cleaning up database entry for terminated "
                            f"standby instance {instance_id}"
                        )
                        cursor.execute(
                            "DELETE FROM premium_user_assignments "
                            "WHERE instance_id = %s AND is_standby = 1",
                            (instance_id,),
                        )
                        cleanup_count += 1

                if cleanup_count > 0:
                    print(f"Cleaned up {cleanup_count} failed standby instance entries")

    except Exception as e:
        print(f"Error cleaning up failed standby instances: {str(e)}")


def get_premium_system_status() -> Dict[str, Any]:
    """Get comprehensive status of premium system including standby pool"""
    try:
        # Get instance states
        all_instances = get_all_premium_instances_with_states()
        running_count = len([i for i in all_instances if i["state"] == "running"])
        launching_count = len(
            [i for i in all_instances if i["state"] in ["pending", "launching"]]
        )

        # Get user counts
        total_premium_users = count_total_premium_users()
        active_users = count_active_premium_users()

        # Get standby pool status
        standby_instances = get_available_standby_instances()
        standby_count = len(standby_instances)
        standby_pool_counts = get_standby_pool_count()

        # Get capacity calculations
        max_capacity = get_dynamic_max_capacity()

        return {
            "instances": {
                "running": running_count,
                "launching": launching_count,
                "total": len(all_instances),
            },
            "users": {
                "total_premium": total_premium_users,
                "active": active_users,
            },
            "standby_pool": {
                "available": standby_count,
                "by_status": standby_pool_counts,
                "required_size": int(os.environ.get("PREMIUM_STANDBY_POOL_SIZE", "1")),
            },
            "capacity": {
                "max_capacity": max_capacity,
                "current_utilization": f"{active_users}/{max_capacity}",
            },
            "timestamp": time.time(),
        }

    except Exception as e:
        print(f"Error getting premium system status: {str(e)}")
        return {"error": str(e)}


def process_migration_queue(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Enhanced migration queue processing with state reconciliation
    and standby pool maintenance
    """

    print(f"Migration queue check triggered by event: {json.dumps(event)}")
    print(f"Lambda context: {context.function_name if context else 'No context'}")

    try:
        # Get comprehensive system status
        system_status = get_premium_system_status()
        print(f"System status: {system_status}")

        # First, check if we should do immediate cleanup when no premium users active
        active_users = count_active_premium_users()
        if active_users == 0:
            print(
                "No active premium users detected, "
                "performing immediate idle instance cleanup"
            )
            immediate_cleanup_count = cleanup_idle_running_instances(
                idle_timeout_hours=0
            )
            if immediate_cleanup_count > 0:
                print(
                    f"Immediately converted {immediate_cleanup_count} "
                    f"instances to standby (no active users)"
                )

        # Then, maintain standby pool
        standby_maintenance_result = maintain_standby_pool()
        print(
            f"Standby pool maintenance: "
            f"{'success' if standby_maintenance_result else 'failed'}"
        )

        # Second, reconcile instance states
        reconciliation_result = reconcile_instance_states()
        print(f"State reconciliation: {reconciliation_result}")

        # Get all premium instances with current states
        all_instances = get_all_premium_instances_with_states()
        running_instances = [i for i in all_instances if i["state"] == "running"]

        available_instances = []
        shared_instances = []

        for instance in running_instances:
            instance_id = instance["instance_id"]

            if not check_instance_readiness(instance_id):
                continue

            assigned_users = get_assigned_users_for_instance(instance_id)
            user_count = len(assigned_users)

            if user_count == 0:
                available_instances.append(instance_id)
            elif user_count > 1:  # Shared instance
                # Extract user_ids from the user objects
                user_ids = [user["user_id"] for user in assigned_users]
                shared_instances.append((instance_id, user_ids))

        migrations_performed = 0

        # Migrate users from shared instances to dedicated instances
        # or standby instances
        for instance_id, user_ids in shared_instances:
            if not available_instances:
                # Try to use standby instances for migration
                standby_instances = get_available_standby_instances()
                if standby_instances:
                    standby_instance_id = standby_instances[0]["instance_id"]
                    if start_standby_instance(standby_instance_id):
                        available_instances.append(standby_instance_id)
                        print(
                            f"Started standby instance {standby_instance_id} "
                            f"for migration"
                        )

            if not available_instances:
                break

            # Migrate all but one user from shared instance
            users_to_migrate = user_ids[1:]  # Keep first user, migrate others

            for user_id in users_to_migrate:
                if not available_instances:
                    break

                new_instance_id = available_instances.pop(0)
                if migrate_user_to_dedicated_instance(user_id, new_instance_id):
                    migrations_performed += 1
                    print(
                        f"Migrated user {user_id} to dedicated instance"
                        f" {new_instance_id}"
                    )
                else:
                    # Return instance to available list if migration failed
                    available_instances.append(new_instance_id)

        # Final standby pool maintenance after migrations
        maintain_standby_pool()

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": f"Migration and maintenance complete. "
                    f"Performed {migrations_performed} migrations.",
                    "migrations": migrations_performed,
                    "available_instances": len(available_instances),
                    "shared_instances": len(shared_instances),
                    "reconciliation": reconciliation_result,
                    "standby_maintenance": standby_maintenance_result,
                    "system_status": system_status,
                }
            ),
        }

    except Exception as e:
        print(f"Error processing migration queue: {str(e)}")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
