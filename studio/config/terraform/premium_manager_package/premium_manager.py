"""
Premium Manager Lambda Function

PRIMARY RESPONSIBILITIES:
- Real-time assignment of premium users to instances (API-triggered)
- Real-time release of premium users from instances (API-triggered)
- Immediate scaling and instance management for user requests
- ALB routing rule creation and deletion

ARCHITECTURE NOTES:
- This Lambda handles REAL-TIME operations triggered by user actions
- Scheduled maintenance (cleanup, reconciliation) is handled by premium_cleanup.py
- Some functions exist in both Lambdas for different purposes:
  - premium_manager: Immediate operations during user login/logout
  - premium_cleanup: Scheduled maintenance operations (hourly)

Required Environment Variables:
- RDS_HOST: Database host (format: host:port)
- RDS_USER: Database username
- RDS_PASSWORD: Database password
- RDS_DATABASE: Database name
- VPC_ID: VPC ID for target group creation
- ALB_LISTENER_ARN: ALB listener ARN for routing rules
- CLUSTER_NAME: ECS cluster name
- PREMIUM_EXTRA_CAPACITY: Extra capacity buffer (default: 2)
- PREMIUM_IDLE_TIMEOUT_HOURS: Must match premium_cleanup.py value
"""

import json
import os
import time
from typing import Any, Dict

import boto3
import pymysql


def get_required_env_var(var_name: str, default_value: str = None) -> str:
    """
    Safely get required environment variable with helpful error messages.

    Args:
        var_name: Name of the environment variable
        default_value: Optional default value if not provided

    Returns:
        Environment variable value

    Raises:
        ValueError: If required environment variable is missing and no default provided
    """
    value = os.environ.get(var_name, default_value)
    if value is None or value == "":
        raise ValueError(
            f"Missing required environment variable: {var_name}. "
            f"Check your Terraform configuration and Lambda environment settings."
        )
    return value


def get_db_connection(auto_commit=False):
    """Create database connection with proper transaction management"""
    try:
        rds_host = get_required_env_var("RDS_HOST")
        host = rds_host.split(":")[0] if ":" in rds_host else rds_host

        return pymysql.connect(
            host=host,
            port=3306,
            user=get_required_env_var("RDS_USER"),
            password=get_required_env_var("RDS_PASSWORD"),
            database=get_required_env_var("RDS_DATABASE"),
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=auto_commit,  # Default False for transactions
        )
    except ValueError as e:
        print(
            f"❌ Database connection failed - environment configuration error: {str(e)}"
        )
        raise
    except Exception as e:
        print(f"❌ Database connection failed - connection error: {str(e)}")
        raise


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


@with_transaction
def _increment_assignment_attempts_transaction(connection, user_id: str) -> int:
    """Internal function: Increment assignment attempts for retry scenarios with transaction safety"""
    with connection.cursor() as cursor:
        # Check if user has existing assignment and increment attempts
        cursor.execute(
            """SELECT assignment_attempts FROM premium_user_assignments
               WHERE user_id = %s FOR UPDATE""",
            (user_id,),
        )
        existing = cursor.fetchone()

        if existing:
            current_attempts = existing[0] if existing[0] is not None else 1
            new_attempts = current_attempts + 1

            cursor.execute(
                """UPDATE premium_user_assignments
                   SET assignment_attempts = %s, last_state_check = NOW()
                   WHERE user_id = %s""",
                (new_attempts, user_id),
            )

            print(
                f"Incremented assignment attempts for user {user_id} to {new_attempts}"
            )
            return new_attempts
        else:
            # No existing assignment, this is first attempt
            print(
                f"No existing assignment for user {user_id}, treating as first attempt"
            )
            return 1


def increment_assignment_attempts(user_id: str) -> int:
    """Increment assignment attempts for retry scenarios"""
    return _increment_assignment_attempts_transaction(user_id)


def _store_user_assignment_transaction(
    connection,
    user_id: str,
    instance_id: str,
    target_group_arn: str,
    rule_arn: str,
    instance_state: str = "launching",
    is_shared: bool = False,
):
    """Internal function: Store user assignment with transaction safety"""
    with connection.cursor() as cursor:
        # Check if user already has assignment with lock to prevent race conditions
        cursor.execute(
            """SELECT user_id, assignment_attempts FROM premium_user_assignments
               WHERE user_id = %s FOR UPDATE""",
            (user_id,),
        )
        existing = cursor.fetchone()

        if existing:
            # User already has assignment - increment attempts counter
            current_attempts = existing[1] if existing[1] is not None else 1
            new_attempts = current_attempts + 1

            cursor.execute(
                """UPDATE premium_user_assignments
                   SET assignment_attempts = %s, last_state_check = NOW()
                   WHERE user_id = %s""",
                (new_attempts, user_id),
            )

            print(
                f"User {user_id} already has assignment, incremented attempts to {new_attempts}"
            )
            raise Exception(
                f"User {user_id} already has a premium assignment (attempt #{new_attempts})"
            )

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

    print(
        f"Stored assignment in RDS: user {user_id} -> instance {instance_id} "
        f"(state: {instance_state}, shared: {is_shared})"
    )


def store_user_assignment(
    user_id: str,
    instance_id: str,
    target_group_arn: str,
    rule_arn: str,
    instance_state: str = "launching",
    is_shared: bool = False,
):
    """Store user assignment in RDS with proper transaction isolation and locking"""
    return _store_user_assignment_transaction(
        user_id, instance_id, target_group_arn, rule_arn, instance_state, is_shared
    )


@with_transaction
def _remove_user_assignment_transaction(connection, user_id: str):
    """Internal function: Remove user assignment with transaction safety"""
    with connection.cursor() as cursor:
        # Get assignment details before deletion with lock to prevent race conditions
        cursor.execute(
            """SELECT instance_id, target_group_arn, alb_rule_arn
               FROM premium_user_assignments
               WHERE user_id = %s FOR UPDATE""",
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


def remove_user_assignment(user_id: str):
    """Remove user assignment from RDS with proper transaction isolation"""
    return _remove_user_assignment_transaction(user_id)


@with_transaction
def _get_assigned_users_for_instance_transaction(connection, instance_id: str):
    """Get list of users assigned to an instance with transaction safety"""
    with connection.cursor() as cursor:
        # First, get all assignments including standby for debugging
        cursor.execute(
            """SELECT user_id, is_shared, instance_state, is_standby, status
               FROM premium_user_assignments
               WHERE instance_id = %s""",
            (instance_id,),
        )
        all_assignments = cursor.fetchall()

        print(f"    📋 All assignments for instance {instance_id}:")
        for assignment in all_assignments:
            user_id = assignment.get("user_id", "N/A")
            is_standby = assignment.get("is_standby", 0)
            status = assignment.get("status", "N/A")
            print(f"      - User: {user_id}, Standby: {is_standby}, Status: {status}")

        # Now get only real user assignments (exclude standby entries) with lock
        cursor.execute(
            """SELECT user_id, is_shared, instance_state
               FROM premium_user_assignments
               WHERE instance_id = %s AND status = 'active' AND is_standby = 0
               FOR UPDATE""",
            (instance_id,),
        )
        real_users = cursor.fetchall()

        print(f"    👥 Real user assignments (excluding standby): {len(real_users)}")
        for user in real_users:
            print(f"      - Real user: {user.get('user_id', 'N/A')}")

        return real_users


def get_assigned_users_for_instance(instance_id: str):
    """Get list of users assigned to an instance (excluding standby entries)"""
    try:
        return _get_assigned_users_for_instance_transaction(instance_id)
    except Exception as e:
        print(f"    ❌ Error getting assigned users for {instance_id}: {str(e)}")
        return []


def get_all_premium_instances_with_states():
    """Get all premium instances with their AWS states"""
    ec2 = boto3.client("ec2")
    try:
        # Get instances with premium tags (use multiple filters for robust discovery)
        response = ec2.describe_instances(
            Filters=[
                {
                    "Name": "instance-state-name",
                    "Values": ["pending", "running", "stopping", "stopped"],
                },
                # Use OR logic: either Name contains "premium" OR Tier tag is "premium"
            ]
        )

        # Apply tag filtering in Python for more flexible matching
        def is_premium_instance(instance):
            tags = {
                tag.get("Key"): tag.get("Value") for tag in instance.get("Tags", [])
            }
            instance_id = instance["InstanceId"]

            # Check multiple criteria for premium instances
            name_match = "premium" in tags.get("Name", "").lower()
            tier_match = tags.get("Tier", "").lower() == "premium"
            type_match = "premium" in tags.get("Type", "").lower()

            # Debug logging for tag matching
            print(f"Instance {instance_id} tag analysis:")
            print(f"  - Name: '{tags.get('Name', '')}' -> name_match: {name_match}")
            print(f"  - Tier: '{tags.get('Tier', '')}' -> tier_match: {tier_match}")
            print(f"  - Type: '{tags.get('Type', '')}' -> type_match: {type_match}")
            print(f"  - All tags: {tags}")

            result = name_match or tier_match or type_match
            print(f"  - Final match result: {result}")
            return result

        instances = []
        all_instances_found = 0

        for reservation in response["Reservations"]:
            for instance in reservation["Instances"]:
                all_instances_found += 1
                instance_id = instance["InstanceId"]
                state = instance["State"]["Name"]

                print(f"Evaluating instance {instance_id} (state: {state})")

                # Only include premium instances
                if is_premium_instance(instance):
                    instance_data = {
                        "instance_id": instance["InstanceId"],
                        "instance_type": instance["InstanceType"],
                        "state": instance["State"]["Name"],
                        "launch_time": instance.get("LaunchTime"),
                    }
                    instances.append(instance_data)
                    print(f"✅ Added premium instance: {instance_data}")
                else:
                    print(f"❌ Skipped non-premium instance: {instance_id}")

        print("Instance discovery summary:")
        print(f"  - Total instances found in AWS: {all_instances_found}")
        print(f"  - Premium instances matched: {len(instances)}")
        print(f"  - Premium instance IDs: {[i['instance_id'] for i in instances]}")
        print(f"  - States: {[(i['instance_id'], i['state']) for i in instances]}")

        return instances
    except Exception as e:
        print(f"Error getting premium instances: {str(e)}")
        return []


@with_transaction
def _count_active_premium_users_transaction(connection):
    """Count users with active premium assignments with transaction safety"""
    with connection.cursor() as cursor:
        # First count all assignments for debugging
        cursor.execute(
            "SELECT COUNT(*) as total_count, "
            "SUM(CASE WHEN is_standby = 1 THEN 1 ELSE 0 END) as standby_count "
            "FROM premium_user_assignments WHERE status = 'active'"
        )
        debug_result = cursor.fetchone()
        total_count = debug_result["total_count"] if debug_result else 0
        standby_count = debug_result["standby_count"] if debug_result else 0

        # Count only real user assignments (exclude standby)
        cursor.execute(
            "SELECT COUNT(*) as count FROM premium_user_assignments "
            "WHERE status = 'active' AND is_standby = 0"
        )
        result = cursor.fetchone()
        real_user_count = result["count"] if result else 0

        print("📊 User count analysis:")
        print(f"  - Total active assignments: {total_count}")
        print(f"  - Standby assignments: {standby_count}")
        print(f"  - Real user assignments: {real_user_count}")

        return real_user_count


def count_active_premium_users():
    """Count users with active premium assignments (excluding standby entries)"""
    try:
        return _count_active_premium_users_transaction()
    except Exception as e:
        print(f"❌ Error counting active premium users: {str(e)}")
        return 0


def count_total_premium_users():
    """Count total premium users with active subscriptions (for capacity planning)"""
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                # First try to query using subscription tables if they exist
                try:
                    cursor.execute(
                        """SELECT COUNT(DISTINCT su.user_id) as count
                           FROM subscription_users su
                           JOIN subscription_plans sp ON su.plan_id = sp.id
                           WHERE sp.name = 'Premium'
                           AND su.expiration > NOW()
                           AND su.sync_status = 'synced'"""
                    )
                    result = cursor.fetchone()
                    if result and result["count"] > 0:
                        total_premium = result["count"]
                        print(
                            f"Total premium subscribers (from subscription tables): {total_premium}"
                        )
                        return total_premium
                except Exception as subscription_error:
                    print(
                        f"Subscription tables query failed: {str(subscription_error)}"
                    )

                # Fallback: Try to count from premium_user_assignments (real users, not standby)
                try:
                    cursor.execute(
                        """SELECT COUNT(DISTINCT user_id) as count
                           FROM premium_user_assignments
                           WHERE status = 'active' AND is_standby = 0"""
                    )
                    result = cursor.fetchone()
                    active_assignments = result["count"] if result else 0

                    # For capacity planning, assume at least some premium users aren't currently assigned
                    # Use active assignments as minimum, but add buffer for unassigned premium users
                    estimated_premium = max(
                        active_assignments, 1
                    )  # At least 1 for testing

                    print(
                        f"Estimated premium subscribers (from assignments): {estimated_premium} (based on {active_assignments} active assignments)"
                    )
                    return estimated_premium

                except Exception as assignment_error:
                    print(f"Assignment table query failed: {str(assignment_error)}")

                # Last resort fallback for development/testing
                print("All database queries failed, using development fallback")
                return 3  # Conservative estimate for development

    except Exception as e:
        print(f"Error counting total premium users: {str(e)}")
        # Fallback to a reasonable default if we can't query the database
        return 3


def get_dynamic_max_capacity():
    """Get dynamic maximum capacity based on premium subscribers with safety buffer"""
    # Get subscriber count for capacity planning
    total_premium_subscribers = count_total_premium_users()

    # Configuration - use existing environment variables where possible
    # Combine buffer and standby into one concept: "extra capacity"
    EXTRA_CAPACITY = int(
        os.environ.get("PREMIUM_EXTRA_CAPACITY", "2")
    )  # Extra instances beyond current subscribers for quick response + standby
    ABSOLUTE_MAX = int(os.environ.get("ABSOLUTE_MAX", "20"))  # Use existing variable

    # Get current standby count from existing table for information
    standby_count = get_standby_count()

    # Calculate dynamic max capacity
    # Logic: We need capacity for all premium subscribers + extra capacity for:
    #   - Quick response to new logins
    #   - Standby instances for fast assignment
    #   - Safety buffer for concurrent logins
    if total_premium_subscribers == 0:
        # Development/testing scenario - minimal capacity
        max_capacity = 3  # Allow testing with 2 running + 1 standby
    else:
        # Production scenario - scale based on subscriber count
        max_capacity = min(total_premium_subscribers + EXTRA_CAPACITY, ABSOLUTE_MAX)

    print("🏗️ Dynamic capacity calculation:")
    print(f"  - Premium subscribers: {total_premium_subscribers}")
    print(f"  - Extra capacity (buffer + standby): {EXTRA_CAPACITY}")
    print(f"  - Current standby count: {standby_count}")
    print(f"  - Calculated max capacity: {max_capacity}")
    print(f"  - Absolute maximum: {ABSOLUTE_MAX}")
    calculated_capacity = (
        total_premium_subscribers + EXTRA_CAPACITY
        if total_premium_subscribers > 0
        else 3
    )
    print(
        f"  - Logic: {total_premium_subscribers} subscribers + "
        f"{EXTRA_CAPACITY} extra = {calculated_capacity} (capped at {ABSOLUTE_MAX})"
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
    """Get standby instances from database (AWS state checked separately)"""
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT instance_id, standby_created_at
                        FROM premium_user_assignments
                       WHERE is_standby = 1 AND status = 'active'
                       ORDER BY standby_created_at ASC
                       """
                )
                db_standby_instances = cursor.fetchall()

        # Filter to only include instances that are actually stopped in AWS
        available_standby = []
        if db_standby_instances:
            # Get current AWS states for all standby instances
            standby_instance_ids = [
                inst["instance_id"] for inst in db_standby_instances
            ]
            ec2 = boto3.client("ec2")

            try:
                response = ec2.describe_instances(InstanceIds=standby_instance_ids)
                aws_states = {}
                for reservation in response["Reservations"]:
                    for instance in reservation["Instances"]:
                        aws_states[instance["InstanceId"]] = instance["State"]["Name"]

                # Only return instances that are actually stopped in AWS
                for inst in db_standby_instances:
                    instance_id = inst["instance_id"]
                    if aws_states.get(instance_id) == "stopped":
                        available_standby.append(inst)

                print(
                    f"Standby instances: {len(db_standby_instances)} in DB, {len(available_standby)} actually stopped in AWS"
                )

            except Exception as aws_error:
                print(f"Failed to check AWS states for standby instances: {aws_error}")
                # Fall back to database list if AWS check fails
                available_standby = db_standby_instances

        return available_standby

    except Exception as e:
        print(f"Error getting available standby instances: {str(e)}")
        return []


def register_orphaned_stopped_instances():
    """Register stopped premium instances that aren't in the standby pool database"""
    try:
        # Get all premium instances from AWS
        all_aws_instances = get_all_premium_instances_with_states()
        stopped_aws_instances = [
            i for i in all_aws_instances if i["state"] == "stopped"
        ]

        # Get existing standby instances from database
        existing_standby_instances = get_available_standby_instances()
        existing_standby_ids = {
            inst["instance_id"] for inst in existing_standby_instances
        }

        # Find orphaned stopped instances (in AWS but not in database)
        orphaned_instances = []
        for instance in stopped_aws_instances:
            instance_id = instance["instance_id"]
            if instance_id not in existing_standby_ids:
                # Check if this instance is already assigned to a user
                assigned_users = get_assigned_users_for_instance(instance_id)
                if not assigned_users:  # Truly orphaned
                    orphaned_instances.append(instance)

        print(
            f"Found {len(orphaned_instances)} orphaned stopped instances to register as standby"
        )

        # Register each orphaned instance as standby
        registered_count = 0
        for instance in orphaned_instances:
            instance_id = instance["instance_id"]
            try:
                # Store as standby assignment
                store_user_assignment(
                    user_id=f"standby-{instance_id}",
                    instance_id=instance_id,
                    target_group_arn="standby",
                    rule_arn="standby",
                    instance_state="launching",  # Use valid enum value
                    is_shared=False,
                )

                # Mark as standby
                with get_db_connection() as connection:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            """UPDATE premium_user_assignments
                               SET is_standby = 1, standby_created_at = NOW(), last_state_check = NOW()
                               WHERE instance_id = %s""",
                            (instance_id,),
                        )

                print(f"Registered orphaned instance {instance_id} as standby")
                registered_count += 1

            except Exception as e:
                print(f"Failed to register orphaned instance {instance_id}: {str(e)}")
                continue

        print(
            f"Successfully registered {registered_count} orphaned instances as standby"
        )
        return registered_count

    except Exception as e:
        print(f"Error registering orphaned stopped instances: {str(e)}")
        return 0


def create_and_stop_standby_instance():
    """Create instance and immediately stop it for standby use"""
    ec2 = boto3.client("ec2")

    try:
        # Get launch template ID from environment
        launch_template_id = get_required_env_var("PREMIUM_LAUNCH_TEMPLATE_ID")

        # Get subnet IDs from environment
        subnet_ids = get_required_env_var("SUBNET_IDS").split(",")

        # Launch instance using the premium launch template
        response = ec2.run_instances(
            LaunchTemplate={
                "LaunchTemplateId": launch_template_id,
                "Version": "$Latest",
            },
            InstanceType="t3.large",
            SubnetId=subnet_ids[0],  # Use first private subnet
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
                       SET is_standby = 1, standby_created_at = NOW(), last_state_check = NOW()
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
                       SET instance_state = 'running', is_standby = 0, last_state_check = NOW()
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
    Handle premium user assignment lifecycle events and scheduled cleanup

    Event structure for API calls:
    {
        "httpMethod": "GET" | "POST",
        "body": '{"action": "assign" | "release", "user_id": "123", "tier": "premium"}'
    }

    Event structure for scheduled cleanup:
    {
        "source": "aws.events",
        "detail-type": "Scheduled Event",
        "detail": {"action": "cleanup"}
    }
    """

    print(f"Premium manager received event: {json.dumps(event)}")
    print(f"Lambda context: {context.function_name if context else 'No context'}")

    try:
        # Scheduled cleanup is now handled by separate premium_cleanup Lambda
        if (
            event.get("source") == "aws.events"
            and event.get("detail-type") == "Scheduled Event"
        ):
            return {
                "statusCode": 400,
                "body": json.dumps(
                    {
                        "error": "Scheduled events should be handled by premium_cleanup Lambda",
                        "message": "This Lambda only handles real-time assignment operations",
                    }
                ),
            }

        # Handle API Gateway events
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
            elif action == "update_activity":
                return handle_activity_update(user_id)
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

    try:
        vpc_id = get_required_env_var("VPC_ID")
        alb_listener_arn = get_required_env_var("ALB_LISTENER_ARN")
    except ValueError as e:
        print(f"❌ Assignment failed - environment configuration error: {str(e)}")
        return {
            "statusCode": 500,
            "body": json.dumps(
                {"error": "Configuration error", "message": str(e), "assigned": False}
            ),
        }

    # Initialize variables for exception handling scope
    target_group_arn = None
    rule_arn = None

    try:
        # 0. Register any orphaned stopped instances as standby first
        register_orphaned_stopped_instances()

        # 1. Get comprehensive instance state information
        all_instances = get_all_premium_instances_with_states()
        running_instances = [i for i in all_instances if i["state"] == "running"]
        launching_instances = [i for i in all_instances if i["state"] in ["pending"]]
        active_users = count_active_premium_users()

        # Get standby pool status (now includes newly registered instances)
        standby_instances = get_available_standby_instances()
        standby_count = len(standby_instances)

        print("🚀 === PREMIUM USER ASSIGNMENT START ===")
        print(f"🎯 Target user: {user_id}")
        print("📊 Assignment context:")
        print(f"  - Running instances: {len(running_instances)}")
        print(f"  - Launching instances: {len(launching_instances)}")
        print(f"  - Active users: {active_users}")
        print(f"  - Standby available: {standby_count}")
        print(f"  - Total instances: {len(all_instances)}")

        print("📋 Instance details:")
        for instance in all_instances:
            print(f"  - {instance['instance_id']}: {instance['state']}")

        stopped_instances = [i for i in all_instances if i["state"] == "stopped"]
        print(
            f"🛑 Stopped instances found in AWS: {[i['instance_id'] for i in stopped_instances]}"
        )
        print(
            f"⏸️ Standby instances in database: {[i['instance_id'] for i in standby_instances]}"
        )
        print("🚀 === STARTING ASSIGNMENT LOGIC ===")
        print()

        # Initialize assignment variables
        instance_to_use = None
        is_shared = False
        instance_state = None
        assignment_source = None

        # 2. PRIORITY 1: Available dedicated running instances (immediate assignment)
        available_dedicated = None
        least_loaded_instance = None
        min_users = float("inf")

        print(
            f"🔍 PRIORITY 1: Evaluating {len(running_instances)} running instances for immediate assignment"
        )

        for i, instance in enumerate(running_instances):
            instance_id = instance["instance_id"]
            print(
                f"  [{i+1}/{len(running_instances)}] Evaluating instance {instance_id}"
            )

            # Check instance readiness
            print(f"    🔧 Checking readiness for instance {instance_id}...")
            is_ready = check_instance_readiness(instance_id)
            print(f"    🔧 Readiness result: {is_ready}")

            if not is_ready:
                print(f"    ❌ Skipping {instance_id} - not ready")
                continue

            # Check assigned users
            print(f"    👥 Checking assigned users for instance {instance_id}...")
            assigned_users = get_assigned_users_for_instance(instance_id)
            user_count = len(assigned_users)
            print(
                f"    👥 Found {user_count} assigned users: {[u.get('user_id', u) for u in assigned_users]}"
            )

            if user_count == 0:
                # Found dedicated instance
                available_dedicated = instance
                print(f"    ✅ Found dedicated instance: {instance_id}")
                break
            elif user_count < min_users:
                # Track least loaded for sharing
                least_loaded_instance = instance
                min_users = user_count
                print(
                    f"    📊 Tracking as least loaded: {instance_id} ({user_count} users)"
                )
            else:
                print(
                    f"    📊 Instance {instance_id} has {user_count} users (not optimal)"
                )

        print("🔍 PRIORITY 1 Results:")
        print(
            f"  - Available dedicated: {available_dedicated['instance_id'] if available_dedicated else 'None'}"
        )
        print(
            f"  - Least loaded: {least_loaded_instance['instance_id'] if least_loaded_instance else 'None'} ({min_users} users)"
        )

        # Use dedicated instance if available (PRIORITY 1)
        if available_dedicated:
            instance_to_use = available_dedicated
            is_shared = False
            instance_state = "running"
            assignment_source = "dedicated"
            print(
                f"✅ PRIORITY 1 SUCCESS: Using dedicated running instance {instance_to_use['instance_id']} for user {user_id}"
            )
        else:
            print("❌ PRIORITY 1 FAILED: No dedicated instances available")

        # 3. PRIORITY 2: Start standby instance (5-15 second assignment)
        if not instance_to_use and standby_instances:
            print("No dedicated instances available, starting standby instance")

            # Use oldest standby instance
            standby_to_start = standby_instances[0]
            standby_instance_id = standby_to_start["instance_id"]

            # Start the standby instance
            if start_standby_instance(standby_instance_id):
                # Create replacement standby instance asynchronously
                print("Creating replacement standby instance")
                create_and_stop_standby_instance()  # Create a single replacement standby

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

        # 3.5 PRIORITY 2.5: Fallback to AWS stopped instances not in database
        if not instance_to_use:
            # Find stopped instances directly from AWS that aren't in our standby database
            stopped_aws_instances = [
                i for i in all_instances if i["state"] == "stopped"
            ]

            # Filter out instances that are already in standby pool
            standby_instance_ids = {inst["instance_id"] for inst in standby_instances}
            aws_only_stopped = [
                i
                for i in stopped_aws_instances
                if i["instance_id"] not in standby_instance_ids
            ]

            if aws_only_stopped:
                fallback_instance = aws_only_stopped[0]
                fallback_instance_id = fallback_instance["instance_id"]
                print(
                    f"No standby instances available, using AWS stopped instance {fallback_instance_id}"
                )

                # Start this AWS instance directly
                ec2 = boto3.client("ec2")
                try:
                    print(f"Starting AWS stopped instance {fallback_instance_id}")
                    ec2.start_instances(InstanceIds=[fallback_instance_id])

                    # Wait for running state
                    waiter = ec2.get_waiter("instance_running")
                    waiter.wait(
                        InstanceIds=[fallback_instance_id],
                        WaiterConfig={"Delay": 15, "MaxAttempts": 24},  # 6 minutes max
                    )

                    # Proceed with assignment
                    instance_to_use = {"instance_id": fallback_instance_id}
                    is_shared = False
                    instance_state = "running"
                    assignment_source = "aws_fallback"

                    print(
                        f"Successfully started and using AWS instance {fallback_instance_id}"
                    )

                except Exception as start_error:
                    print(
                        f"Failed to start AWS instance {fallback_instance_id}: {str(start_error)}"
                    )

        # 4. PRIORITY 3: Use existing running instances with sharing
        if not instance_to_use:
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
                    scaled = scale_premium_instances_if_needed()
                    if scaled:
                        print(
                            "Triggered premium instance scaling for dedicated instance"
                        )

        # 5. PRIORITY 4: Scale up (last resort)
        if not instance_to_use:
            if len(launching_instances) > 0:
                # Already scaling - track retry attempt
                attempts = increment_assignment_attempts(user_id)
                return {
                    "statusCode": 202,
                    "body": json.dumps(
                        {
                            "message": f"Premium capacity scaling in progress "
                            f"({len(launching_instances)} instances launching). "
                            f"Please retry in 2-3 minutes. (attempt #{attempts})",
                            "retry_after": 180,
                        }
                    ),
                }
            else:
                # Try to scale
                scaled = scale_premium_instances_if_needed()
                if scaled:
                    # Scaling initiated - track retry attempt
                    attempts = increment_assignment_attempts(user_id)
                    return {
                        "statusCode": 202,
                        "body": json.dumps(
                            {
                                "message": "Scaling premium capacity. "
                                f"Please retry in 2-3 minutes. (attempt #{attempts})",
                                "retry_after": 180,
                            }
                        ),
                    }
                else:
                    # Generate detailed error message for debugging
                    stopped_instances = [
                        i for i in all_instances if i["state"] == "stopped"
                    ]
                    error_details = {
                        "error": "No available premium instances and cannot scale further",
                        "debug_info": {
                            "total_instances": len(all_instances),
                            "running_instances": len(running_instances),
                            "launching_instances": len(launching_instances),
                            "stopped_instances": len(stopped_instances),
                            "standby_instances": len(standby_instances),
                            "active_users": active_users,
                            "stopped_instance_ids": [
                                i["instance_id"] for i in stopped_instances
                            ],
                            "standby_instance_ids": [
                                i["instance_id"] for i in standby_instances
                            ],
                        },
                    }
                    print(f"Assignment failed with debug info: {error_details}")
                    return {
                        "statusCode": 503,
                        "body": json.dumps(error_details),
                    }

        # Final check: Ensure we have an instance assigned
        if not instance_to_use:
            print("💥 === ASSIGNMENT FAILURE ANALYSIS ===")

            # Analyze why each priority failed
            failure_reasons = []

            if len(running_instances) == 0:
                failure_reasons.append("❌ Priority 1: No running instances found")
            else:
                failure_reasons.append(
                    f"❌ Priority 1: {len(running_instances)} running instances found but all failed readiness/assignment checks"
                )

            if len(standby_instances) == 0:
                failure_reasons.append(
                    "❌ Priority 2: No standby instances available in database"
                )
            else:
                failure_reasons.append(
                    f"❌ Priority 2: {len(standby_instances)} standby instances found but failed to start"
                )

            if len(stopped_instances) == 0:
                failure_reasons.append(
                    "❌ Priority 2.5: No stopped instances found in AWS"
                )
            else:
                failure_reasons.append(
                    f"❌ Priority 2.5: {len(stopped_instances)} stopped instances found but failed to start"
                )

            if least_loaded_instance:
                failure_reasons.append(
                    f"❌ Priority 3: Sharing available but conditions not met"
                )
            else:
                failure_reasons.append(
                    "❌ Priority 3: No instances available for sharing"
                )

            failure_reasons.append("❌ Priority 4: Scaling failed or blocked")

            print("🔍 Failure analysis:")
            for reason in failure_reasons:
                print(f"  {reason}")

            error_details = {
                "error": "Could not assign premium instance - all assignment paths failed",
                "debug_info": {
                    "user_id": user_id,
                    "total_instances": len(all_instances),
                    "running_instances": len(running_instances),
                    "launching_instances": len(launching_instances),
                    "stopped_instances": len(stopped_instances),
                    "standby_instances": len(standby_instances),
                    "active_users": active_users,
                    "running_instance_ids": [
                        i["instance_id"] for i in running_instances
                    ],
                    "stopped_instance_ids": [
                        i["instance_id"] for i in stopped_instances
                    ],
                    "standby_instance_ids": [
                        i["instance_id"] for i in standby_instances
                    ],
                    "failure_reasons": failure_reasons,
                    "has_least_loaded": least_loaded_instance is not None,
                    "min_users_on_least_loaded": min_users
                    if least_loaded_instance
                    else None,
                },
            }
            print(f"💥 Final assignment failure details: {error_details}")
            print("💥 === ASSIGNMENT FAILED ===")

            return {
                "statusCode": 503,
                "body": json.dumps(error_details),
            }

        # 6. Create target group for the user
        print("✅ === ASSIGNMENT SUCCESS ===")
        print(
            f"🎯 Assigning user {user_id} to instance {instance_to_use['instance_id']}"
        )
        print("📋 Assignment details:")
        print(f"  - Instance ID: {instance_to_use['instance_id']}")
        print(f"  - Assignment source: {assignment_source}")
        print(f"  - Instance state: {instance_state}")
        print(f"  - Is shared: {is_shared}")
        print("✅ === PROCEEDING WITH TARGET GROUP CREATION ===")
        print()

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

        # 10.1. Initialize activity tracking for the new assignment
        try:
            update_user_activity(user_id)
            print(f"✅ Initialized activity tracking for user {user_id}")
        except Exception as activity_error:
            print(f"⚠️ Failed to initialize activity tracking: {str(activity_error)}")
            # Don't fail the assignment for activity tracking errors

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


def scale_premium_instances_if_needed():
    """Scale up premium instances by starting stopped instances or creating new ones"""
    ec2 = boto3.client("ec2")

    try:
        # Get dynamic capacity limits based on premium users
        max_capacity = get_dynamic_max_capacity()

        # Get all premium instances with their states
        all_instances = get_all_premium_instances_with_states()
        active_users = count_active_premium_users()

        running_instances = [i for i in all_instances if i["state"] == "running"]
        launching_instances = [
            i for i in all_instances if i["state"] in ["pending", "launching"]
        ]

        running_count = len(running_instances)
        launching_count = len(launching_instances)
        total_instances = len(all_instances)

        # Get subscriber count for comparison
        total_subscribers = count_total_premium_users()

        print("📊 Enhanced premium instance analysis:")
        print(f"  - Running instances: {running_count}")
        print(f"  - Launching instances: {launching_count}")
        print(f"  - Total instances: {total_instances}")
        print(f"  - Maximum capacity: {max_capacity}")
        print(f"  - Active assignments (logged-in users): {active_users}")
        print(f"  - Premium subscribers (capacity planning): {total_subscribers}")

        # NO SCALING CONDITIONS:
        if launching_count > 0:
            print(f"❌ Scaling blocked: {launching_count} instances already launching")
            return False

        # Key decision: Scale based on ACTIVE ASSIGNMENTS, not subscribers
        # This represents current demand (logged-in users) vs available capacity
        if running_count >= active_users:
            print(
                f"❌ Scaling not needed: {running_count} running >= "
                f"{active_users} active assignments"
            )
            print(
                f"   (Note: {total_subscribers} total subscribers, "
                f"but only {active_users} currently logged in)"
            )
            return False

        # SCALE UP CONDITIONS:
        needed_capacity = active_users - running_count

        if needed_capacity > 0 and total_instances < max_capacity:
            # Try to start stopped instances first
            stopped_instances = [i for i in all_instances if i["state"] == "stopped"]

            if stopped_instances:
                # Start stopped instances
                instances_to_start = min(len(stopped_instances), needed_capacity)
                instance_ids_to_start = [
                    inst["instance_id"]
                    for inst in stopped_instances[:instances_to_start]
                ]

                print(
                    f"Starting {instances_to_start} stopped instances: {instance_ids_to_start}"
                )
                ec2.start_instances(InstanceIds=instance_ids_to_start)
                return True
            else:
                # No stopped instances available, check if we can create new ones
                if total_instances + needed_capacity <= max_capacity:
                    print(
                        f"No stopped instances available, need to create {needed_capacity} new instances"
                    )
                    for _ in range(needed_capacity):
                        create_and_stop_standby_instance()
                        # Note: New instances will be stopped, will need another scaling cycle to start them
                    return True
                else:
                    print(
                        f"Cannot scale: would exceed max capacity ({total_instances + needed_capacity} > {max_capacity})"
                    )
                    return False

        elif total_instances >= max_capacity:
            print(
                f"Scaling blocked: already at maximum capacity "
                f"({total_instances}/{max_capacity})"
            )
            return False

        print(
            f"No scaling needed: running={running_count}, active_users={active_users}, total={total_instances}, max={max_capacity}"
        )
        return False

    except Exception as e:
        print(f"Error scaling premium instances: {str(e)}")
        return False


def get_ecs_container_instance_id(ec2_instance_id: str, cluster_name: str) -> str:
    """Map EC2 instance ID to ECS container instance ID"""
    ecs = boto3.client("ecs")

    try:
        print(
            f"    🔍 Looking up ECS container instance for EC2 instance {ec2_instance_id}"
        )

        # List all container instances in the cluster
        response = ecs.list_container_instances(cluster=cluster_name)
        container_instance_arns = response.get("containerInstanceArns", [])

        if not container_instance_arns:
            print(f"    ❌ No container instances found in cluster {cluster_name}")
            return None

        print(
            f"    📋 Found {len(container_instance_arns)} container instances in cluster"
        )

        # Describe container instances to find the one matching our EC2 instance
        describe_response = ecs.describe_container_instances(
            cluster=cluster_name, containerInstances=container_instance_arns
        )

        for container_instance in describe_response.get("containerInstances", []):
            if container_instance.get("ec2InstanceId") == ec2_instance_id:
                container_instance_id = container_instance.get("containerInstanceArn")
                print(f"    ✅ Found ECS container instance: {container_instance_id}")
                return container_instance_id

        print(
            f"    ❌ No ECS container instance found for EC2 instance {ec2_instance_id}"
        )
        return None

    except Exception as e:
        print(f"    ❌ Error mapping EC2 to ECS container instance: {str(e)}")
        return None


def check_instance_readiness(instance_id: str) -> bool:
    """Check if an instance has a running ECS task and is ready for user assignment"""
    ecs = boto3.client("ecs")

    try:
        cluster_name = get_required_env_var("CLUSTER_NAME")
    except ValueError as e:
        print(
            f"❌ Instance readiness check failed - environment configuration error: {str(e)}"
        )
        return False

    print(f"    🔧 Checking readiness for EC2 instance {instance_id}")

    try:
        # First, get the ECS container instance ID from the EC2 instance ID
        ecs_container_instance_id = get_ecs_container_instance_id(
            instance_id, cluster_name
        )

        if not ecs_container_instance_id:
            print(
                f"    ❌ Cannot find ECS container instance for EC2 instance {instance_id}"
            )
            print("    ❌ Instance not ready: No ECS container instance mapping")
            return False

        # Get ECS tasks running on this container instance
        print("    🔍 Listing tasks on ECS container instance...")
        tasks_response = ecs.list_tasks(
            cluster=cluster_name, containerInstance=ecs_container_instance_id
        )

        task_arns = tasks_response.get("taskArns", [])
        print(f"    📋 Found {len(task_arns)} tasks on container instance")

        if not task_arns:
            print(
                f"    ❌ No tasks running on container instance {ecs_container_instance_id}"
            )
            print("    ❌ Instance not ready: No ECS tasks running")
            return False

        # Check task status
        print(f"    🔍 Describing {len(task_arns)} tasks...")
        task_details = ecs.describe_tasks(cluster=cluster_name, tasks=task_arns)

        premium_tasks_running = 0
        for task in task_details.get("tasks", []):
            task_def_arn = task.get("taskDefinitionArn", "")
            last_status = task.get("lastStatus", "")
            desired_status = task.get("desiredStatus", "")

            print(f"      - Task: {task_def_arn}")
            print(f"        Status: {last_status} (desired: {desired_status})")

            if "premium" in task_def_arn.lower() and last_status == "RUNNING":
                premium_tasks_running += 1
                print("        ✅ Premium task running!")

        print(f"    📊 Found {premium_tasks_running} running premium tasks")

        if premium_tasks_running > 0:
            print(f"    ✅ Instance {instance_id} is ready (has running premium tasks)")
            return True
        else:
            print("    ❌ No running premium tasks found")
            print("    ❌ Instance not ready: No premium ECS tasks running")
            return False

    except Exception as e:
        print(f"    ❌ Error checking instance readiness for {instance_id}: {str(e)}")
        print("    ❌ Instance not ready: Error during readiness check")
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
                    """UPDATE premium_user_assignments SET instance_id = %s, last_state_check = NOW()
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
    """Release premium user from assigned instance - always succeeds to prevent logout blocking"""

    _ = boto3.client("ec2")
    elbv2 = boto3.client("elbv2")

    instance_id = None
    success = True
    errors = []

    try:
        # 1. Get assignment from RDS (may fail if already removed)
        try:
            assignment = remove_user_assignment(user_id)
            instance_id = assignment["instance_id"]
            target_group_arn = assignment["target_group_arn"]
            rule_arn = assignment["alb_rule_arn"]
            print(f"Found assignment for user {user_id} on instance {instance_id}")
        except Exception as assignment_error:
            print(f"No assignment found for user {user_id}: {str(assignment_error)}")
            # User may not have been assigned or already released - not an error for logout
            target_group_arn = None
            rule_arn = None

        # 2. Delete ALB listener rule (if it exists)
        if rule_arn:
            try:
                elbv2.delete_rule(RuleArn=rule_arn)
                print(f"Deleted ALB rule: {rule_arn}")
            except Exception as rule_error:
                error_msg = f"Error deleting ALB rule: {str(rule_error)}"
                print(error_msg)
                errors.append(error_msg)

        # 3. Delete target group (if it exists)
        if target_group_arn and target_group_arn != "standby":
            try:
                elbv2.delete_target_group(TargetGroupArn=target_group_arn)
                print(f"Deleted target group: {target_group_arn}")
            except Exception as tg_error:
                error_msg = f"Error deleting target group: {str(tg_error)}"
                print(error_msg)
                errors.append(error_msg)

        # Note: Stale assignment cleanup is now handled by separate premium_cleanup Lambda
        # running on scheduled basis (hourly)

        # 5. Check if we can scale down premium instances by stopping idle ones
        try:
            scale_down_if_possible()
        except Exception as scale_error:
            print(f"⚠️ Scale down failed but continuing: {str(scale_error)}")

        # 6. Immediately convert idle instances to standby if no premium users are left
        try:
            active_users = count_active_premium_users()
            if active_users == 0:
                print(
                    "No premium users remaining, converting idle "
                    "instances to standby immediately"
                )
                converted_count = convert_idle_instances_to_standby_immediate()
                if converted_count > 0:
                    print(
                        f"Immediately converted {converted_count} idle instances "
                        f"to standby after user logout"
                    )
        except Exception as standby_error:
            print(f"⚠️ Standby conversion failed but continuing: {str(standby_error)}")

        # Always return success - don't block user logout
        message = f"Premium user {user_id} release completed"
        if instance_id:
            message += f" from instance {instance_id}"
        if errors:
            message += f" (with {len(errors)} warnings)"

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": message,
                    "released_instance": instance_id,
                    "success": success,
                    "warnings": errors,
                }
            ),
        }

    except Exception as e:
        # Even on critical errors, return success to prevent blocking user logout
        error_msg = f"Error releasing premium user {user_id}: {str(e)}"
        print(f"❌ {error_msg}")
        return {
            "statusCode": 200,  # Still return 200 to not block logout
            "body": json.dumps(
                {
                    "message": f"Premium user {user_id} release completed with errors",
                    "released_instance": instance_id,
                    "success": False,
                    "error": error_msg,
                }
            ),
        }


def scale_down_if_possible():
    """Scale down premium instances by stopping idle instances"""
    ec2 = boto3.client("ec2")

    try:
        # Get dynamic capacity settings
        max_capacity = get_dynamic_max_capacity()
        active_users = count_active_premium_users()
        total_premium_users = count_total_premium_users()

        # Get all premium instances with their states
        all_instances = get_all_premium_instances_with_states()
        running_instances = [i for i in all_instances if i["state"] == "running"]

        total_instances = len(all_instances)
        occupied_instances = 0

        # Count occupied instances
        for instance in running_instances:
            instance_id = instance["instance_id"]
            assigned_users = get_assigned_users_for_instance(instance_id)
            if assigned_users:
                occupied_instances += 1

        idle_instances = len(running_instances) - occupied_instances

        print(
            f"Scale-down analysis: {total_instances} total, "
            f"{occupied_instances} occupied, "
            f"{idle_instances} idle, {active_users} active users, "
            f"{total_premium_users} total premium users"
            f" (max capacity: {max_capacity})"
        )

        # Conservative scale-down logic:
        # - Keep at least 1 running instance always
        # - Keep enough capacity for quick assignment (active users + 1)
        # - Only stop instances if we have significantly more than needed
        min_running_needed = max(
            1, active_users + 1
        )  # Active users + 1 for quick response

        if len(running_instances) > min_running_needed and idle_instances >= 2:
            # Stop idle instances conservatively
            instances_to_stop = min(
                idle_instances - 1, len(running_instances) - min_running_needed
            )

            # Find idle instances to stop
            idle_instance_ids = []
            for instance in running_instances:
                if len(idle_instance_ids) >= instances_to_stop:
                    break
                instance_id = instance["instance_id"]
                assigned_users = get_assigned_users_for_instance(instance_id)
                if not assigned_users:
                    idle_instance_ids.append(instance_id)

            if idle_instance_ids:
                print(
                    f"Stopping {len(idle_instance_ids)} idle instances: {idle_instance_ids} "
                    f"(min running needed: {min_running_needed})"
                )
                ec2.stop_instances(InstanceIds=idle_instance_ids)
            else:
                print("No idle instances found to stop")

        else:
            print(
                f"No scale-down: running={len(running_instances)}, min_needed={min_running_needed}, "
                f"idle={idle_instances}"
            )

    except Exception as e:
        print(f"Error scaling down premium instances: {str(e)}")


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


def convert_idle_instances_to_standby_immediate() -> int:
    """
    Immediately convert idle running instances to standby for cost optimization.
    This is called after user logout when no premium users remain.
    Returns the number of instances converted.
    """
    cleanup_count = 0

    try:
        # Get all running premium instances
        all_instances = get_all_premium_instances_with_states()
        running_instances = [i for i in all_instances if i["state"] == "running"]

        for instance in running_instances:
            instance_id = instance["instance_id"]

            # Check if instance has any assigned users
            assigned_users = get_assigned_users_for_instance(instance_id)

            if len(assigned_users) == 0:
                # Instance is idle - convert immediately
                print(
                    f"Converting idle instance {instance_id} to standby "
                    f"(immediate - no premium users)"
                )
                if convert_running_instance_to_standby(instance_id):
                    cleanup_count += 1

        print(f"Immediately converted {cleanup_count} idle instances to standby")
        return cleanup_count

    except Exception as e:
        print(f"Error in immediate standby conversion: {str(e)}")
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
                       SET is_standby = 1, standby_created_at = NOW(), last_state_check = NOW()
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


def process_shared_instance_optimization() -> Dict[str, Any]:
    """
    Optimize shared instances by migrating users to available dedicated instances.
    Called during assignment operations to improve resource allocation.
    """
    try:
        ec2 = boto3.client("ec2")
        premium_instance_ids = get_required_env_var("PREMIUM_INSTANCE_IDS").split(",")

        print("🔄 Checking for shared instance optimization opportunities")

        # Get all premium instances with detailed state information
        instances_response = ec2.describe_instances(InstanceIds=premium_instance_ids)

        available_instances = []
        shared_instances = []

        for reservation in instances_response["Reservations"]:
            for instance in reservation["Instances"]:
                instance_id = instance["InstanceId"]
                instance_state = instance["State"]["Name"]

                if instance_state == "running":
                    assigned_users = get_assigned_users_for_instance(instance_id)

                    if not assigned_users and check_instance_readiness(instance_id):
                        available_instances.append(instance_id)
                    elif len(assigned_users) > 1:  # Shared instance
                        shared_instances.append((instance_id, assigned_users))

        # Only migrate if we have available instances
        if not available_instances or not shared_instances:
            return {
                "migrations_performed": 0,
                "available_instances": len(available_instances),
                "shared_instances_found": len(shared_instances),
                "message": "No optimization opportunities found",
            }

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
                        f"✅ Optimized: Migrated user {user_id} to dedicated instance {new_instance_id}"
                    )
                else:
                    # Return instance to available list if migration failed
                    available_instances.append(new_instance_id)

        print(
            f"🔄 Shared instance optimization complete: {migrations_performed} users migrated"
        )

        return {
            "migrations_performed": migrations_performed,
            "shared_instances_found": len(shared_instances),
            "available_instances": len(available_instances),
            "message": f"Optimized {migrations_performed} user assignments",
        }

    except Exception as e:
        print(f"❌ Error during shared instance optimization: {str(e)}")
        return {"error": str(e), "migrations_performed": 0}


@with_transaction
def update_user_activity_timestamp(connection, user_id: str) -> bool:
    """Update activity timestamp for a user with proper transaction isolation"""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE premium_user_assignments
            SET last_activity = CURRENT_TIMESTAMP
            WHERE user_id = %s AND is_standby = 0
        """,
            (user_id,),
        )
        return cursor.rowcount > 0


def handle_activity_update(user_id: str) -> Dict[str, Any]:
    """Handle heartbeat activity update for a premium user"""
    try:
        print(f"💓 Processing activity update for user {user_id}")

        # Update the user's activity timestamp using transaction-safe function
        success = update_user_activity_timestamp(user_id)

        if success:
            return {
                "statusCode": 200,
                "body": json.dumps(
                    {
                        "message": f"Activity updated for user {user_id}",
                        "user_id": user_id,
                        "timestamp": time.time(),
                    }
                ),
            }
        else:
            # User might not have an active assignment - not necessarily an error
            return {
                "statusCode": 200,
                "body": json.dumps(
                    {
                        "message": f"No active assignment found for user {user_id}",
                        "user_id": user_id,
                        "updated": False,
                    }
                ),
            }

    except Exception as e:
        print(f"Error handling activity update for user {user_id}: {str(e)}")
        return {
            "statusCode": 200,  # Don't fail heartbeats
            "body": json.dumps(
                {
                    "message": f"Activity update completed with warnings for user {user_id}",
                    "user_id": user_id,
                    "error": str(e),
                }
            ),
        }


# cleanup_stale_assignments function moved to premium_cleanup Lambda


def update_user_activity(user_id: str) -> bool:
    """Update last_activity timestamp for a user's assignment"""
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE premium_user_assignments
                    SET last_activity = CURRENT_TIMESTAMP
                    WHERE user_id = %s AND is_standby = 0
                """,
                    (user_id,),
                )

                if cursor.rowcount > 0:
                    print(f"🔄 Updated activity timestamp for user {user_id}")
                    return True
                else:
                    print(
                        f"⚠️ No assignment found to update activity for user {user_id}"
                    )
                    return False

    except Exception as e:
        print(f"❌ Error updating activity for user {user_id}: {str(e)}")
        return False
