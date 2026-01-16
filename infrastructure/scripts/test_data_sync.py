#!/usr/bin/env python3
"""
Manual testing script for experiment data sync between instances.

Tests that experiment metadata syncs correctly when users are migrated between
instances, preventing 404 errors when accessing experiments after migration.

Two sync mechanisms are available:
    - Lazy Sync: On-demand sync when user accesses an experiment (default)
    - Proactive Sync: Immediate bulk sync triggered via internal API

Run from inside ECS container:
    1. Use infrastructure/terraform/container_access.sh to get shell access
    2. pip install pymysql requests boto3 (if not installed)
    3. python /path/to/test_data_sync.py <command>

Commands:
    find-user <email>      Find user by email (partial match)
    status <user_id>       Get user's current instance assignment
    list-instances         List all instances with user counts
    asg-status             Show ASG capacity and instance status
    migrate <user_id>      Migrate user to different instance (auto-scales if needed)
    trigger-sync <user_id> Trigger proactive sync via internal API
    clear-local <user_id>  Clear local experiment files (simulates fresh instance)

Example commands:
    python test_data_sync.py find-user test@example.com
    python test_data_sync.py status 42
    python test_data_sync.py migrate 42                   # Lazy sync (default)
    python test_data_sync.py migrate 42 --proactive       # With proactive sync
    python test_data_sync.py migrate 42 i-0abc123         # Explicit target
    python test_data_sync.py trigger-sync 42              # Trigger sync only
    python test_data_sync.py clear-local 42               # Clear all experiments
    python test_data_sync.py clear-local 42 abc123        # Clear specific experiment


Workflow A - Test Lazy Sync:
    1. Log in with test user and run experiment on current instance
    2. Find user by email to get user ID
    3. Clear local experiment files to simulate fresh instance:
       python test_data_sync.py clear-local <user_id> <unique_id>
    4. Reproduce the experiment in RECORDS page - confirm no 404 error
    5. Check logs for: "Experiment config not found locally, syncing from S3"

Workflow B - Test Proactive Sync:
    1. Log in with test user and run experiment on current instance
    2. Find user by email to get user ID
    3. Clear local experiment files:
       python test_data_sync.py clear-local <user_id>
    4. Trigger proactive sync:
       python test_data_sync.py trigger-sync <user_id>
    5. Check logs for: "Experiment sync initiated" and "sync completed"
    6. Reproduce experiment - files should already be present (no lazy sync msg)

"""

import argparse
import os
import sys
import time


def load_config():
    """Load configuration from ECS container environment variables.

    Container uses DB_* vars. Falls back to MYSQL_* for compatibility.
    """
    if not os.environ.get("DB_HOST"):
        mysql_server = os.environ.get("MYSQL_SERVER", "")
        if ":" in mysql_server:
            mysql_server = mysql_server.split(":")[0]
        os.environ["DB_HOST"] = mysql_server
    if not os.environ.get("DB_USER"):
        os.environ["DB_USER"] = os.environ.get("MYSQL_USER", "")
    if not os.environ.get("DB_PASSWORD"):
        os.environ["DB_PASSWORD"] = os.environ.get("MYSQL_PASSWORD", "")
    if not os.environ.get("DB_NAME"):
        os.environ["DB_NAME"] = os.environ.get("MYSQL_DATABASE", "")


def get_db_connection():
    """Get database connection using environment variables."""
    import pymysql

    return pymysql.connect(
        host=os.environ["DB_HOST"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        database=os.environ["DB_NAME"],
        cursorclass=pymysql.cursors.DictCursor,
    )


def find_user_by_email(email: str) -> list:
    """Find user by email address."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT u.id, u.email, u.name,
                       f.instance_id, f.last_activity
                FROM users u
                LEFT JOIN free_user_assignments f ON u.id = f.user_id
                WHERE u.email LIKE %s
                ORDER BY u.id
                """,
                (f"%{email}%",),
            )
            return cursor.fetchall()
    finally:
        conn.close()


def get_user_status(user_id: int) -> dict:
    """Get current user assignment status."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT u.id, u.email, u.name
                FROM users u
                WHERE u.id = %s
                """,
                (user_id,),
            )
            user = cursor.fetchone()

            if not user:
                return {"error": f"User {user_id} not found"}

            cursor.execute(
                """
                SELECT instance_id, last_activity, assigned_at,
                       migration_count, active_workflow_count
                FROM free_user_assignments
                WHERE user_id = %s
                """,
                (user_id,),
            )
            assignment = cursor.fetchone()

            return {"user": user, "assignment": assignment}
    finally:
        conn.close()


def list_instances() -> list:
    """List all instances with user counts."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT instance_id,
                       COUNT(*) as user_count,
                       MAX(last_activity) as latest_activity
                FROM free_user_assignments
                GROUP BY instance_id
                ORDER BY user_count DESC
                """
            )
            return cursor.fetchall()
    finally:
        conn.close()


def migrate_user(user_id: int, target_instance: str) -> dict:
    """Migrate user to target instance (File sync handled by lazy loading)."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT instance_id, active_workflow_count
                FROM free_user_assignments
                WHERE user_id = %s
                """,
                (user_id,),
            )
            current = cursor.fetchone()

            if not current:
                return {"error": f"No assignment found for user {user_id}"}

            if current["active_workflow_count"] > 0:
                return {
                    "error": f"User has {current['active_workflow_count']} "
                    "active workflows - cannot migrate"
                }

            old_instance = current["instance_id"]

            cursor.execute(
                """
                UPDATE free_user_assignments
                SET instance_id = %s,
                    migration_count = migration_count + 1,
                    last_migration = NOW()
                WHERE user_id = %s
                """,
                (target_instance, user_id),
            )
            conn.commit()

            return {
                "status": "migrated",
                "user_id": user_id,
                "from_instance": old_instance,
                "to_instance": target_instance,
            }
    finally:
        conn.close()


def get_user_workspaces(user_id: int) -> list:
    """Get all workspace IDs for a user."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id FROM workspaces WHERE user_id = %s
                """,
                (user_id,),
            )
            return [row["id"] for row in cursor.fetchall()]
    finally:
        conn.close()


def get_workspace_experiments(workspace_id: str) -> list:
    """Get all experiment unique_ids in a workspace directory."""
    output_dir = os.environ.get("OUTPUT_DIR", "/tmp/studio/output")
    workspace_path = os.path.join(output_dir, workspace_id)

    if not os.path.isdir(workspace_path):
        return []

    # List subdirectories (each is an experiment)
    experiments = []
    for item in os.listdir(workspace_path):
        item_path = os.path.join(workspace_path, item)
        if os.path.isdir(item_path):
            experiments.append(item)
    return experiments


def clear_local_experiment(workspace_id: str, unique_id: str) -> dict:
    """Clear local experiment metadata files.

    Deletes the experiment.yaml, workflow.yaml, and snakemake_config.yaml
    files for a specific experiment, simulating a fresh instance that
    needs to sync from S3.

    Args:
        workspace_id: Workspace ID containing the experiment
        unique_id: Unique ID of the experiment

    Returns:
        Dict with status and list of deleted files
    """
    output_dir = os.environ.get("OUTPUT_DIR", "/tmp/studio/output")
    experiment_path = os.path.join(output_dir, workspace_id, unique_id)

    if not os.path.isdir(experiment_path):
        return {
            "error": f"Experiment directory not found: {experiment_path}",
            "workspace_id": workspace_id,
            "unique_id": unique_id,
        }

    # Metadata files that lazy sync downloads
    metadata_files = ["experiment.yaml", "workflow.yaml", "snakemake_config.yaml"]
    deleted = []

    for filename in metadata_files:
        filepath = os.path.join(experiment_path, filename)
        if os.path.isfile(filepath):
            os.remove(filepath)
            deleted.append(filename)

    return {
        "status": "cleared",
        "workspace_id": workspace_id,
        "unique_id": unique_id,
        "deleted_files": deleted,
        "experiment_path": experiment_path,
    }


def clear_local_experiments_for_user(user_id: int, unique_id: str = None) -> dict:
    """Clear local experiment files for a user.

    If unique_id is provided, clears only that experiment.
    Otherwise, clears all experiments across all user's workspaces.

    Args:
        user_id: User ID to clear experiments for
        unique_id: Optional specific experiment to clear

    Returns:
        Dict with status and summary of cleared files
    """
    workspaces = get_user_workspaces(user_id)

    if not workspaces:
        return {"error": f"No workspaces found for user {user_id}"}

    results = []
    total_deleted = 0

    for workspace_id in workspaces:
        if unique_id:
            # Clear specific experiment
            result = clear_local_experiment(workspace_id, unique_id)
            if "error" not in result:
                results.append(result)
                total_deleted += len(result.get("deleted_files", []))
                break  # Found the experiment, stop searching
        else:
            # Clear all experiments in workspace
            experiments = get_workspace_experiments(workspace_id)
            for exp_id in experiments:
                result = clear_local_experiment(workspace_id, exp_id)
                if "error" not in result:
                    results.append(result)
                    total_deleted += len(result.get("deleted_files", []))

    if not results:
        if unique_id:
            return {"error": f"Experiment {unique_id} not found for user {user_id}"}
        else:
            return {"error": f"No local experiments found for user {user_id}"}

    return {
        "status": "cleared",
        "user_id": user_id,
        "experiments_cleared": len(results),
        "files_deleted": total_deleted,
        "details": results,
    }


def trigger_proactive_sync(user_id: int) -> dict:
    """Trigger proactive sync via internal API.

    Calls the /system-internal/sync-experiments endpoint to trigger
    immediate bulk sync of all experiment metadata for the user.

    Requires INTERNAL_API_SECRET and ALB_DNS_NAME environment variables.
    """
    import requests

    alb_dns = os.environ.get("ALB_DNS_NAME")
    internal_secret = os.environ.get("INTERNAL_API_SECRET")

    if not alb_dns:
        return {"error": "ALB_DNS_NAME environment variable is required"}
    if not internal_secret:
        return {"error": "INTERNAL_API_SECRET environment variable is required"}

    url = f"https://{alb_dns}/system-internal/sync-experiments/{user_id}"
    headers = {
        "X-Internal-Secret": internal_secret,
        "Content-Type": "application/json",
    }

    try:
        print(f"Triggering proactive sync for user {user_id}...")
        response = requests.post(url, headers=headers, timeout=30.0, verify=True)

        if response.status_code == 200:
            return {
                "status": "sync_initiated",
                "user_id": user_id,
                "response": response.json(),
            }
        elif response.status_code == 429:
            return {
                "error": "Rate limited - sync request too frequent",
                "status_code": response.status_code,
            }
        else:
            return {
                "error": f"Sync request failed: {response.status_code}",
                "status_code": response.status_code,
                "response": response.text,
            }
    except requests.exceptions.SSLError:
        # Try without SSL verification for internal testing
        print("SSL verification failed, retrying without verification...")
        try:
            response = requests.post(url, headers=headers, timeout=30.0, verify=False)
            if response.status_code == 200:
                return {
                    "status": "sync_initiated",
                    "user_id": user_id,
                    "response": response.json(),
                    "warning": "SSL verification disabled",
                }
            else:
                return {
                    "error": f"Sync request failed: {response.status_code}",
                    "status_code": response.status_code,
                }
        except Exception as e2:
            return {"error": f"Request failed: {e2}"}
    except Exception as e:
        return {"error": f"Request failed: {e}"}


# =============================================================================
# ASG and ECS Auto-Scaling Functions
# =============================================================================

# Required environment variables for ASG/ECS operations:
#   AWS_REGION    - AWS region (e.g., ap-northeast-1)
#   ASG_NAME      - Auto Scaling Group name
#   ECS_CLUSTER   - ECS cluster name
#   ECS_SERVICE   - ECS service name


def get_aws_region() -> str:
    """Get AWS region from environment (required)."""
    region = os.environ.get("AWS_REGION")
    if not region:
        raise ValueError("AWS_REGION environment variable is required")
    return region


def get_asg_client():
    """Get boto3 autoscaling client."""
    import boto3

    return boto3.client("autoscaling", region_name=get_aws_region())


def get_ecs_client():
    """Get boto3 ECS client."""
    import boto3

    return boto3.client("ecs", region_name=get_aws_region())


def get_ecs_cluster() -> str:
    """Get ECS cluster name from environment (required)."""
    cluster = os.environ.get("ECS_CLUSTER")
    if not cluster:
        raise ValueError("ECS_CLUSTER environment variable is required")
    return cluster


def get_ecs_service() -> str:
    """Get ECS service name from environment (required)."""
    service = os.environ.get("ECS_SERVICE")
    if not service:
        raise ValueError("ECS_SERVICE environment variable is required")
    return service


def get_asg_name() -> str:
    """Get ASG name from environment (required)."""
    asg_name = os.environ.get("ASG_NAME")
    if not asg_name:
        raise ValueError("ASG_NAME environment variable is required")
    return asg_name


def get_asg_status() -> dict:
    """Get current ASG status including capacity and instance details."""
    try:
        asg_client = get_asg_client()
        asg_name = get_asg_name()

        response = asg_client.describe_auto_scaling_groups(
            AutoScalingGroupNames=[asg_name]
        )

        if not response["AutoScalingGroups"]:
            return {"error": f"ASG not found: {asg_name}"}

        asg = response["AutoScalingGroups"][0]

        instances = []
        for inst in asg["Instances"]:
            instances.append(
                {
                    "instance_id": inst["InstanceId"],
                    "lifecycle_state": inst["LifecycleState"],
                    "health_status": inst["HealthStatus"],
                }
            )

        healthy_instances = [
            i
            for i in instances
            if i["lifecycle_state"] == "InService" and i["health_status"] == "Healthy"
        ]

        return {
            "asg_name": asg_name,
            "desired_capacity": asg["DesiredCapacity"],
            "min_size": asg["MinSize"],
            "max_size": asg["MaxSize"],
            "instances": instances,
            "healthy_instance_ids": [i["instance_id"] for i in healthy_instances],
            "healthy_count": len(healthy_instances),
        }
    except Exception as e:
        return {"error": str(e)}


def suspend_asg_scaling() -> dict:
    """Suspend ASG scaling to prevent auto-scaling interference during testing."""
    try:
        asg_client = get_asg_client()
        asg_name = get_asg_name()

        print(f"Suspending scaling processes for ASG '{asg_name}'...")
        asg_client.suspend_processes(
            AutoScalingGroupName=asg_name,
            ScalingProcesses=["AlarmNotification", "ScheduledActions"],
        )

        return {"status": "suspended", "asg_name": asg_name}
    except Exception as e:
        return {"error": str(e)}


def resume_asg_scaling() -> dict:
    """Resume ASG scaling processes after testing."""
    try:
        asg_client = get_asg_client()
        asg_name = get_asg_name()

        print(f"Resuming scaling processes for ASG '{asg_name}'...")
        asg_client.resume_processes(
            AutoScalingGroupName=asg_name,
            ScalingProcesses=["AlarmNotification", "ScheduledActions"],
        )

        return {"status": "resumed", "asg_name": asg_name}
    except Exception as e:
        return {"error": str(e)}


def scale_asg(desired_capacity: int) -> dict:
    """Scale ASG to desired capacity."""
    try:
        asg_client = get_asg_client()
        asg_name = get_asg_name()

        # Get current status first
        status = get_asg_status()
        if "error" in status:
            return status

        current = status["desired_capacity"]
        max_size = status["max_size"]

        if desired_capacity > max_size:
            return {
                "error": f"Desired capacity {desired_capacity} exceeds max {max_size}"
            }

        if desired_capacity == current:
            return {
                "status": "no_change",
                "message": f"ASG already at desired capacity {current}",
            }

        print(f"Scaling ASG '{asg_name}' from {current} to {desired_capacity}...")
        asg_client.set_desired_capacity(
            AutoScalingGroupName=asg_name,
            DesiredCapacity=desired_capacity,
        )

        return {
            "status": "scaling",
            "from_capacity": current,
            "to_capacity": desired_capacity,
        }
    except Exception as e:
        return {"error": str(e)}


def wait_for_healthy_instances(
    target_count: int,
    timeout_seconds: int = 1000,
    poll_interval: int = 10,
    print_interval: int = 60,
) -> dict:
    """Wait for ASG to have target number of healthy instances."""
    print(
        f"Waiting for {target_count} healthy instances (timeout: {timeout_seconds}s)..."
    )

    start_time = time.time()
    last_print = -print_interval  # Print immediately on first check

    while (time.time() - start_time) < timeout_seconds:
        status = get_asg_status()
        if "error" in status:
            return status

        healthy_count = status["healthy_count"]
        elapsed = int(time.time() - start_time)

        if healthy_count >= target_count:
            print(f"{healthy_count}/{target_count} healthy ({elapsed}s elapsed)")
            return {
                "status": "ready",
                "healthy_count": healthy_count,
                "healthy_instance_ids": status["healthy_instance_ids"],
                "elapsed_seconds": elapsed,
            }

        # Print summary every print_interval seconds
        if elapsed - last_print >= print_interval:
            print(f"{healthy_count}/{target_count} healthy ({elapsed}s elapsed)")
            last_print = elapsed

        time.sleep(poll_interval)

    return {
        "error": f"Timeout waiting for {target_count} healthy instances",
        "current_count": healthy_count,
        "elapsed_seconds": int(time.time() - start_time),
    }


def get_ecs_service_status() -> dict:
    """Get current ECS service status including task counts."""
    try:
        ecs_client = get_ecs_client()
        cluster = get_ecs_cluster()
        service = get_ecs_service()

        response = ecs_client.describe_services(cluster=cluster, services=[service])

        if not response["services"]:
            return {"error": f"ECS service not found: {service}"}

        svc = response["services"][0]

        return {
            "cluster": cluster,
            "service": service,
            "desired_count": svc["desiredCount"],
            "running_count": svc["runningCount"],
            "pending_count": svc["pendingCount"],
            "status": svc["status"],
        }
    except Exception as e:
        return {"error": str(e)}


def scale_ecs_service(desired_count: int) -> dict:
    """Scale ECS service to desired task count."""
    try:
        ecs_client = get_ecs_client()
        cluster = get_ecs_cluster()
        service = get_ecs_service()

        # Get current status first
        status = get_ecs_service_status()
        if "error" in status:
            return status

        current = status["desired_count"]

        if desired_count == current:
            return {
                "status": "no_change",
                "message": f"ECS service already at desired count {current}",
            }

        print(f"Scaling ECS '{service}' from {current} to {desired_count} tasks...")
        ecs_client.update_service(
            cluster=cluster,
            service=service,
            desiredCount=desired_count,
        )

        return {
            "status": "scaling",
            "from_count": current,
            "to_count": desired_count,
        }
    except Exception as e:
        return {"error": str(e)}


def wait_for_running_tasks(
    target_count: int,
    timeout_seconds: int = 1000,
    poll_interval: int = 10,
    print_interval: int = 60,
) -> dict:
    """Wait for ECS service to have target number of running tasks."""
    print(f"Waiting for {target_count} running tasks (timeout: {timeout_seconds}s)...")

    start_time = time.time()
    last_print = -print_interval  # Print immediately on first check

    while (time.time() - start_time) < timeout_seconds:
        status = get_ecs_service_status()
        if "error" in status:
            return status

        running_count = status["running_count"]
        pending_count = status["pending_count"]
        elapsed = int(time.time() - start_time)

        if running_count >= target_count:
            print(
                f"{running_count}/{target_count} tasks running, "
                f"{pending_count} pending ({elapsed}s elapsed)"
            )
            return {
                "status": "ready",
                "running_count": running_count,
                "elapsed_seconds": elapsed,
            }

        # Print summary every print_interval seconds
        if elapsed - last_print >= print_interval:
            print(
                f"{running_count}/{target_count} tasks running, "
                f"{pending_count} pending ({elapsed}s elapsed)"
            )
            last_print = elapsed

        time.sleep(poll_interval)

    return {
        "error": f"Timeout waiting for {target_count} running tasks",
        "current_count": running_count,
        "elapsed_seconds": int(time.time() - start_time),
    }


def ensure_multiple_instances(current_instance: str) -> dict:
    """Ensure there are at least 2 healthy instances with running tasks for migration.

    If only 1 instance exists, scales up ASG and ECS service, then waits for:
    1. New EC2 instance to become healthy in ASG
    2. ECS task to start running on the new instance

    Returns the list of available target instances (excluding current).
    """
    asg_status = get_asg_status()
    if "error" in asg_status:
        return asg_status

    healthy_ids = asg_status["healthy_instance_ids"]
    healthy_count = asg_status["healthy_count"]

    # Filter out current instance to get available targets
    available_targets = [i for i in healthy_ids if i != current_instance]

    # Also check ECS tasks are running
    ecs_status = get_ecs_service_status()
    if "error" in ecs_status:
        return ecs_status

    running_tasks = ecs_status["running_count"]

    # If we have available target instances AND enough running tasks, we're ready
    if available_targets and running_tasks >= 2:
        return {
            "status": "ready",
            "available_targets": available_targets,
            "scaled_up": False,
        }

    # Need to scale up
    print(f"Only {healthy_count} instance(s) and {running_tasks} task(s) available.")
    print("Scaling up ASG and ECS service to 2...")

    # Suspend auto-scaling to prevent interference during scale-up
    suspend_result = suspend_asg_scaling()
    if "error" in suspend_result:
        print(f"Warning: Could not suspend scaling: {suspend_result['error']}")
        # Continue anyway - scaling might still work

    total_wait_time = 0

    # Step 1: Scale ASG if needed
    if healthy_count < 2:
        scale_result = scale_asg(2)
        if "error" in scale_result:
            return scale_result

        if scale_result.get("status") != "no_change":
            print("Waiting for ASG instances to become healthy...")
            wait_result = wait_for_healthy_instances(target_count=2)
            if "error" in wait_result:
                return wait_result
            total_wait_time += wait_result.get("elapsed_seconds", 0)

    # Step 2: Scale ECS service if needed
    if running_tasks < 2:
        ecs_scale_result = scale_ecs_service(2)
        if "error" in ecs_scale_result:
            return ecs_scale_result

        if ecs_scale_result.get("status") != "no_change":
            print("Waiting for ECS tasks to start running...")
            task_wait_result = wait_for_running_tasks(target_count=2)
            if "error" in task_wait_result:
                return task_wait_result
            total_wait_time += task_wait_result.get("elapsed_seconds", 0)

    # Get updated list of healthy instances
    asg_status = get_asg_status()
    if "error" in asg_status:
        return asg_status

    healthy_ids = asg_status["healthy_instance_ids"]
    available_targets = [i for i in healthy_ids if i != current_instance]

    if not available_targets:
        return {"error": "No available target instances after scaling"}

    # Final verification: ensure tasks are running
    ecs_status = get_ecs_service_status()
    if "error" in ecs_status:
        return ecs_status

    if ecs_status["running_count"] < 2:
        return {
            "error": f"Only {ecs_status['running_count']} tasks running after scaling"
        }

    return {
        "status": "ready",
        "available_targets": available_targets,
        "scaled_up": True,
        "scaling_suspended": True,
        "wait_time": total_wait_time,
    }


def migrate_user_auto(
    user_id: int, target_instance: str = None, proactive: bool = False
) -> dict:
    """Migrate user to target instance with auto-scaling support.

    If target_instance is None, automatically:
    1. Suspend ASG auto-scaling to prevent interference
    2. Scale ASG to 2 if only 1 instance exists
    3. Select a different instance as target
    4. Migrate user
    5. Optionally trigger proactive sync (if proactive=True)
    6. Resume ASG auto-scaling

    Args:
        user_id: User ID to migrate
        target_instance: Target instance ID (auto-selected if None)
        proactive: If True, trigger proactive sync after migration
    """
    scaling_suspended = False
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT instance_id, active_workflow_count
                FROM free_user_assignments
                WHERE user_id = %s
                """,
                (user_id,),
            )
            current = cursor.fetchone()

            if not current:
                return {"error": f"No assignment found for user {user_id}"}

            if current["active_workflow_count"] > 0:
                return {
                    "error": f"User has {current['active_workflow_count']} "
                    "active workflows - cannot migrate"
                }

            old_instance = current["instance_id"]

            # Auto-select target if not provided
            if not target_instance:
                print(f"Auto-selecting target instance (current: {old_instance})...")

                ensure_result = ensure_multiple_instances(old_instance)
                if "error" in ensure_result:
                    return ensure_result

                available = ensure_result["available_targets"]
                target_instance = available[0]  # Pick first available
                scaling_suspended = ensure_result.get("scaling_suspended", False)

                if ensure_result.get("scaled_up"):
                    print(f"Scaled up ASG in {ensure_result.get('wait_time', 0)}s")

                print(f"Auto-selected target instance: {target_instance}")

            # Perform migration
            cursor.execute(
                """
                UPDATE free_user_assignments
                SET instance_id = %s,
                    migration_count = migration_count + 1,
                    last_migration = NOW()
                WHERE user_id = %s
                """,
                (target_instance, user_id),
            )
            conn.commit()

            result = {
                "status": "migrated",
                "user_id": user_id,
                "from_instance": old_instance,
                "to_instance": target_instance,
                "sync_mode": "proactive" if proactive else "lazy",
            }

            # Trigger proactive sync if requested
            if proactive:
                sync_result = trigger_proactive_sync(user_id)
                result["sync_result"] = sync_result

            return result
    finally:
        conn.close()
        # Always resume scaling if it was suspended
        if scaling_suspended:
            resume_result = resume_asg_scaling()
            if "error" in resume_result:
                print(f"Warning: Could not resume scaling: {resume_result['error']}")


def main():
    parser = argparse.ArgumentParser(
        description="Test experiment data sync (run from inside ECS container)"
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Find user command
    find_parser = subparsers.add_parser("find-user", help="Find user by email")
    find_parser.add_argument("email", help="Email address (partial match)")

    # Status command
    status_parser = subparsers.add_parser("status", help="Get user assignment status")
    status_parser.add_argument("user_id", type=int, help="User ID to check")

    # List instances command
    subparsers.add_parser("list-instances", help="List all instances with user counts")

    # ASG status command
    subparsers.add_parser("asg-status", help="Show ASG capacity and instance status")

    # Migrate command
    migrate_parser = subparsers.add_parser(
        "migrate", help="Migrate user to instance (auto-scales ASG if needed)"
    )
    migrate_parser.add_argument("user_id", type=int, help="User ID to migrate")
    migrate_parser.add_argument(
        "target_instance",
        nargs="?",
        default=None,
        help="Target instance ID (optional - auto-selects if not provided)",
    )
    migrate_parser.add_argument(
        "--proactive",
        action="store_true",
        help="Trigger proactive sync after migration (requires INTERNAL_API_SECRET)",
    )

    # Trigger sync command
    sync_parser = subparsers.add_parser(
        "trigger-sync", help="Trigger proactive sync via internal API"
    )
    sync_parser.add_argument("user_id", type=int, help="User ID to sync")

    # Clear local experiment files command
    clear_parser = subparsers.add_parser(
        "clear-local", help="Clear local experiment files (simulates fresh instance)"
    )
    clear_parser.add_argument("user_id", type=int, help="User ID")
    clear_parser.add_argument(
        "unique_id",
        nargs="?",
        default=None,
        help="Experiment unique_id (optional - clears all if not provided)",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Load config from container environment
    load_config()

    # Check required environment variables
    required_vars = ["DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME"]

    missing = [v for v in required_vars if not os.environ.get(v)]
    if missing:
        print(f"Error: Missing environment variables: {', '.join(missing)}")
        print("This script must be run from inside the ECS container.")
        sys.exit(1)

    # Execute command
    if args.command == "find-user":
        users = find_user_by_email(args.email)
        print(f"\n=== Users matching '{args.email}' ===")
        if not users:
            print("No users found")
        else:
            for u in users:
                instance = u["instance_id"] or "(no assignment)"
                print(f"ID: {u['id']:4d} | {u['email']} | {u['name']}")
                print(f"Instance: {instance}")
                if u["last_activity"]:
                    print(f"Last Activity: {u['last_activity']}")
                print()

    elif args.command == "status":
        result = get_user_status(args.user_id)
        print(f"\n=== User Status for ID {args.user_id} ===")
        if "error" in result:
            print(f"Error: {result['error']}")
        else:
            user = result["user"]
            print(f"Name: {user['name']}")
            print(f"Email: {user['email']}")

            assignment = result["assignment"]
            if assignment:
                print(f"\nInstance: {assignment['instance_id']}")
                print(f"Last Activity: {assignment['last_activity']}")
                print(f"Migration Count: {assignment['migration_count']}")
                print(f"Active Workflows: {assignment['active_workflow_count']}")
            else:
                print("\nNo free tier assignment found")

    elif args.command == "list-instances":
        instances = list_instances()
        print("\n=== Instances with User Counts (from DB) ===")
        if not instances:
            print("No instances found")
        else:
            for inst in instances:
                print(
                    f"{inst['instance_id']}: "
                    f"{inst['user_count']} users, "
                    f"latest activity: {inst['latest_activity']}"
                )

    elif args.command == "asg-status":
        print("\n=== ASG Status ===")
        asg_status = get_asg_status()
        if "error" in asg_status:
            print(f"Error: {asg_status['error']}")
        else:
            print(f"ASG Name: {asg_status['asg_name']}")
            print(f"Desired Capacity: {asg_status['desired_capacity']}")
            print(f"Min/Max Size: {asg_status['min_size']}/{asg_status['max_size']}")
            print(f"Healthy Instances: {asg_status['healthy_count']}")
            print("\nInstances:")
            for inst in asg_status["instances"]:
                health = "✓" if inst["health_status"] == "Healthy" else "✗"
                print(
                    f"{inst['instance_id']}: "
                    f"{inst['lifecycle_state']} ({health} {inst['health_status']})"
                )

        print("\n=== ECS Service Status ===")
        ecs_status = get_ecs_service_status()
        if "error" in ecs_status:
            print(f"Error: {ecs_status['error']}")
        else:
            print(f"Cluster: {ecs_status['cluster']}")
            print(f"Service: {ecs_status['service']}")
            print(f"Desired Tasks: {ecs_status['desired_count']}")
            print(f"Running Tasks: {ecs_status['running_count']}")
            print(f"Pending Tasks: {ecs_status['pending_count']}")
            print(f"Service Status: {ecs_status['status']}")

    elif args.command == "migrate":
        target_display = args.target_instance or "(auto-select)"
        sync_mode = "proactive" if args.proactive else "lazy"
        print(f"\n=== Migrating user {args.user_id} to {target_display} ===")
        print(f"Sync mode: {sync_mode}")
        result = migrate_user_auto(args.user_id, args.target_instance, args.proactive)
        if "error" in result:
            print(f"Error: {result['error']}")
        else:
            print(f"Status: {result['status']}")
            print(f"From: {result['from_instance']}")
            print(f"To: {result['to_instance']}")
            print(f"Sync Mode: {result.get('sync_mode', 'lazy')}")
            if "sync_result" in result:
                sync_res = result["sync_result"]
                if "error" in sync_res:
                    print(f"Sync Error: {sync_res['error']}")
                else:
                    print(f"Sync Status: {sync_res.get('status', 'unknown')}")

    elif args.command == "trigger-sync":
        print(f"\n=== Triggering proactive sync for user {args.user_id} ===")
        result = trigger_proactive_sync(args.user_id)
        if "error" in result:
            print(f"Error: {result['error']}")
            if "status_code" in result:
                print(f"Status Code: {result['status_code']}")
        else:
            print(f"Status: {result['status']}")
            if "warning" in result:
                print(f"Warning: {result['warning']}")
            print("Check container logs for sync progress.")

    elif args.command == "clear-local":
        target = args.unique_id or "all experiments"
        print(f"\n=== Clearing local files for user {args.user_id} ({target}) ===")
        result = clear_local_experiments_for_user(args.user_id, args.unique_id)
        if "error" in result:
            print(f"Error: {result['error']}")
        else:
            print(f"Status: {result['status']}")
            print(f"Experiments cleared: {result['experiments_cleared']}")
            print(f"Files deleted: {result['files_deleted']}")
            if result.get("details"):
                print("\nDetails:")
                for detail in result["details"]:
                    print(f"  {detail['workspace_id']}/{detail['unique_id']}:")
                    print(f"    Deleted: {', '.join(detail['deleted_files'])}")


if __name__ == "__main__":
    main()
