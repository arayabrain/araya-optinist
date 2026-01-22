#!/usr/bin/env python3
"""
Test experiment data sync between instances.

Run from inside ECS container (use container_access.sh to get shell).

Quick Start - Automated Tests:
    # Test lazy sync (clears files, calls API, verifies sync)
    python test_data_sync.py test-lazy <email>

    # Test proactive sync (clears files, triggers sync API, verifies)
    python test_data_sync.py test-proactive <email>

Other Commands:
    status [user_id]    Show system status; with user_id shows experiments
    migrate <user_id>   Migrate user to different instance

Manual Testing (if automated tests fail):
    1. python test_data_sync.py status <user_id>  # Get workspace/experiment IDs
    2. python test_data_sync.py clear-local <user_id> <unique_id>
    3. Reproduce experiment in UI, check logs for sync messages

"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests
import yaml


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


def get_user_workspaces(user_id: int) -> list:
    """Get all workspaces for a user (returns id and name)."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, name FROM workspaces WHERE user_id = %s
                """,
                (user_id,),
            )
            return cursor.fetchall()
    finally:
        conn.close()


def get_experiments_from_s3(user_id: int, workspace_id: int) -> list:
    """
    List experiments from S3 for a user's workspace.

    Used when local files don't exist (e.g., after migration to new instance).

    Args:
        user_id: User ID
        workspace_id: Workspace ID

    Returns:
        List of experiment dicts with unique_id and name
    """
    try:
        import boto3

        # Get user's S3 bucket name from database (stored in attributes JSON)
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT attributes FROM users WHERE id = %s",
                    (user_id,),
                )
                result = cursor.fetchone()
                if not result or not result.get("attributes"):
                    return []
                attributes = result["attributes"]
                if isinstance(attributes, str):
                    attributes = json.loads(attributes)
                bucket_name = attributes.get("remote_bucket_name")
                if not bucket_name:
                    return []
        finally:
            conn.close()

        # List experiment directories in S3
        s3 = boto3.client("s3")
        prefix = f"app/studio_data/output/{workspace_id}/"

        response = s3.list_objects_v2(
            Bucket=bucket_name,
            Prefix=prefix,
            Delimiter="/",
        )

        experiments = []
        for common_prefix in response.get("CommonPrefixes", []):
            # Extract unique_id from prefix like "app/studio_data/output/74/e8c519c2/"
            exp_path = common_prefix["Prefix"].rstrip("/")
            unique_id = exp_path.split("/")[-1]

            # Skip tutorial directories
            if unique_id.startswith("tutorial"):
                continue

            # Try to get experiment name from experiment.yaml in S3
            exp_name = "(in S3)"
            try:
                yaml_key = f"{prefix}{unique_id}/experiment.yaml"
                yaml_obj = s3.get_object(Bucket=bucket_name, Key=yaml_key)
                yaml_content = yaml_obj["Body"].read().decode("utf-8")
                data = yaml.safe_load(yaml_content)
                exp_name = data.get("name", "(unknown)")
            except Exception:
                pass

            experiments.append(
                {
                    "unique_id": unique_id,
                    "name": exp_name,
                    "source": "s3",
                }
            )

        return experiments
    except Exception as e:
        print(f"Warning: Could not list experiments from S3: {e}")
        return []


def list_experiments_for_user(user_id: int) -> dict:
    """
    List all experiments for a user across all workspaces.

    First checks local files, then falls back to S3 if no local experiments found.

    Args:
        user_id: User ID to list experiments for

    Returns:
        Dict with workspaces and their experiments
    """
    workspaces = get_user_workspaces(user_id)

    if not workspaces:
        return {"error": f"No workspaces found for user {user_id}"}

    output_dir = os.environ.get("OUTPUT_DIR", "/app/studio_data/output")
    results = []
    total_local_experiments = 0

    for ws in workspaces:
        workspace_id = ws["id"]
        workspace_name = ws["name"]
        workspace_path = os.path.join(output_dir, str(workspace_id))

        experiments = []
        if os.path.isdir(workspace_path):
            for item in os.listdir(workspace_path):
                item_path = os.path.join(workspace_path, item)
                if os.path.isdir(item_path):
                    # Skip tutorial directories
                    if item.startswith("tutorial"):
                        continue

                    # Try to read experiment name from experiment.yaml
                    exp_name = None
                    exp_yaml = os.path.join(item_path, "experiment.yaml")
                    if os.path.isfile(exp_yaml):
                        try:
                            with open(exp_yaml) as f:
                                data = yaml.safe_load(f)
                                exp_name = data.get("name", "unknown")
                        except Exception:
                            exp_name = "(yaml parse error)"
                    else:
                        exp_name = "(no experiment.yaml)"

                    experiments.append(
                        {
                            "unique_id": item,
                            "name": exp_name,
                            "source": "local",
                        }
                    )
                    total_local_experiments += 1

        results.append(
            {
                "workspace_id": workspace_id,
                "workspace_name": workspace_name,
                "experiments": experiments,
            }
        )

    # If no local experiments found, try S3 (for lazy sync testing after migration)
    if total_local_experiments == 0:
        print("No local experiments found, checking S3...")
        for ws_result in results:
            s3_experiments = get_experiments_from_s3(user_id, ws_result["workspace_id"])
            if s3_experiments:
                ws_result["experiments"] = s3_experiments

    return {
        "status": "success",
        "user_id": user_id,
        "workspaces": results,
    }


def get_workspace_experiments(workspace_id: str) -> list:
    """Get all experiment unique_ids in a workspace directory."""
    output_dir = os.environ.get("OUTPUT_DIR", "/app/studio_data/output")
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
    output_dir = os.environ.get("OUTPUT_DIR", "/app/studio_data/output")
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


def clear_all_workspace_experiment_metadata(workspace_id: str) -> dict:
    """Clear ALL experiment metadata files in a workspace.

    Used for testing fetch_last_experiment, which needs to sync all experiments
    from S3 to determine which is the "last" one.

    Args:
        workspace_id: Workspace ID to clear experiments from

    Returns:
        Dict with status and counts of deleted files
    """
    output_dir = os.environ.get("OUTPUT_DIR", "/app/studio_data/output")
    workspace_path = os.path.join(output_dir, workspace_id)

    if not os.path.isdir(workspace_path):
        return {
            "status": "no_workspace",
            "workspace_id": workspace_id,
            "message": f"Workspace directory not found: {workspace_path}",
        }

    metadata_files = ["experiment.yaml", "workflow.yaml", "snakemake_config.yaml"]
    experiments_cleared = 0
    files_deleted = 0

    # Iterate through all experiment directories in the workspace
    for item in os.listdir(workspace_path):
        experiment_path = os.path.join(workspace_path, item)
        if not os.path.isdir(experiment_path):
            continue

        experiment_files_deleted = 0
        for filename in metadata_files:
            filepath = os.path.join(experiment_path, filename)
            if os.path.isfile(filepath):
                os.remove(filepath)
                experiment_files_deleted += 1
                files_deleted += 1

        if experiment_files_deleted > 0:
            experiments_cleared += 1

    return {
        "status": "cleared",
        "workspace_id": workspace_id,
        "experiments_cleared": experiments_cleared,
        "files_deleted": files_deleted,
    }


def clear_local_visualization_files(workspace_id: str, unique_id: str) -> dict:
    """Clear local visualization files (JSON, TIFF) for tiered sync testing.

    Deletes JSON and TIFF files that are downloaded during visualization sync,
    simulating a fresh instance that needs to sync visualization data from S3.

    Args:
        workspace_id: Workspace ID containing the experiment
        unique_id: Unique ID of the experiment

    Returns:
        Dict with status and counts of deleted files
    """

    output_dir = os.environ.get("OUTPUT_DIR", "/app/studio_data/output")
    experiment_path = os.path.join(output_dir, workspace_id, unique_id)

    if not os.path.isdir(experiment_path):
        return {
            "error": f"Experiment directory not found: {experiment_path}",
            "workspace_id": workspace_id,
            "unique_id": unique_id,
        }

    json_deleted = 0
    tiff_deleted = 0

    for root, dirs, files in os.walk(experiment_path):
        for filename in files:
            filepath = os.path.join(root, filename)
            lower_name = filename.lower()

            if lower_name.endswith(".json") and not lower_name.endswith(".yaml"):
                os.remove(filepath)
                json_deleted += 1
            elif lower_name.endswith((".tif", ".tiff")):
                os.remove(filepath)
                tiff_deleted += 1

    return {
        "status": "cleared",
        "workspace_id": workspace_id,
        "unique_id": unique_id,
        "json_files_deleted": json_deleted,
        "tiff_files_deleted": tiff_deleted,
        "experiment_path": experiment_path,
    }


def clear_local_edit_roi_files(workspace_id: str, unique_id: str) -> dict:
    """Clear local PKL and NWB files for Edit ROI sync testing.

    Deletes PKL and NWB files that are needed for Edit ROI functionality,
    simulating a fresh instance that needs full sync from S3.

    Args:
        workspace_id: Workspace ID containing the experiment
        unique_id: Unique ID of the experiment

    Returns:
        Dict with status and counts of deleted files
    """
    output_dir = os.environ.get("OUTPUT_DIR", "/app/studio_data/output")
    experiment_path = os.path.join(output_dir, workspace_id, unique_id)

    if not os.path.isdir(experiment_path):
        return {
            "error": f"Experiment directory not found: {experiment_path}",
            "workspace_id": workspace_id,
            "unique_id": unique_id,
        }

    pkl_deleted = 0
    nwb_deleted = 0

    for root, dirs, files in os.walk(experiment_path):
        for filename in files:
            filepath = os.path.join(root, filename)
            lower_name = filename.lower()

            if lower_name.endswith(".pkl"):
                os.remove(filepath)
                pkl_deleted += 1
            elif lower_name.endswith(".nwb"):
                os.remove(filepath)
                nwb_deleted += 1

    return {
        "status": "cleared",
        "workspace_id": workspace_id,
        "unique_id": unique_id,
        "pkl_files_deleted": pkl_deleted,
        "nwb_files_deleted": nwb_deleted,
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

    for ws in workspaces:
        workspace_id = ws["id"]
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

    Requires INTERNAL_API_SECRET environment variable.
    Uses get_api_base_url() for the API endpoint.
    """
    internal_secret = os.environ.get("INTERNAL_API_SECRET")

    if not internal_secret:
        return {"error": "INTERNAL_API_SECRET environment variable is required"}

    base_url = get_api_base_url()
    url = f"{base_url}/system-internal/sync-experiments/{user_id}"
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

# Environment variables for ASG/ECS operations (auto-discovered if not set):
#   AWS_REGION    - AWS region (default: ap-northeast-1)
#   ASG_NAME      - Auto Scaling Group name (auto-discovered)
#   ECS_CLUSTER   - ECS cluster name (auto-discovered)
#   ECS_SERVICE   - ECS service name (auto-discovered)

# Cache for discovered values
_discovered_values = {}


def get_aws_region() -> str:
    """Get AWS region from environment or default."""
    region = os.environ.get("AWS_REGION")
    if region:
        return region

    # Try to get from ECS metadata
    ecs_uri = os.environ.get("ECS_CONTAINER_METADATA_URI_V4")
    if ecs_uri:
        try:
            import requests

            resp = requests.get(f"{ecs_uri}/task", timeout=2)
            if resp.status_code == 200:
                # Extract region from task ARN
                task_arn = resp.json().get("TaskARN", "")
                # ARN format: arn:aws:ecs:REGION:ACCOUNT:task/...
                parts = task_arn.split(":")
                if len(parts) >= 4:
                    return parts[3]
        except Exception:
            pass

    # Default to Tokyo region
    return "ap-northeast-1"


def get_asg_client():
    """Get boto3 autoscaling client."""
    import boto3

    return boto3.client("autoscaling", region_name=get_aws_region())


def get_ecs_client():
    """Get boto3 ECS client."""
    import boto3

    return boto3.client("ecs", region_name=get_aws_region())


def discover_asg_name() -> str:
    """Auto-discover ASG name by listing ASGs with 'optinist' in the name."""
    if "asg_name" in _discovered_values:
        return _discovered_values["asg_name"]

    try:
        asg_client = get_asg_client()
        response = asg_client.describe_auto_scaling_groups()

        for asg in response.get("AutoScalingGroups", []):
            name = asg["AutoScalingGroupName"]
            if "optinist" in name.lower():
                _discovered_values["asg_name"] = name
                print(f"Auto-discovered ASG: {name}")
                return name
    except Exception as e:
        print(f"Warning: Could not auto-discover ASG: {e}")

    return None


def discover_ecs_cluster_and_service() -> tuple:
    """Auto-discover ECS cluster and service names."""
    if "ecs_cluster" in _discovered_values:
        return _discovered_values["ecs_cluster"], _discovered_values["ecs_service"]

    cluster = None
    service = None

    # Try to get from ECS metadata (works when running inside container)
    ecs_uri = os.environ.get("ECS_CONTAINER_METADATA_URI_V4")
    if ecs_uri:
        try:
            import requests

            resp = requests.get(f"{ecs_uri}/task", timeout=2)
            if resp.status_code == 200:
                data = resp.json()
                cluster_arn = data.get("Cluster", "")
                # Cluster can be ARN or just name
                if "/" in cluster_arn:
                    cluster = cluster_arn.split("/")[-1]
                elif ":" in cluster_arn:
                    cluster = cluster_arn.split(":")[-1]
                else:
                    cluster = cluster_arn

                # Get service from task group if available
                task_group = data.get("Group", "")
                if task_group.startswith("service:"):
                    service = task_group.split(":", 1)[1]

                # If no service from group, infer from cluster name pattern
                # e.g., subscr-optinist-cloud-cluster -> subscr-optinist-cloud-service
                if not service and cluster:
                    if cluster.endswith("-cluster"):
                        service = cluster.replace("-cluster", "-service")
                    else:
                        service = f"{cluster}-service"

                if cluster and service:
                    _discovered_values["ecs_cluster"] = cluster
                    _discovered_values["ecs_service"] = service
                    print(f"Auto-discovered ECS: cluster={cluster}, service={service}")
                    return cluster, service

        except Exception as e:
            print(f"Warning: Could not get ECS metadata: {e}")

    # Fallback: try listing via API (may fail due to IAM)
    if not cluster:
        try:
            ecs_client = get_ecs_client()
            clusters_resp = ecs_client.list_clusters()
            for cluster_arn in clusters_resp.get("clusterArns", []):
                cluster_name = cluster_arn.split("/")[-1]
                if "optinist" in cluster_name.lower():
                    cluster = cluster_name
                    break
        except Exception as e:
            print(f"Warning: Could not list ECS clusters: {e}")

    if cluster and not service:
        try:
            ecs_client = get_ecs_client()
            services_resp = ecs_client.list_services(cluster=cluster)
            for service_arn in services_resp.get("serviceArns", []):
                service_name = service_arn.split("/")[-1]
                if "optinist" in service_name.lower():
                    service = service_name
                    break
        except Exception:
            # Infer service name from cluster name
            if cluster.endswith("-cluster"):
                service = cluster.replace("-cluster", "-service")

    if cluster and service:
        _discovered_values["ecs_cluster"] = cluster
        _discovered_values["ecs_service"] = service
        print(f"Auto-discovered ECS: cluster={cluster}, service={service}")
        return cluster, service

    return None, None


def get_ecs_cluster() -> str:
    """Get ECS cluster name from environment or auto-discover."""
    cluster = os.environ.get("ECS_CLUSTER")
    if cluster:
        return cluster

    cluster, _ = discover_ecs_cluster_and_service()
    if cluster:
        return cluster

    raise ValueError(
        "ECS_CLUSTER not found - set environment variable or ensure discovery works"
    )


def get_ecs_service() -> str:
    """Get ECS service name from environment or auto-discover."""
    service = os.environ.get("ECS_SERVICE")
    if service:
        return service

    _, service = discover_ecs_cluster_and_service()
    if service:
        return service

    raise ValueError(
        "ECS_SERVICE not found - set environment variable or ensure discovery works"
    )


def get_asg_name() -> str:
    """Get ASG name from environment or auto-discover."""
    asg_name = os.environ.get("ASG_NAME")
    if asg_name:
        return asg_name

    asg_name = discover_asg_name()
    if asg_name:
        return asg_name

    raise ValueError(
        "ASG_NAME not found - set environment variable or ensure discovery works"
    )


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
    print("Scaling up to 2 instances...")

    # Suspend auto-scaling alarms to prevent interference during scale-up
    suspend_result = suspend_asg_scaling()
    if "error" in suspend_result:
        print(f"Warning: Could not suspend scaling: {suspend_result['error']}")
        # Continue anyway - scaling might still work

    total_wait_time = 0

    # Step 1: Scale ASG - the free_manager Lambda will sync ECS automatically
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

    # Step 2: Wait for ECS tasks (Lambda syncs ECS to ASG via lifecycle hook)
    # The free_manager Lambda is triggered by ASG lifecycle events and syncs ECS
    ecs_status = get_ecs_service_status()
    if ecs_status.get("running_count", 0) < 2:
        print("Waiting for ECS tasks to start (Lambda syncs ECS to ASG)...")
        task_wait_result = wait_for_running_tasks(target_count=2)
        if "error" in task_wait_result:
            # If Lambda didn't sync, try manual ECS scale as fallback
            print("Attempting manual ECS scale as fallback...")
            ecs_scale_result = scale_ecs_service(2)
            if "error" not in ecs_scale_result:
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


# =============================================================================
# API Endpoint Test Functions
# =============================================================================

# These functions test lazy sync by calling API endpoints that trigger
# ensure_synced_async() when experiment metadata is missing locally.
#
# Prerequisites:
#   - Must have a valid JWT token (from tokens.json, browser, or auto-generate)
#   - ALB_DNS_NAME environment variable must be set
#   - User must have at least one experiment in the workspace

# Import token generation utilities (optional - for auto-generate feature)
try:
    from get_jwt_tokens import generate_jwt_tokens, initialize_firebase_admin
except ImportError:
    generate_jwt_tokens = None
    initialize_firebase_admin = None


def get_auth_headers(token: str) -> dict:
    """Get headers for authenticated API requests."""
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def get_api_base_url() -> str:
    """Get base URL for API requests.

    Checks (in order):
    1. ALB_DNS_NAME env var (explicit ALB DNS)
    2. FRONTEND_SERVER_HOST env var (frontend hostname)
    3. Falls back to localhost:8000 (for in-container testing)
    """
    alb_dns = os.environ.get("ALB_DNS_NAME")
    if alb_dns:
        return f"https://{alb_dns}"

    frontend_host = os.environ.get("FRONTEND_SERVER_HOST")
    if frontend_host:
        return f"https://{frontend_host}"

    # Inside container, we can use localhost
    backend_host = os.environ.get("BACKEND_HOST", "localhost")
    backend_port = os.environ.get("BACKEND_PORT", "8000")
    return f"http://{backend_host}:{backend_port}"


def make_api_request(
    method: str,
    url: str,
    headers: dict,
    json_body: dict = None,
    timeout: float = 30.0,
) -> tuple:
    """Make an API request with SSL fallback.

    Tries with SSL verification first, falls back to no verification on SSLError.

    Args:
        method: HTTP method (get, post, patch, delete)
        url: Request URL
        headers: Request headers
        json_body: Optional JSON body for POST/PATCH requests
        timeout: Request timeout in seconds

    Returns:
        Tuple of (response, ssl_warning) where ssl_warning is True if SSL was disabled

    Raises:
        Exception: If request fails completely
    """
    request_func = getattr(requests, method.lower())
    kwargs = {"headers": headers, "timeout": timeout}
    if json_body is not None:
        kwargs["json"] = json_body

    try:
        response = request_func(url, verify=True, **kwargs)
        return response, False
    except requests.exceptions.SSLError:
        response = request_func(url, verify=False, **kwargs)
        return response, True


def load_token_from_file(user_email: str = None) -> str:
    """
    Load JWT token from tokens.json file.

    Args:
        user_email: Email of user to get token for. If None, returns first available.

    Returns:
        JWT token string, or None if not found
    """
    tokens_file = Path(__file__).parent / "tokens.json"

    if not tokens_file.exists():
        print(f"Token file not found: {tokens_file}")
        return None

    try:
        with open(tokens_file) as f:
            tokens = json.load(f)

        if user_email:
            if user_email in tokens:
                return tokens[user_email]
            else:
                print(f"Token not found for user: {user_email}")
                print(f"Available users: {list(tokens.keys())}")
                return None
        else:
            # Return first available token
            if tokens:
                first_key = list(tokens.keys())[0]
                print(f"Using token for: {first_key}")
                return tokens[first_key]
            return None
    except Exception as e:
        print(f"Error loading tokens: {e}")
        return None


def find_firebase_credentials() -> str:
    """
    Find Firebase service account credentials in various locations.

    Checks (in order):
    1. FIREBASE_SERVICE_ACCOUNT_KEY env var (JSON string)
    2. FIREBASE_PRIVATE_JSON env var (JSON string)
    3. Container config paths
    4. Script directory paths

    Returns:
        Path to credentials file, or None if using env var or not found
    """
    # Check environment variables first
    if os.environ.get("FIREBASE_SERVICE_ACCOUNT_KEY"):
        print("Found Firebase credentials in FIREBASE_SERVICE_ACCOUNT_KEY env var")
        return None  # Will use env var directly

    if os.environ.get("FIREBASE_PRIVATE_JSON"):
        # Copy to the expected env var name
        os.environ["FIREBASE_SERVICE_ACCOUNT_KEY"] = os.environ["FIREBASE_PRIVATE_JSON"]
        print("Found Firebase credentials in FIREBASE_PRIVATE_JSON env var")
        return None

    # Check container-specific paths
    container_paths = [
        "/app/studio/config/auth/firebase_private.json",
        "/app/studio_data/config/auth/firebase_private.json",
        "/app/config/auth/firebase_private.json",
        "/app/config/firebase-service-account.json",
    ]

    # Check script directory paths
    script_dir = Path(__file__).parent
    local_paths = [
        script_dir / "firebase-service-account.json",
        script_dir / "terraform" / "firebase-service-account.json",
        script_dir.parent / "studio" / "config" / "auth" / "firebase_private.json",
    ]

    all_paths = container_paths + [str(p) for p in local_paths]

    for path in all_paths:
        if os.path.exists(path):
            print(f"Found Firebase credentials at: {path}")
            return path

    return None


def get_test_users_from_env() -> list:
    """
    Get test users from TEST_USERS_CONFIG environment variable.

    Returns:
        List of test user dicts with email, firebase_uid, name, etc.
    """
    test_users_json = os.environ.get("TEST_USERS_CONFIG")
    if not test_users_json:
        return []

    try:
        users = json.loads(test_users_json)
        if isinstance(users, list):
            return users
    except json.JSONDecodeError:
        pass

    return []


def generate_token_for_user(email: str) -> str:
    """
    Generate a Firebase ID token for a specific user.

    Uses Firebase Admin SDK to create a custom token, then exchanges it
    for an ID token via the Firebase REST API.

    Args:
        email: User email to generate token for

    Returns:
        JWT token string, or None if generation failed
    """
    # Find Firebase credentials
    cred_path = find_firebase_credentials()

    # Try to initialize Firebase Admin SDK
    if initialize_firebase_admin is None:
        print("Firebase Admin SDK not available (firebase-admin not installed)")
        return None

    if not initialize_firebase_admin(cred_path):
        print("Failed to initialize Firebase Admin SDK")
        return None

    # Get test users from environment or config
    test_users = get_test_users_from_env()
    if not test_users:
        print("TEST_USERS_CONFIG env var not found, trying Terraform...")
        # Fall back to generate_jwt_tokens which handles Terraform
        return None

    # Find the user
    user_data = None
    for user in test_users:
        if user.get("email") == email:
            user_data = user
            break

    if not user_data:
        print(f"User {email} not found in TEST_USERS_CONFIG")
        return None

    firebase_uid = user_data.get("firebase_uid")
    if not firebase_uid:
        print(f"No firebase_uid for user {email}")
        return None

    # Import Firebase auth
    try:
        from firebase_admin import auth as firebase_auth
    except ImportError:
        print("firebase_admin not installed")
        return None

    # Create custom token and exchange for ID token
    try:
        from studio.app.common.core.auth import pyrebase_app

        custom_token = firebase_auth.create_custom_token(firebase_uid)
        print(f"Created custom token for {email}")

        user = pyrebase_app.auth().sign_in_with_custom_token(custom_token.decode())
        id_token = user["idToken"]
        print("Exchanged for ID token (expires in 1 hour)")

        # Save token to file for reuse
        tokens_file = Path(__file__).parent / "tokens.json"
        try:
            existing_tokens = {}
            if tokens_file.exists():
                with open(tokens_file) as f:
                    existing_tokens = json.load(f)
            existing_tokens[email] = id_token
            with open(tokens_file, "w") as f:
                json.dump(existing_tokens, f, indent=2)
            print(f"Token saved to {tokens_file}")
        except Exception as e:
            print(f"Warning: Could not save token: {e}")

        return id_token

    except Exception as e:
        print(f"Error generating token: {e}")
        return None


def get_or_generate_token(user_email: str = None, auto_generate: bool = False) -> str:
    """
    Get JWT token from file or generate new one.

    Args:
        user_email: Email of user to get token for
        auto_generate: If True, generate new tokens if not found

    Returns:
        JWT token string, or None if not available
    """
    # Try loading from file first
    token = load_token_from_file(user_email)
    if token:
        return token

    # Auto-generate if requested
    if auto_generate and user_email:
        # Try direct token generation using TEST_USERS_CONFIG
        # (faster, works in container)
        token = generate_token_for_user(user_email)
        if token:
            return token

        # Fall back to generate_jwt_tokens (uses Terraform)
        if generate_jwt_tokens is not None:
            # Find Firebase credentials
            cred_path = find_firebase_credentials()

            # Determine terraform directory
            terraform_dir = None
            possible_tf_dirs = [
                Path(__file__).parent / "terraform",
                Path("/app/scripts/terraform"),
                Path(__file__).parent.parent / "terraform",
            ]
            for tf_dir in possible_tf_dirs:
                if tf_dir.exists():
                    terraform_dir = str(tf_dir)
                    break

            if not terraform_dir:
                terraform_dir = str(Path(__file__).parent / "terraform")

            print("Trying generate_jwt_tokens fallback...")
            tokens = generate_jwt_tokens(
                environment="cloud",
                terraform_dir=terraform_dir,
                service_account_path=cred_path,
                user_type="free",
                multi_free=True,
            )

            if tokens:
                return load_token_from_file(user_email)

    return None


def test_fetch_last_experiment(workspace_id: str, token: str) -> dict:
    """
    Test lazy sync on workspace page load.

    Calls GET /workflow/fetch/{workspace_id} which triggers ensure_synced_async
    for the last experiment if its metadata is missing locally.
    """
    url = f"{get_api_base_url()}/workflow/fetch/{workspace_id}"
    headers = get_auth_headers(token)
    endpoint = "GET /workflow/fetch/{workspace_id}"

    try:
        print(f"Testing fetch_last_experiment for workspace {workspace_id}...")
        response, ssl_warning = make_api_request("get", url, headers)

        result = {"endpoint": endpoint, "workspace_id": workspace_id}
        if ssl_warning:
            result["warning"] = "SSL verification disabled"

        if response.status_code == 200:
            data = response.json()
            result.update(
                {
                    "status": "success",
                    "experiment_name": data.get("name", "unknown"),
                    "unique_id": data.get("unique_id", "unknown"),
                    "message": "Lazy sync triggered if metadata was missing",
                }
            )
        elif response.status_code == 404:
            result.update(
                {
                    "status": "no_experiment",
                    "message": "No experiments found in workspace",
                }
            )
        else:
            result.update(
                {
                    "status": "error",
                    "status_code": response.status_code,
                    "response": response.text[:500],
                }
            )
        return result
    except Exception as e:
        return {"status": "error", "error": str(e)}


def test_run_result(workspace_id: str, unique_id: str, token: str) -> dict:
    """
    Test lazy sync when viewing experiment results.

    Calls POST /run/result/{workspace_id}/{uid} which triggers ensure_synced_async
    before fetching results.
    """
    url = f"{get_api_base_url()}/run/result/{workspace_id}/{unique_id}"
    headers = get_auth_headers(token)
    body = {"pendingNodeIdList": []}
    endpoint = "POST /run/result/{workspace_id}/{uid}"

    try:
        print(f"Testing run_result for {workspace_id}/{unique_id}...")
        response, ssl_warning = make_api_request("post", url, headers, body)

        result = {
            "endpoint": endpoint,
            "workspace_id": workspace_id,
            "unique_id": unique_id,
        }
        if ssl_warning:
            result["warning"] = "SSL verification disabled"

        if response.status_code == 200:
            result.update(
                {
                    "status": "success",
                    "message": "Lazy sync triggered if metadata was missing",
                }
            )
        elif response.status_code == 404:
            result.update(
                {
                    "status": "not_found",
                    "message": "Experiment not found (sync may have failed)",
                }
            )
        else:
            result.update(
                {
                    "status": "error",
                    "status_code": response.status_code,
                    "response": response.text[:500],
                }
            )
        return result
    except Exception as e:
        return {"status": "error", "error": str(e)}


def test_experiment_rename(
    workspace_id: str, unique_id: str, new_name: str, token: str
) -> dict:
    """
    Test lazy sync on experiment rename.

    Calls PATCH /experiments/{workspace_id}/{unique_id}/rename which triggers
    ensure_synced_async before renaming.
    """
    url = f"{get_api_base_url()}/experiments/{workspace_id}/{unique_id}/rename"
    headers = get_auth_headers(token)
    body = {"new_name": new_name}
    endpoint = "PATCH /experiments/{workspace_id}/{unique_id}/rename"

    try:
        print(f"Testing rename_experiment for {workspace_id}/{unique_id}...")
        response, ssl_warning = make_api_request("patch", url, headers, body)

        result = {
            "endpoint": endpoint,
            "workspace_id": workspace_id,
            "unique_id": unique_id,
        }
        if ssl_warning:
            result["warning"] = "SSL verification disabled"

        if response.status_code == 200:
            data = response.json()
            result.update(
                {
                    "status": "success",
                    "new_name": data.get("name", new_name),
                    "message": "Lazy sync triggered if metadata was missing",
                }
            )
        elif response.status_code == 404:
            result.update(
                {
                    "status": "not_found",
                    "message": "Experiment not found (sync may have failed)",
                }
            )
        else:
            result.update(
                {
                    "status": "error",
                    "status_code": response.status_code,
                    "response": response.text[:500],
                }
            )
        return result
    except Exception as e:
        return {"status": "error", "error": str(e)}


def test_visualization_sync(workspace_id: str, unique_id: str, token: str) -> dict:
    """
    Test tiered visualization sync endpoint.

    Calls POST /outputs/sync/{workspace_id}/{unique_id} which:
    1. Downloads only JSON and TIFF files (sync_mode="visualization")
    2. Triggers background task to download PKL/NWB files

    This tests the new tiered sync approach for faster visualization loading.
    """
    url = f"{get_api_base_url()}/outputs/sync/{workspace_id}/{unique_id}"
    headers = get_auth_headers(token)
    endpoint = "POST /outputs/sync/{workspace_id}/{unique_id}"

    try:
        print(f"Testing visualization_sync for {workspace_id}/{unique_id}...")
        response, ssl_warning = make_api_request("post", url, headers)

        result = {
            "endpoint": endpoint,
            "workspace_id": workspace_id,
            "unique_id": unique_id,
        }
        if ssl_warning:
            result["warning"] = "SSL verification disabled"

        if response.status_code == 200:
            data = response.json()
            result.update(
                {
                    "status": "success",
                    "sync_result": data,
                    "message": "Visualization sync triggered (JSON/TIFF downloaded, "
                    "PKL/NWB in background)",
                }
            )
        elif response.status_code == 404:
            result.update(
                {
                    "status": "not_found",
                    "message": "Experiment not found",
                }
            )
        else:
            result.update(
                {
                    "status": "error",
                    "status_code": response.status_code,
                    "response": response.text[:500],
                }
            )
        return result
    except Exception as e:
        return {"status": "error", "error": str(e)}


# =============================================================================
# Automated End-to-End Tests
# =============================================================================


def find_user_and_experiment(email: str) -> dict:
    """Find user by email and get their first experiment.

    Common setup for test functions.

    Returns:
        Dict with user_id, workspace_id, unique_id, exp_name on success,
        or dict with "error" and "step" keys on failure.
    """
    users = find_user_by_email(email)
    if not users:
        return {"error": f"User not found: {email}", "step": 1}

    user = users[0]
    user_id = user["id"]
    print(f"Found: {user['name']} (ID: {user_id})")

    exp_result = list_experiments_for_user(user_id)
    if exp_result.get("status") != "success":
        return {"error": "Failed to list experiments", "step": 2}

    workspaces = exp_result.get("workspaces", [])
    for ws in workspaces:
        experiments = ws.get("experiments", [])
        if experiments:
            return {
                "user_id": user_id,
                "workspace_id": ws["workspace_id"],
                "unique_id": experiments[0]["unique_id"],
                "exp_name": experiments[0]["name"],
            }

    return {"error": "No experiments found. Run an experiment first.", "step": 2}


def run_test_lazy_sync(email: str) -> dict:
    """
    Automated end-to-end test for lazy sync across all endpoints.

    Tests: fetch_last, run_result, rename (each clears files first)
    """
    print(f"\n{'='*60}")
    print(f"LAZY SYNC TEST: {email}")
    print(f"{'='*60}")

    # Steps 1-2: Find user and experiment
    print("\n[1/4] Finding user...")
    print("\n[2/4] Finding experiments...")
    setup = find_user_and_experiment(email)
    if "error" in setup:
        return {"status": "error", "step": setup["step"], "error": setup["error"]}

    user_id = setup["user_id"]
    workspace_id = setup["workspace_id"]
    unique_id = setup["unique_id"]
    exp_name = setup["exp_name"]
    print(f"Found: {exp_name} ({workspace_id}/{unique_id})")

    # Step 3: Get token
    print("\n[3/4] Loading token...")
    token = load_token_from_file(email)
    if not token:
        token = get_or_generate_token(email, auto_generate=True)
    if not token:
        return {
            "status": "error",
            "step": 3,
            "error": "No token. Run: python get_jwt_tokens.py --multi-free",
        }
    print("Token loaded")

    # Step 4: Test each endpoint
    print("\n[4/4] Testing endpoints...")
    ws_id = str(workspace_id)
    results = {}

    # Test 1: fetch_last_experiment
    # This test clears ALL experiment metadata in the workspace because
    # fetch_last needs to compare all experiments to find the "last" one
    print("\n      [a] GET /workflow/fetch (fetch_last_experiment)")
    clear_all_workspace_experiment_metadata(ws_id)
    result = test_fetch_last_experiment(ws_id, token)
    results["fetch_last"] = result.get("status") == "success"
    print(f"{'PASS' if results['fetch_last'] else 'FAIL'}")

    # Test 2: run_result
    print("\n      [b] POST /run/result (run_result)")
    clear_local_experiment(ws_id, unique_id)
    result = test_run_result(ws_id, unique_id, token)
    results["run_result"] = result.get("status") == "success"
    print(f"{'PASS' if results['run_result'] else 'FAIL'}")

    # Test 3: rename (non-destructive)
    print("\n      [c] PATCH /experiments/.../rename (rename_experiment)")
    clear_local_experiment(ws_id, unique_id)
    # Rename to same name (effectively a no-op but triggers sync)
    result = test_experiment_rename(ws_id, unique_id, exp_name, token)
    results["rename"] = result.get("status") == "success"
    print(f"{'PASS' if results['rename'] else 'FAIL'}")

    # Test 4: visualization sync (tiered sync - JSON/TIFF first, PKL/NWB background)
    print("\n      [d] POST /outputs/sync (visualization_sync)")
    clear_local_visualization_files(ws_id, unique_id)
    result = test_visualization_sync(ws_id, unique_id, token)
    results["visualization_sync"] = result.get("status") == "success"
    print(f"{'PASS' if results['visualization_sync'] else 'FAIL'}")

    # Summary
    passed = sum(1 for v in results.values() if v)
    total = len(results)

    if passed == total:
        return {
            "status": "success",
            "user_id": user_id,
            "workspace_id": workspace_id,
            "unique_id": unique_id,
            "results": results,
            "message": f"Lazy sync test PASSED ({passed}/{total} endpoints)",
        }
    else:
        return {
            "status": "partial",
            "user_id": user_id,
            "results": results,
            "message": f"Lazy sync test PARTIAL ({passed}/{total} endpoints)",
        }


def run_test_proactive_sync(email: str) -> dict:
    """
    Automated end-to-end test for proactive sync.

    Steps: Find user/experiment, clear files, trigger sync, verify.
    """
    print(f"\n{'='*60}")
    print(f"PROACTIVE SYNC TEST: {email}")
    print(f"{'='*60}")

    # Steps 1-2: Find user and experiment
    print("\n[1/5] Finding user...")
    print("\n[2/5] Finding experiments...")
    setup = find_user_and_experiment(email)
    if "error" in setup:
        return {"status": "error", "step": setup["step"], "error": setup["error"]}

    user_id = setup["user_id"]
    workspace_id = setup["workspace_id"]
    unique_id = setup["unique_id"]
    exp_name = setup["exp_name"]
    print(f"Found: {exp_name} ({workspace_id}/{unique_id})")

    # Step 3: Clear local files
    print("\n[3/5] Clearing local experiment files...")
    clear_result = clear_local_experiment(str(workspace_id), unique_id)
    if "error" in clear_result:
        return {"status": "error", "step": 3, "error": clear_result["error"]}

    deleted = clear_result.get("deleted_files", [])
    if not deleted:
        print("Warning: No files to clear (already missing?)")
    else:
        print(f"Cleared: {', '.join(deleted)}")

    # Step 4: Trigger proactive sync
    print("\n[4/5] Triggering proactive sync...")
    sync_result = trigger_proactive_sync(user_id)
    if "error" in sync_result:
        return {"status": "error", "step": 4, "error": sync_result["error"]}

    print(f"Sync initiated: {sync_result.get('status')}")

    # Step 5: Verify files exist (poll with timeout for background task)
    print("\n[5/5] Verifying sync (waiting for background task)...")

    output_dir = os.environ.get("OUTPUT_DIR", "/app/studio_data/output")
    exp_yaml = os.path.join(output_dir, str(workspace_id), unique_id, "experiment.yaml")
    workflow_yaml = os.path.join(
        output_dir, str(workspace_id), unique_id, "workflow.yaml"
    )

    # Poll for up to 15 seconds (background sync typically takes 3-5 seconds)
    max_wait = 15
    poll_interval = 1
    elapsed = 0

    while elapsed < max_wait:
        if os.path.isfile(exp_yaml) and os.path.isfile(workflow_yaml):
            print(f"Files synced successfully (after {elapsed}s)")
            return {
                "status": "success",
                "user_id": user_id,
                "workspace_id": workspace_id,
                "unique_id": unique_id,
                "message": "Proactive sync test PASSED",
            }
        time.sleep(poll_interval)
        elapsed += poll_interval
        if elapsed % 5 == 0:
            print(f"Still waiting... ({elapsed}s)")

    # Timeout - check what we have
    exp_exists = os.path.isfile(exp_yaml)
    wf_exists = os.path.isfile(workflow_yaml)
    print(
        f"Timeout after {max_wait}s - experiment.yaml: "
        f"{exp_exists}, workflow.yaml: {wf_exists}"
    )
    return {
        "status": "timeout",
        "user_id": user_id,
        "message": f"Sync timeout after {max_wait}s. experiment.yaml: "
        f"{exp_exists}, workflow.yaml: {wf_exists}",
    }


def main():
    parser = argparse.ArgumentParser(
        description="Test experiment data sync (run from inside ECS container)"
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Automated test commands
    lazy_parser = subparsers.add_parser(
        "test-lazy", help="Test lazy sync end-to-end (recommended)"
    )
    lazy_parser.add_argument("email", help="User email to test")

    proactive_parser = subparsers.add_parser(
        "test-proactive", help="Test proactive sync end-to-end"
    )
    proactive_parser.add_argument("email", help="User email to test")

    # Find user command
    find_parser = subparsers.add_parser("find-user", help="Find user by email")
    find_parser.add_argument("email", help="Email address (partial match)")

    # Status command
    status_parser = subparsers.add_parser(
        "status",
        help="Show system status (instances, ASG) and optionally user details",
    )
    status_parser.add_argument(
        "user_id",
        type=int,
        nargs="?",
        default=None,
        help="User ID (optional - if provided, shows user info and experiments)",
    )

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
    if args.command == "test-lazy":
        result = run_test_lazy_sync(args.email)
        print(f"\n{'='*60}")
        if result["status"] == "success":
            print(f"RESULT: {result['message']}")
        elif result["status"] == "partial":
            print(f"RESULT: {result['message']}")
            for endpoint, passed in result.get("results", {}).items():
                print(f"  {endpoint}: {'PASS' if passed else 'FAIL'}")
        else:
            print(f"RESULT: FAILED at step {result.get('step', '?')}")
            print(f"Error: {result.get('error', 'Unknown')}")
        print(f"{'='*60}\n")

    elif args.command == "test-proactive":
        result = run_test_proactive_sync(args.email)
        print(f"\n{'='*60}")
        if result["status"] == "success":
            print(f"RESULT: {result['message']}")
        elif result["status"] == "pending":
            print(f"RESULT: {result['message']}")
        else:
            print(f"RESULT: FAILED at step {result.get('step', '?')}")
            print(f"Error: {result.get('error', 'Unknown')}")
        print(f"{'='*60}\n")

    elif args.command == "find-user":
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
        # If user_id provided, show user info first
        if args.user_id:
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
                    print(f"\nAssigned Instance: {assignment['instance_id']}")
                    print(f"Last Activity: {assignment['last_activity']}")
                    print(f"Migration Count: {assignment['migration_count']}")
                    print(f"Active Workflows: {assignment['active_workflow_count']}")
                else:
                    print("\nNo free tier assignment found")

                # Show workspaces and experiments
                exp_result = list_experiments_for_user(args.user_id)
                if exp_result.get("status") == "success":
                    workspaces = exp_result.get("workspaces", [])
                    if workspaces:
                        print("\n=== Workspaces & Experiments ===")
                        for ws in workspaces:
                            ws_name = ws["workspace_name"]
                            ws_id = ws["workspace_id"]
                            print(f"\nWorkspace: {ws_name} (ID: {ws_id})")
                            experiments = ws.get("experiments", [])
                            if experiments:
                                for exp in experiments:
                                    print(f" - {exp['name']}")
                                    print(f"unique_id: {exp['unique_id']}")
                            else:
                                print("  (no experiments)")
                    else:
                        print("\nNo workspaces found")

        # Always show instance and ASG status
        instances = list_instances()
        print("\n=== Instances with User Counts ===")
        if not instances:
            print("No instances found")
        else:
            for inst in instances:
                print(
                    f"  {inst['instance_id']}: "
                    f"{inst['user_count']} users, "
                    f"latest: {inst['latest_activity']}"
                )

        print("\n=== ASG Status ===")
        asg_status = get_asg_status()
        if "error" in asg_status:
            print(f"Error: {asg_status['error']}")
        else:
            desired = asg_status["desired_capacity"]
            min_s = asg_status["min_size"]
            max_s = asg_status["max_size"]
            print(f"Desired/Min/Max: {desired}/{min_s}/{max_s}")
            print(f"Healthy: {asg_status['healthy_count']}")
            for inst in asg_status["instances"]:
                health = "OK" if inst["health_status"] == "Healthy" else "FAIL"
                iid = inst["instance_id"]
                state = inst["lifecycle_state"]
                print(f"  {iid}: {state} ({health})")

        print("\n=== ECS Service ===")
        ecs_status = get_ecs_service_status()
        if "error" in ecs_status:
            print(f"Error: {ecs_status['error']}")
        else:
            running = ecs_status["running_count"]
            pending = ecs_status["pending_count"]
            print(f"Tasks: {running} running, {pending} pending")

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
                    print(f"Deleted: {', '.join(detail['deleted_files'])}")


if __name__ == "__main__":
    main()
