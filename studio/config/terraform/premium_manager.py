"""
Premium Manager Lambda Function

Handles assignment and release of premium users to/from spot fleet instances.
Triggered by API calls when premium users log in/out.
Manages ALB routing rules for premium user traffic.
"""

import json
import os
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
    spot_fleet_id = os.environ["SPOT_FLEET_ID"]

    try:
        # 1. Find available instance in spot fleet
        spot_fleet_response = ec2.describe_spot_fleet_instances(
            SpotFleetRequestId=spot_fleet_id
        )

        available_instance = None
        shared_instance = None

        for instance in spot_fleet_response["ActiveInstances"]:
            instance_id = instance["InstanceId"]

            # Check if instance is already assigned using RDS
            assigned_users = get_assigned_users_for_instance(instance_id)

            if not assigned_users:  # Instance is available
                # Check if instance is ready (has running ECS tasks)
                if check_instance_readiness(instance_id):
                    available_instance = instance
                    break
            elif (
                len(assigned_users) == 1
            ):  # Instance has one user (can be shared temporarily)
                shared_instance = instance

        # Strategy: Use dedicated instance if available, otherwise share temporarily
        if available_instance:
            instance_id = available_instance["InstanceId"]
            print(f"Assigning user {user_id} to dedicated instance {instance_id}")
        elif shared_instance:
            instance_id = shared_instance["InstanceId"]
            print(
                f"Temporarily assigning user {user_id} to shared instance {instance_id}"
            )
            # Trigger scaling for future users
            scale_spot_fleet_if_needed()
        else:
            # Try to scale up and assign to shared instance if available
            scaled = scale_spot_fleet_if_needed()
            if scaled:
                # Return a message asking to retry in a moment
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
                            "error": "No available premium "
                            "instances and cannot scale further"
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


def scale_spot_fleet_if_needed():
    """Scale up spot fleet if all instances are occupied"""
    ec2 = boto3.client("ec2")
    spot_fleet_id = os.environ["SPOT_FLEET_ID"]

    try:
        # Get current spot fleet capacity and instances
        spot_fleet_response = ec2.describe_spot_fleet_requests(
            SpotFleetRequestIds=[spot_fleet_id]
        )
        current_capacity = spot_fleet_response["SpotFleetRequestConfigs"][0][
            "SpotFleetRequestConfig"
        ]["TargetCapacity"]

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

        available_instances = total_instances - occupied_instances
        print(
            f"Spot fleet status: {available_instances} available, "
            f"{occupied_instances} occupied, {total_instances} total"
        )

        # Scale up if no available instances and current capacity < 3
        if available_instances == 0 and current_capacity < 3:
            new_capacity = min(current_capacity + 1, 3)
            print(f"Scaling spot fleet from {current_capacity} to {new_capacity}")

            ec2.modify_spot_fleet_request(
                SpotFleetRequestId=spot_fleet_id, TargetCapacity=new_capacity
            )

            return True

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

        # 4. Check if we can scale down the spot fleet
        scale_down_if_possible()

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
    """Scale down spot fleet if there are idle instances"""
    ec2 = boto3.client("ec2")
    spot_fleet_id = os.environ["SPOT_FLEET_ID"]

    try:
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

        # Keep at least 1 instance, scale down if we have 2+ idle instances
        if total_instances > 1 and (total_instances - occupied_instances) >= 2:
            new_capacity = max(occupied_instances + 1, 1)
            print(f"Scaling spot fleet down from {total_instances} to {new_capacity}")

            ec2.modify_spot_fleet_request(
                SpotFleetRequestId=spot_fleet_id, TargetCapacity=new_capacity
            )

    except Exception as e:
        print(f"Error scaling down spot fleet: {str(e)}")


def process_migration_queue(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Check for users on shared instances and migrate to dedicated instances when ready
    """

    print(f"Migration queue check triggered by event: {json.dumps(event)}")
    print(f"Lambda context: {context.function_name if context else 'No context'}")

    try:
        ec2 = boto3.client("ec2")
        spot_fleet_id = os.environ["SPOT_FLEET_ID"]

        # Get all premium instances
        spot_fleet_response = ec2.describe_spot_fleet_instances(
            SpotFleetRequestId=spot_fleet_id
        )

        available_instances = []
        shared_instances = []

        for instance in spot_fleet_response["ActiveInstances"]:
            instance_id = instance["InstanceId"]
            assigned_users = get_assigned_users_for_instance(instance_id)

            if not assigned_users and check_instance_readiness(instance_id):
                available_instances.append(instance_id)
            elif len(assigned_users) > 1:  # Shared instance
                shared_instances.append((instance_id, assigned_users))

        migrations_performed = 0

        # Migrate users from shared instances to dedicated instances
        for instance_id, users in shared_instances:
            if not available_instances:
                break

            # Migrate all but one user from shared instance
            users_to_migrate = users[1:]  # Keep first user, migrate others

            for user_id in users_to_migrate:
                if not available_instances:
                    break

                new_instance_id = available_instances.pop(0)
                if migrate_user_to_dedicated_instance(user_id, new_instance_id):
                    migrations_performed += 1
                    print(
                        f"Migrated user {user_id} to dedicated instance "
                        f"{new_instance_id}"
                    )
                else:
                    # Return instance to available list if migration failed
                    available_instances.append(new_instance_id)

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": (
                        f"Migration check complete. Performed "
                        f"{migrations_performed} migrations."
                    ),
                    "migrations": migrations_performed,
                    "available_instances": len(available_instances),
                    "shared_instances": len(shared_instances),
                }
            ),
        }

    except Exception as e:
        print(f"Error processing migration queue: {str(e)}")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
