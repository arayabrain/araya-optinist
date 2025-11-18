"""
Free Manager Lambda Function

PRIMARY RESPONSIBILITIES:
- Monitor active free tier user count
- Proactively scale ECS service when user threshold reached
- Actively rebalance idle users across ALL instances (multi-instance algorithm)
- Wait for new instances to become ready (up to 10 minutes with retry)
- Preserve users with running workflows on their current instance

ARCHITECTURE NOTES:
- This Lambda handles proactive scaling and load rebalancing for free tier users
- Triggered by CloudWatch Events (every 5 minutes)
- Works in conjunction with ALB sticky sessions
    (reduced to 1 hour for better rebalancing)
- Timeout: 15 minutes (allows for 7-min instance launch + rebalancing)

IMPROVEMENTS IN THIS VERSION:
- Multi-instance rebalancing: Distributes users evenly across
    ALL instances, not just most/least
- Retry logic: Waits up to 10 minutes for new instances to launch before rebalancing
- Effectiveness checking: Verifies distribution is balanced after rebalancing
- Idle threshold: Reduced to 5 minutes (from 10) for faster migration eligibility

Required Environment Variables:
- RDS_HOST: Database host (format: host:port)
- RDS_USER: Database username
- RDS_PASSWORD: Database password
- RDS_DATABASE: Database name
- CLUSTER_NAME: ECS cluster name
- FREE_SERVICE_NAME: ECS service name for free tier
    (e.g., subscr-optinist-cloud-service)
- ASG_NAME: Auto Scaling Group name for free tier instances
- FREE_USER_THRESHOLD: Number of active users to trigger scaling (default: 5)
- FREE_IDLE_THRESHOLD_MINUTES: Minutes of inactivity to consider user idle (default: 5)
- MAX_FREE_INSTANCES: Maximum number of free tier instances (default: 10)
"""

import json
import os
from typing import Any, Dict, List

import boto3

# Import utility functions
from free_user_utils import (
    count_active_free_users,
    get_idle_users_for_instance,
    get_users_per_instance,
    is_distribution_balanced,
    migrate_user_to_instance,
)


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


# Initialize AWS clients
ecs_client = boto3.client("ecs")
autoscaling_client = boto3.client("autoscaling")
cloudwatch_client = boto3.client("cloudwatch")
ec2_client = boto3.client("ec2")


def handler(event, context):
    """
    Main Lambda handler for Free Manager.

    Triggered by:
    1. CloudWatch Event (every 5 minutes) - regular monitoring
    2. CloudWatch Alarm (when active users >= threshold) - immediate scaling

    Returns:
        Dictionary with status and actions taken
    """
    print("Free Manager Lambda triggered")
    print(f"Event: {json.dumps(event)}")

    try:
        # Get configuration
        user_threshold = int(get_required_env_var("FREE_USER_THRESHOLD", "5"))
        activity_threshold_minutes = int(
            get_required_env_var("FREE_IDLE_THRESHOLD_MINUTES", "10")
        )
        max_instances = int(get_required_env_var("MAX_FREE_INSTANCES", "10"))

        # Count active free tier users
        active_user_count = count_active_free_users(
            activity_threshold_minutes=activity_threshold_minutes
        )
        print(f"Active free tier users: {active_user_count}")

        # Publish metric to CloudWatch
        publish_active_user_metric(active_user_count)

        # Determine if scaling needed
        scaling_action_taken = False
        if active_user_count >= user_threshold:
            print(
                f"User threshold reached ({active_user_count} >= {user_threshold}), "
                f"initiating scaling and rebalancing"
            )
            result = scale_and_rebalance(
                active_user_count=active_user_count,
                max_instances=max_instances,
            )
            # Track if we performed a scaling action
            scaling_action_taken = result.get("scaling_action") in [
                "scale_up",
                "scale_down",
            ]
        else:
            print(
                f"User threshold not reached ({active_user_count} < {user_threshold}), "
                f"no scaling needed"
            )
            result = {
                "status": "no_action_needed",
                "active_users": active_user_count,
                "threshold": user_threshold,
            }

        # Only sync ECS service to running instances if we didn't just scale
        # If we just scaled up/down, the ASG and ECS are already set to the correct
        # desired counts, and syncing would undo the scaling (since new instances
        # take 7+ minutes to become InService)
        if not scaling_action_taken:
            print("\n" + "=" * 70)
            print("SYNCING ECS SERVICE TO RUNNING INSTANCES")
            print("=" * 70)
            sync_ecs_service_to_running_instances()
        else:
            print("\n" + "=" * 70)
            print("SKIPPING ECS SYNC - Scaling action was just performed")
            print("Will sync on next Lambda run after instances stabilize")
            print("=" * 70)

        return {"statusCode": 200, "body": json.dumps(result)}

    except Exception as e:
        print(f"Error in Free Manager Lambda: {e}")
        import traceback

        traceback.print_exc()
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


def scale_and_rebalance(
    active_user_count: int,
    max_instances: int,
) -> Dict[str, Any]:
    """
    Scale ECS service and rebalance idle users to new instances.

    Args:
        active_user_count: Current number of active free tier users
        max_instances: Maximum number of instances allowed

    Returns:
        Dictionary with scaling and rebalancing results
    """
    print("\n" + "=" * 70)
    print("SCALE AND REBALANCE")
    print("=" * 70)
    print(f"Active users: {active_user_count}")
    print(f"Max instances: {max_instances}")

    cluster_name = get_required_env_var("CLUSTER_NAME")
    service_name = get_required_env_var("FREE_SERVICE_NAME")

    # Get current service state
    service_info = get_service_info(cluster_name, service_name)
    current_desired = service_info["desired_count"]
    current_running = service_info["running_count"]

    print("\nCurrent state from get_service_info:")
    print(f"- Desired capacity (ASG): {current_desired}")
    print(f"- Running tasks (ECS): {current_running}")

    # Calculate desired instance count
    # Simple algorithm: 1 instance per 5 users, minimum 1, maximum max_instances
    desired_instances = min(max(1, (active_user_count + 4) // 5), max_instances)

    print(f"\nCalculated desired instances: {desired_instances}")
    print(f"Formula: min(max(1, ({active_user_count} + 4) // 5), {max_instances})")

    result = {
        "active_users": active_user_count,
        "current_instances": current_running,
        "desired_instances": desired_instances,
        "scaling_action": "none",
        "rebalanced_users": [],
    }

    # Scale up if needed
    if desired_instances > current_desired:
        print(f"Scaling up from {current_desired} to {desired_instances} instances")
        scale_service(cluster_name, service_name, desired_instances)
        result["scaling_action"] = "scale_up"

        # Wait for instances to launch and rebalance with retry logic
        # Instances take ~7 minutes to become fully operational
        # We'll retry for up to 10 minutes
        import time

        max_wait_time = 600  # 10 minutes in seconds
        check_interval = 60  # Check every 60 seconds
        start_time = time.time()
        attempt = 0

        print(
            f"\nWaiting for new instances to launch "
            f"(up to {max_wait_time // 60} minutes)..."
        )

        rebalanced = []
        rebalancing_successful = False

        while time.time() - start_time < max_wait_time:
            attempt += 1
            elapsed = int(time.time() - start_time)

            print(
                f"\n[Attempt {attempt}] Checking instance readiness "
                f"(elapsed: {elapsed}s / {max_wait_time}s)..."
            )

            # Check if desired number of instances are ready
            available_instances = get_available_instance_ids(cluster_name, service_name)
            print(
                f"Found {len(available_instances)}/{desired_instances} "
                f"running instances: {available_instances}"
            )

            if len(available_instances) >= desired_instances:
                print("All instances ready! Attempting rebalancing...")

                # Try multi-instance rebalancing
                rebalanced = rebalance_idle_users_multi(available_instances)

                # Check if rebalancing was effective
                distribution = get_users_per_instance()
                if is_distribution_balanced(distribution, tolerance=1):
                    print("Rebalancing successful - distribution is balanced!")
                    rebalancing_successful = True
                    break
                else:
                    print("Distribution still imbalanced, will retry...")

            else:
                print(
                    f"Only {len(available_instances)}/{desired_instances} instances "
                    f"ready, waiting {check_interval}s before next check..."
                )

            # Wait before next check (unless this is the last iteration)
            if time.time() - start_time + check_interval < max_wait_time:
                time.sleep(check_interval)
            else:
                print(
                    f"\nTimeout reached ({max_wait_time}s). "
                    f"Rebalancing will be retried on next Lambda run."
                )
                break

        result["rebalanced_users"] = rebalanced
        result["rebalancing_successful"] = rebalancing_successful
        result["rebalancing_attempts"] = attempt

    elif desired_instances < current_desired:
        # Scale down (conservative - only if significantly overprovisioned)
        if current_desired - desired_instances >= 2:
            print(
                f"Scaling down from {current_desired} to {desired_instances} instances"
            )
            scale_service(cluster_name, service_name, desired_instances)
            result["scaling_action"] = "scale_down"
        else:
            print("No scale down - within acceptable range")
            result["scaling_action"] = "none"
    else:
        # No scaling needed, but check if rebalancing needed
        print("No scaling needed, checking if rebalancing is beneficial...")

        available_instances = get_available_instance_ids(cluster_name, service_name)
        distribution = get_users_per_instance()

        # Build complete distribution including instances with 0 users
        # (get_users_per_instance only returns instances that have users)
        complete_distribution = {inst_id: 0 for inst_id in available_instances}
        complete_distribution.update(distribution)

        print(f"Complete distribution across all instances: {complete_distribution}")

        if not is_distribution_balanced(complete_distribution, tolerance=1):
            print("Distribution is imbalanced, attempting rebalancing...")
            rebalanced = rebalance_idle_users_multi(available_instances)
            result["rebalanced_users"] = rebalanced
            result["rebalancing_reason"] = "imbalance_without_scaling"
        else:
            print("Distribution is already balanced, no action needed")

    return result


def get_service_info(cluster_name: str, service_name: str) -> Dict[str, int]:
    """
    Get current ASG and ECS service information.

    For free tier, we check both the ASG desired capacity and ECS service
    status. The ASG desired capacity is the source of truth for scaling.

    Args:
        cluster_name: ECS cluster name
        service_name: ECS service name

    Returns:
        Dictionary with info (desired_count, running_count, pending_count)
    """
    print("\n" + "=" * 60)
    print("GET SERVICE INFO")
    print("=" * 60)

    asg_name = get_required_env_var("ASG_NAME")

    # Get ASG information
    print(f"Querying ASG: {asg_name}")
    asg_response = autoscaling_client.describe_auto_scaling_groups(
        AutoScalingGroupNames=[asg_name]
    )

    if not asg_response["AutoScalingGroups"]:
        raise Exception(f"Auto Scaling Group {asg_name} not found")

    asg = asg_response["AutoScalingGroups"][0]

    print("\nASG Status:")
    print(f"Desired Capacity: {asg['DesiredCapacity']}")
    print(f"Min Size: {asg['MinSize']}")
    print(f"Max Size: {asg['MaxSize']}")
    print(f"Instances: {len(asg['Instances'])}")

    for inst in asg["Instances"]:
        print(
            f"- {inst['InstanceId']}: {inst['LifecycleState']}, "
            f"Health={inst['HealthStatus']}"
        )

    # Get ECS service information
    print(f"\nQuerying ECS Service: {service_name}")
    print(f"Cluster: {cluster_name}")
    ecs_response = ecs_client.describe_services(
        cluster=cluster_name, services=[service_name]
    )

    if not ecs_response["services"]:
        raise Exception(f"Service {service_name} not found in cluster {cluster_name}")

    service = ecs_response["services"][0]

    print("\nECS Service Status:")
    print(f"Desired Count: {service['desiredCount']}")
    print(f"Running Count: {service['runningCount']}")
    print(f"Pending Count: {service['pendingCount']}")

    # List tasks
    print("\nQuerying ECS tasks...")
    tasks_response = ecs_client.list_tasks(
        cluster=cluster_name, serviceName=service_name, desiredStatus="RUNNING"
    )
    task_arns = tasks_response.get("taskArns", [])
    print(f"Running tasks: {len(task_arns)}")

    # Return ASG capacity as the desired count (source of truth)
    # but also include ECS running/pending counts
    result = {
        "desired_count": asg["DesiredCapacity"],
        "running_count": service["runningCount"],
        "pending_count": service["pendingCount"],
    }

    print("\nReturning:")
    print(f"desired_count (from ASG): {result['desired_count']}")
    print(f"running_count (from ECS): {result['running_count']}")
    print(f"pending_count (from ECS): {result['pending_count']}")
    print("=" * 60)

    return result


def scale_service(cluster_name: str, service_name: str, desired_count: int) -> None:
    """
    Update ASG desired capacity to scale the free tier instances.

    Unlike premium tier which uses individual EC2 instances, free tier
    uses an Auto Scaling Group (ASG) with ECS. We need to scale the ASG
    directly, not just the ECS service desired count.

    This is manual ASG management to avoid the runaway scaling issue that
    occurs with ECS managed scaling (where instance startup CPU spikes
    trigger additional scaling events).

    Args:
        cluster_name: ECS cluster name (used for logging only)
        service_name: ECS service name (used for logging only)
        desired_count: New desired capacity for the ASG
    """
    asg_name = get_required_env_var("ASG_NAME")

    print(f"Scaling ASG {asg_name} to desired capacity: {desired_count}")

    try:
        # Set ASG desired capacity directly
        autoscaling_client.set_desired_capacity(
            AutoScalingGroupName=asg_name,
            DesiredCapacity=desired_count,
            HonorCooldown=False,  # Immediate scaling, no cooldown wait
        )

        print(
            f"Successfully set ASG {asg_name} desired capacity to " f"{desired_count}"
        )

        # Also update ECS service desired count to match
        # This ensures ECS knows how many tasks should be running
        ecs_client.update_service(
            cluster=cluster_name, service=service_name, desiredCount=desired_count
        )

        print(
            f"Successfully updated ECS service {service_name} to "
            f"{desired_count} tasks"
        )

    except Exception as e:
        print(f"Error scaling ASG: {e}")
        raise


def sync_ecs_service_to_running_instances() -> None:
    """
    Sync ECS service desired count to match the number of running free tier instances.

    This ensures that each free tier instance has an ECS task running on it.
    Similar to premium manager's update_premium_service_desired_count().

    The function:
    1. Counts running free tier EC2 instances (InService in ASG)
    2. Updates the ECS service desired count to match
    3. ECS will then place one task per instance (with distinctInstance constraint)
    """
    try:
        cluster_name = get_required_env_var("CLUSTER_NAME")
        service_name = get_required_env_var("FREE_SERVICE_NAME")
        asg_name = get_required_env_var("ASG_NAME")

        # Get ASG information to count InService instances
        asg_response = autoscaling_client.describe_auto_scaling_groups(
            AutoScalingGroupNames=[asg_name]
        )

        if not asg_response["AutoScalingGroups"]:
            print(f"ASG {asg_name} not found")
            return

        asg = asg_response["AutoScalingGroups"][0]

        # Use ASG DesiredCapacity as the target, not InService count
        # This prevents undoing scale-up actions when instances are still launching
        asg_desired_capacity = asg["DesiredCapacity"]

        # Count instances that are InService (running and healthy)
        running_instance_count = sum(
            1 for inst in asg["Instances"] if inst["LifecycleState"] == "InService"
        )

        # Get current ECS service status
        service_response = ecs_client.describe_services(
            cluster=cluster_name, services=[service_name]
        )

        if not service_response.get("services"):
            print(f"Service {service_name} not found in cluster {cluster_name}")
            return

        current_desired_count = service_response["services"][0]["desiredCount"]
        current_running_count = service_response["services"][0]["runningCount"]

        print(f"ASG DesiredCapacity: {asg_desired_capacity}")
        print(f"ASG Instances InService: {running_instance_count}")
        print(
            f"ECS Service Status: desired={current_desired_count}, "
            f"running={current_running_count}"
        )

        # Sync ECS to ASG DesiredCapacity (not InService count)
        # This ensures ECS matches the scaling target,
        # even if instances are still launching
        if asg_desired_capacity != current_desired_count:
            print(
                f"Syncing ECS service desired count: {current_desired_count} "
                f"→ {asg_desired_capacity}"
            )
            ecs_client.update_service(
                cluster=cluster_name,
                service=service_name,
                desiredCount=asg_desired_capacity,
            )
            print(
                f"ECS service {service_name} updated to desired count "
                f"{asg_desired_capacity}"
            )
        else:
            print(
                f"ECS service desired count already matches ASG desired capacity "
                f"({asg_desired_capacity})"
            )

    except Exception as e:
        print(f"Error syncing ECS service to running instances: {str(e)}")
        import traceback

        traceback.print_exc()


def rebalance_idle_users_multi(available_instances: List[str]) -> List[str]:
    """
    Rebalance idle users across ALL available instances (multi-instance algorithm).

    This improved algorithm:
    1. Calculates target users per instance (even distribution)
    2. Identifies all overloaded and underloaded instances
    3. Migrates users from overloaded to underloaded instances in round-robin fashion
    4. Ensures balanced distribution across all instances,
        not just most/least loaded pair

    Idle users are defined as currently having no active workflows
    (active_workflow_count = 0).

    Args:
        available_instances: List of available instance IDs to distribute users across

    Returns:
        List of user IDs that were migrated
    """
    print("\n" + "=" * 70)
    print("MULTI-INSTANCE REBALANCING")
    print("=" * 70)
    print(f"Available instances: {available_instances}")

    if len(available_instances) < 2:
        print(
            f"Cannot rebalance: Only {len(available_instances)} instance(s) available"
        )
        return []

    # Get current user distribution
    print("\nGetting current user distribution...")
    users_per_instance = get_users_per_instance()

    # Build complete instance map (includes instances with 0 users)
    instance_user_counts = {inst_id: 0 for inst_id in available_instances}
    instance_user_counts.update(users_per_instance)

    print(f"User distribution: {instance_user_counts}")

    total_users = sum(instance_user_counts.values())
    if total_users == 0:
        print("No active users to rebalance")
        return []

    # Calculate target users per instance (even distribution)
    target_per_instance = total_users // len(available_instances)
    print(
        f"Target distribution: {target_per_instance} users per instance "
        f"({total_users} users / {len(available_instances)} instances)"
    )

    # Identify overloaded instances (above target + tolerance)
    overloaded = [
        (inst, count)
        for inst, count in instance_user_counts.items()
        if count > target_per_instance + 1  # Allow 1 user difference
    ]

    # Identify underloaded instances (below target)
    underloaded = [
        (inst, count)
        for inst, count in instance_user_counts.items()
        if count < target_per_instance
    ]

    if not overloaded or not underloaded:
        print("Instances are already balanced")
        return []

    print(f"\nOverloaded instances: {overloaded}")
    print(f"Underloaded instances: {underloaded}")

    # Sort by severity
    overloaded.sort(key=lambda x: x[1], reverse=True)  # Most loaded first
    underloaded.sort(key=lambda x: x[1])  # Least loaded first

    migrated = []
    underloaded_idx = 0  # Round-robin index for destination instances

    # Migrate users from each overloaded instance
    for source_inst, source_count in overloaded:
        users_to_move = source_count - target_per_instance

        print(
            f"\nProcessing overloaded instance {source_inst}: "
            f"{source_count} users (need to move {users_to_move})"
        )

        # Get idle users from this instance
        idle_users = get_idle_users_for_instance(source_inst)

        if not idle_users:
            print(f"No idle users on instance {source_inst}, skipping")
            continue

        # Limit to number of users we need to move
        idle_users_to_migrate = idle_users[:users_to_move]
        print(
            f"Found {len(idle_users)} idle users, "
            f"migrating {len(idle_users_to_migrate)}"
        )

        # Distribute to underloaded instances in round-robin fashion
        for user_id in idle_users_to_migrate:
            if underloaded_idx >= len(underloaded):
                print("All underloaded instances have reached target, stopping")
                break

            dest_inst, _ = underloaded[underloaded_idx]

            # Attempt migration
            if migrate_user_to_instance(user_id, dest_inst):
                migrated.append(user_id)

                # Update counts
                instance_user_counts[source_inst] -= 1
                instance_user_counts[dest_inst] += 1

                # Move to next underloaded instance if current reached target
                if instance_user_counts[dest_inst] >= target_per_instance:
                    underloaded_idx += 1

    print(f"\n{len(migrated)} users migrated successfully")
    print(f"New distribution: {instance_user_counts}")

    return migrated


def rebalance_idle_users() -> List[str]:
    """
    Rebalance idle users to underutilized instances.

    This function:
    1. Discovers all available ECS instances
    2. Gets current user distribution across instances
    3. Identifies idle users on overloaded instances
    4. Migrates idle users to underutilized instances

    Idle users are defined as currently having no active workflows
    (active_workflow_count = 0).

    Returns:
        List of user IDs that were migrated
    """
    print("\n" + "=" * 70)
    print("REBALANCING IDLE USERS")
    print("=" * 70)

    # Get all available ECS instances
    cluster_name = get_required_env_var("CLUSTER_NAME")
    service_name = get_required_env_var("FREE_SERVICE_NAME")

    print(f"\nDiscovering available instances from cluster: {cluster_name}")
    available_instances = get_available_instance_ids(cluster_name, service_name)

    print(f"\nFound {len(available_instances)} available instances")

    if len(available_instances) < 2:
        print(
            f"\nCannot rebalance: Only {len(available_instances)} "
            f"instance(s) available, need at least 2"
        )
        print("=" * 70)
        return []

    print(f"Available instances for rebalancing: {available_instances}")

    # Get current user distribution
    print("\nGetting current user distribution from database...")
    users_per_instance = get_users_per_instance()

    print(f"User distribution from DB: {users_per_instance}")

    if not users_per_instance:
        print("\nNo active users to rebalance")
        print("=" * 70)
        return []

    # Build complete instance map (includes instances with 0 users)
    instance_user_counts = {inst_id: 0 for inst_id in available_instances}
    instance_user_counts.update(users_per_instance)

    print(f"Complete instance map: {instance_user_counts}")

    # Find most loaded and least loaded instances
    sorted_instances = sorted(
        instance_user_counts.items(), key=lambda x: x[1], reverse=True
    )

    most_loaded_instance = sorted_instances[0][0]
    least_loaded_instance = sorted_instances[-1][0]

    most_loaded_count = sorted_instances[0][1]
    least_loaded_count = sorted_instances[-1][1]

    print(
        f"Most loaded: {most_loaded_instance} ({most_loaded_count} users), "
        f"Least loaded: {least_loaded_instance} ({least_loaded_count} users)"
    )

    # Only rebalance if there's significant imbalance
    if most_loaded_count - least_loaded_count < 2:
        print("Instances are reasonably balanced, no rebalancing needed")
        return []

    # Get idle users from most loaded instance
    idle_users = get_idle_users_for_instance(most_loaded_instance)

    if not idle_users:
        print(f"No idle users on instance {most_loaded_instance}")
        return []

    print(f"Found {len(idle_users)} idle users on {most_loaded_instance}")

    # Migrate up to 50% of idle users (or enough to balance)
    users_to_migrate = min(
        len(idle_users), (most_loaded_count - least_loaded_count) // 2
    )

    migrated = []
    for user_id in idle_users[:users_to_migrate]:
        if migrate_user_to_instance(user_id, least_loaded_instance):
            migrated.append(user_id)

    print(f"Successfully migrated {len(migrated)} users")
    return migrated


def get_available_instance_ids(cluster_name: str, service_name: str) -> List[str]:
    """
    Get list of RUNNING EC2 instance IDs from ECS cluster.

    Only returns instances that are:
    1. Registered with ECS cluster (ACTIVE container instances)
    2. Have ECS agent connected
    3. Are in 'running' state in EC2 (not pending/stopping/stopped)

    This ensures we only try to rebalance users to instances that are
    actually ready to handle traffic.

    Args:
        cluster_name: ECS cluster name
        service_name: ECS service name (unused, kept for compatibility)

    Returns:
        List of EC2 instance IDs that are fully running and ready
    """
    print("=" * 60)
    print("INSTANCE DISCOVERY DEBUG")
    print("=" * 60)
    print(f"Cluster: {cluster_name}")
    print(f"Service: {service_name}")

    try:
        # List all container instances in the cluster
        # Check all statuses to find available instances
        print("\nStep 1: Listing container instances in ECS cluster...")

        all_container_arns = []
        for status in ["ACTIVE", "DRAINING", "REGISTERING"]:
            response = ecs_client.list_container_instances(
                cluster=cluster_name, status=status
            )
            arns = response.get("containerInstanceArns", [])
            if arns:
                print(f"Found {len(arns)} {status} container instances")
                all_container_arns.extend(arns)

        print(f"Total container instances found: {len(all_container_arns)}")

        if not all_container_arns:
            print("No container instances found in cluster")
            return []

        # Describe container instances to get EC2 instance IDs
        print("\nStep 2: Describing container instances...")
        instances_response = ecs_client.describe_container_instances(
            cluster=cluster_name, containerInstances=all_container_arns
        )

        print("\nContainer instance details:")
        all_ecs_instances = []
        for inst in instances_response["containerInstances"]:
            ec2_id = inst["ec2InstanceId"]
            status = inst["status"]
            agent_connected = inst["agentConnected"]
            running_tasks = inst["runningTasksCount"]

            print(f"- {ec2_id}:")
            print(f"Status: {status}")
            print(f"Agent Connected: {agent_connected}")
            print(f"Running Tasks: {running_tasks}")

            all_ecs_instances.append(
                {"id": ec2_id, "status": status, "agent": agent_connected}
            )

        # Get EC2 instance IDs that can accept tasks
        # Include ACTIVE, DRAINING (can still run tasks), and REGISTERING
        # Exclude instances with disconnected agents
        ecs_instance_ids = [
            inst["ec2InstanceId"]
            for inst in instances_response["containerInstances"]
            if inst["status"] in ["ACTIVE", "DRAINING", "REGISTERING"]
            and inst["agentConnected"]
        ]

        print(
            f"\nFiltered for (ACTIVE/DRAINING/REGISTERING) + agentConnected: "
            f"{len(ecs_instance_ids)} instances"
        )
        print(f"Instance IDs: {ecs_instance_ids}")

        if not ecs_instance_ids:
            print("No ECS instances with connected agents")
            print(f"All instances: {all_ecs_instances}")
            return []

        # Check EC2 state to ensure instances are actually running
        print("\nStep 3: Checking EC2 instance states...")
        ec2_response = ec2_client.describe_instances(InstanceIds=ecs_instance_ids)

        running_instances = []
        pending_instances = []
        other_states = []

        for reservation in ec2_response["Reservations"]:
            for instance in reservation["Instances"]:
                instance_id = instance["InstanceId"]
                state = instance["State"]["Name"]

                print(f"- {instance_id}: EC2 state = {state}")

                if state == "running":
                    running_instances.append(instance_id)
                elif state == "pending":
                    pending_instances.append(instance_id)
                else:
                    other_states.append((instance_id, state))

        print("\n" + "=" * 60)
        print("INSTANCE DISCOVERY SUMMARY")
        print("=" * 60)
        print(f"Total ECS-registered: {len(ecs_instance_ids)}")
        print(f"Running instances: {len(running_instances)} " f"{running_instances}")
        print(f"Pending instances: {len(pending_instances)} " f"{pending_instances}")
        if other_states:
            print(f"Other states: {other_states}")
        print("=" * 60)

        # Only return running instances
        return running_instances

    except Exception as e:
        print(f"ERROR in get_available_instance_ids: {e}")
        import traceback

        traceback.print_exc()
        return []


def publish_active_user_metric(active_user_count: int) -> None:
    """
    Publish active free tier user count to CloudWatch.

    Args:
        active_user_count: Number of active free tier users
    """
    try:
        cloudwatch_client.put_metric_data(
            Namespace="OptiNiSt/FreeUsers",
            MetricData=[
                {
                    "MetricName": "ActiveLogins",
                    "Value": active_user_count,
                    "Unit": "Count",
                }
            ],
        )
        print(f"Published CloudWatch metric: ActiveLogins={active_user_count}")
    except Exception as e:
        print(f"Failed to publish CloudWatch metric: {e}")
        # Don't fail the Lambda if metric publishing fails


# For testing
if __name__ == "__main__":
    # Simulate CloudWatch Event trigger
    test_event = {"source": "aws.events", "detail-type": "Scheduled Event"}
    test_context = {}

    result = handler(test_event, test_context)
    print(f"Test result: {json.dumps(result, indent=2)}")
