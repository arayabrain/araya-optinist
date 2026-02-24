#!/usr/bin/env python3
"""
Hybrid User Migration Test

Automates infrastructure steps (ensure 2+ instances, migrate user via
Lambda) and pauses for manual GUI verification (experiments accessible,
data syncs from S3).

Supports both free and premium tiers with tier-specific Lambda and
ECS service selection.

WHERE TO RUN:
- Local development machine with AWS credentials

REQUIREMENTS:
- AWS credentials configured (boto3 access)
- Terraform outputs available in infrastructure/terraform/
- Python 3.8+ with boto3
- Cleanup Lambda redeployed with migrate_user action

HOW TO RUN:
  python test_user_migration.py {free,premium} USER_EMAIL
      [--no-scale-down]

TEST FLOW:
  1. Look up user assignment via cleanup Lambda
  2. Ensure >= 2 running instances (scale up if needed)
  3. Migrate user to a different instance
  4. Pause for manual GUI verification
  5. Scale ECS back to original count (unless --no-scale-down)
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import boto3
from botocore.exceptions import ClientError

_script_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_script_dir / ".." / ".."))
sys.path.insert(0, str(_script_dir / ".."))

from aws_constants import ECSTaskStatus  # noqa: E402

POLL_INTERVAL_SECONDS = 15
SCALE_UP_TIMEOUT_SECONDS = 300
EC2_STOP_TIMEOUT_SECONDS = 120
MIN_INSTANCES = 2
DEFAULT_REGION = "ap-northeast-1"
TERRAFORM_DIR = _script_dir.parent / "terraform"

TIER_FREE = "free"
TIER_PREMIUM = "premium"


def _load_terraform_outputs() -> Dict:
    """Load Terraform outputs from infrastructure/terraform/."""
    if not TERRAFORM_DIR.exists():
        print(f"Terraform directory not found: {TERRAFORM_DIR}")
        sys.exit(1)
    try:
        result = subprocess.run(
            ["terraform", "output", "-json"],
            cwd=str(TERRAFORM_DIR),
            capture_output=True,
            text=True,
            check=True,
        )
        outputs = json.loads(result.stdout)
        return {k: v["value"] for k, v in outputs.items()}
    except subprocess.CalledProcessError as e:
        print(f"Failed to load Terraform outputs: {e}")
        print(f"stderr: {e.stderr}")
        sys.exit(1)
    except FileNotFoundError:
        print("Terraform not found. Install Terraform.")
        sys.exit(1)


def _invoke_cleanup_lambda(
    lambda_client, function_name: str, action: str, **kwargs
) -> Optional[Dict]:
    """Invoke cleanup Lambda and return result dict."""
    event = {"action": action, **kwargs}
    try:
        response = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(event),
        )
        payload = json.loads(response["Payload"].read())
        status_code = payload.get("statusCode", 500)
        if status_code != 200:
            print(f"Lambda returned status {status_code}: {payload}")
            return None
        body = json.loads(payload.get("body", "{}"))
        return body.get("result", body)
    except ClientError as e:
        print(f"Failed to invoke cleanup Lambda: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"Failed to decode Lambda response: {e}")
        return None


def get_running_instance_ids(
    ecs_client, cluster_name: str, service_name: str
) -> List[str]:
    """Get EC2 instance IDs for running ECS tasks."""
    try:
        response = ecs_client.list_tasks(
            cluster=cluster_name,
            serviceName=service_name,
            desiredStatus=ECSTaskStatus.RUNNING,
        )
        task_arns = response.get("taskArns", [])
        if not task_arns:
            return []

        tasks_resp = ecs_client.describe_tasks(cluster=cluster_name, tasks=task_arns)
        container_arns = list(
            {
                t["containerInstanceArn"]
                for t in tasks_resp["tasks"]
                if "containerInstanceArn" in t
            }
        )
        if not container_arns:
            return []

        inst_resp = ecs_client.describe_container_instances(
            cluster=cluster_name,
            containerInstances=container_arns,
        )
        return [i["ec2InstanceId"] for i in inst_resp["containerInstances"]]
    except ClientError as e:
        print(f"Failed to get instance IDs: {e}")
        return []


def get_premium_instance_ids(ec2_client, terraform_ids: List[str]) -> List[str]:
    """Get running premium EC2 instance IDs.

    Discovers by Tier=premium tag to catch both Terraform-managed
    and Lambda-launched instances.
    """
    try:
        response = ec2_client.describe_instances(
            Filters=[
                {
                    "Name": "tag:Tier",
                    "Values": ["premium"],
                },
                {
                    "Name": "instance-state-name",
                    "Values": ["running"],
                },
            ],
        )
        ids = []
        for res in response["Reservations"]:
            for inst in res["Instances"]:
                ids.append(inst["InstanceId"])
        return ids
    except ClientError as e:
        print(f"Failed to get premium instances: {e}")
        return []


def _get_stopped_premium_instance(ec2_client) -> Optional[str]:
    """Find a stopped premium instance to start."""
    try:
        response = ec2_client.describe_instances(
            Filters=[
                {"Name": "tag:Tier", "Values": ["premium"]},
                {
                    "Name": "instance-state-name",
                    "Values": ["stopped"],
                },
            ],
        )
        for res in response["Reservations"]:
            for inst in res["Instances"]:
                return inst["InstanceId"]
        return None
    except ClientError as e:
        print(f"Failed to find stopped premium instances: {e}")
        return None


def _start_premium_instance(ec2_client, instance_id: str) -> bool:
    """Start a stopped premium instance and wait for running."""
    try:
        print(f"  Starting stopped instance {instance_id}...")
        ec2_client.start_instances(InstanceIds=[instance_id])
        waiter = ec2_client.get_waiter("instance_running")
        waiter.wait(
            InstanceIds=[instance_id],
            WaiterConfig={
                "Delay": POLL_INTERVAL_SECONDS,
                "MaxAttempts": SCALE_UP_TIMEOUT_SECONDS // POLL_INTERVAL_SECONDS,
            },
        )
        print(f"  Instance {instance_id} is now running")
        return True
    except ClientError as e:
        print(f"ERROR: Failed to start instance: {e}")
        return False


PREMIUM_LAUNCH_TEMPLATE_PREFIX = "subscr-optinist-premium-"
PREMIUM_INSTANCE_TYPE = "t3.large"
PREMIUM_INSTANCE_TAGS = [
    {"Key": "Name", "Value": "subscr-premium-running"},
    {"Key": "Type", "Value": "Premium-Instance"},
    {"Key": "Tier", "Value": "premium"},
    {"Key": "Service", "Value": "premium-tier"},
]


def _launch_premium_instance(ec2_client, subnet_ids: List[str]) -> Optional[str]:
    """Launch a new premium instance from the launch template."""
    try:
        resp = ec2_client.describe_launch_templates(
            Filters=[
                {
                    "Name": "launch-template-name",
                    "Values": [f"{PREMIUM_LAUNCH_TEMPLATE_PREFIX}*"],
                }
            ],
        )
        templates = resp.get("LaunchTemplates", [])
        if not templates:
            print("ERROR: No premium launch template found")
            return None
        template_id = templates[0]["LaunchTemplateId"]
        print(f"  Using launch template {template_id}")
    except ClientError as e:
        print(f"ERROR: Failed to find launch template: {e}")
        return None

    for i, subnet_id in enumerate(subnet_ids):
        try:
            print(
                f"  Launching in subnet {subnet_id} "
                f"(attempt {i + 1}/{len(subnet_ids)})"
            )
            response = ec2_client.run_instances(
                LaunchTemplate={
                    "LaunchTemplateId": template_id,
                    "Version": "$Latest",
                },
                InstanceType=PREMIUM_INSTANCE_TYPE,
                SubnetId=subnet_id,
                MinCount=1,
                MaxCount=1,
                TagSpecifications=[
                    {
                        "ResourceType": "instance",
                        "Tags": PREMIUM_INSTANCE_TAGS,
                    }
                ],
            )
            instance_id = response["Instances"][0]["InstanceId"]
            print(f"  Launched {instance_id}, waiting...")
            waiter = ec2_client.get_waiter("instance_running")
            waiter.wait(
                InstanceIds=[instance_id],
                WaiterConfig={
                    "Delay": POLL_INTERVAL_SECONDS,
                    "MaxAttempts": SCALE_UP_TIMEOUT_SECONDS // POLL_INTERVAL_SECONDS,
                },
            )
            print(f"  Instance {instance_id} is running")
            return instance_id
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code == "InsufficientInstanceCapacity":
                print("Insufficient capacity, trying next")
                continue
            print(f"ERROR: {code}: {e}")
            return None

    print("ERROR: All subnets exhausted")
    return None


def _resolve_tier_config(tier: str, outputs: Dict) -> Dict[str, str]:
    """Resolve Lambda/ECS names from Terraform outputs."""
    cluster = outputs.get("ecs_cluster_name", "")
    if tier == TIER_FREE:
        return {
            "cleanup_lambda": outputs.get(
                "free_cleanup_lambda_name",
                "subscr-free-cleanup",
            ),
            "cluster_name": cluster,
            "service_name": outputs.get(
                "ecs_service_name_autoscaling",
                outputs.get(
                    "ecs_service_name_free",
                    "subscr-optinist-cloud-service",
                ),
            ),
            "premium_instance_ids": [],
        }
    else:
        raw_ids = outputs.get("premium_instance_ids", [])
        if isinstance(raw_ids, str):
            raw_ids = [i.strip() for i in raw_ids.split(",") if i.strip()]
        return {
            "cleanup_lambda": outputs.get(
                "premium_cleanup_lambda_name",
                "subscr-premium-cleanup",
            ),
            "cluster_name": cluster,
            "service_name": outputs.get(
                "ecs_service_name_premium",
                "subscr-premium-optinist-cloud-service",
            ),
            "premium_instance_ids": raw_ids,
            "private_subnet_ids": list(outputs.get("private_subnet_ids", {}).values()),
        }


# ------------------------------------------------------------------
# Steps
# ------------------------------------------------------------------


def step_lookup_user(
    lambda_client,
    cleanup_lambda: str,
    user_email: str,
    tier: str,
) -> Dict:
    """Step 1: Look up user assignment."""
    print("\n" + "=" * 60)
    print("STEP 1: Look up user")
    print("=" * 60)

    result = _invoke_cleanup_lambda(
        lambda_client,
        cleanup_lambda,
        "get_user_assignment",
        user_email=user_email,
    )
    if not result:
        print("ERROR: Lambda invocation failed")
        sys.exit(1)

    if not result.get("success"):
        msg = result.get("message") or result.get("error") or "Unknown error"
        print(f"ERROR: {msg}")
        sys.exit(1)

    instance_id = result.get("instance_id")
    if not instance_id:
        print(f"User {user_email} has no instance " "assignment (not logged in yet?)")
        sys.exit(1)

    if tier == TIER_FREE:
        workflows = result.get("active_workflow_count", 0)
        if workflows > 0:
            print(
                f"ERROR: User has {workflows} active "
                "workflow(s). Wait for completion."
            )
            sys.exit(1)

    if tier == TIER_PREMIUM:
        status = result.get("status", "")
        if status != "active":
            print(
                f"ERROR: Premium assignment status " f"is '{status}', expected 'active'"
            )
            sys.exit(1)

    print(f"  Email:     {user_email}")
    print(f"  User ID:   {result.get('user_id')}")
    print(f"  Instance:  {instance_id}")
    if tier == TIER_FREE:
        print(f"  Workflows: " f"{result.get('active_workflow_count', 0)}")
    if tier == TIER_PREMIUM:
        print(f"  Status:    {result.get('status')}")

    return result


def step_ensure_instances(
    tier: str,
    ecs_client,
    ec2_client,
    cluster_name: str,
    service_name: str,
    premium_instance_ids: List[str],
    private_subnet_ids: Optional[List[str]] = None,
) -> List[str]:
    """Step 2: Ensure >= 2 running instances."""
    print("\n" + "=" * 60)
    print("STEP 2: Ensure 2+ running instances")
    print("=" * 60)

    if tier == TIER_FREE:
        instance_ids = get_running_instance_ids(ecs_client, cluster_name, service_name)
    else:
        instance_ids = get_premium_instance_ids(ec2_client, premium_instance_ids)

    print(f"Running instances ({len(instance_ids)}): " f"{instance_ids}")

    if len(instance_ids) == 0:
        print("ERROR: No running instances found")
        sys.exit(1)

    if len(instance_ids) >= MIN_INSTANCES:
        print("Already have enough instances")
        return instance_ids

    if tier == TIER_FREE:
        print(f"Scaling ECS service to {MIN_INSTANCES}...")
        try:
            ecs_client.update_service(
                cluster=cluster_name,
                service=service_name,
                desiredCount=MIN_INSTANCES,
            )
        except ClientError as e:
            print(f"ERROR: Failed to scale service: {e}")
            sys.exit(1)

        start = time.time()
        while time.time() - start < SCALE_UP_TIMEOUT_SECONDS:
            elapsed = int(time.time() - start)
            print(
                f"  Waiting for instances... "
                f"({elapsed}s / {SCALE_UP_TIMEOUT_SECONDS}s)"
            )
            time.sleep(POLL_INTERVAL_SECONDS)
            instance_ids = get_running_instance_ids(
                ecs_client, cluster_name, service_name
            )
            if len(instance_ids) >= MIN_INSTANCES:
                print(f"  Instances ready: {instance_ids}")
                return instance_ids

        print(
            "ERROR: Timed out waiting for instances "
            f"after {SCALE_UP_TIMEOUT_SECONDS}s"
        )
        sys.exit(1)
    else:
        # Try starting a stopped instance first
        stopped_id = _get_stopped_premium_instance(ec2_client)
        if stopped_id:
            if not _start_premium_instance(ec2_client, stopped_id):
                sys.exit(1)
        else:
            # No stopped instances; launch a new one
            print("  No stopped instances, launching new...")
            new_id = _launch_premium_instance(ec2_client, private_subnet_ids or [])
            if not new_id:
                print("ERROR: Failed to launch instance")
                sys.exit(1)

        instance_ids = get_premium_instance_ids(ec2_client, premium_instance_ids)
        print(f"Running instances ({len(instance_ids)}): " f"{instance_ids}")
        if len(instance_ids) >= MIN_INSTANCES:
            return instance_ids

        print("ERROR: Still not enough instances")
        sys.exit(1)


def step_migrate_user(
    lambda_client,
    cleanup_lambda: str,
    user_email: str,
    current_instance: str,
    instance_ids: List[str],
) -> Dict:
    """Step 3: Migrate user to a different instance."""
    print("\n" + "=" * 60)
    print("STEP 3: Migrate user")
    print("=" * 60)

    candidates = [i for i in instance_ids if i != current_instance]
    if not candidates:
        candidates = instance_ids

    target = candidates[0]
    print(f"  Source:  {current_instance}")
    print(f"  Target:  {target}")

    result = _invoke_cleanup_lambda(
        lambda_client,
        cleanup_lambda,
        "migrate_user",
        user_email=user_email,
        target_instance_id=target,
    )
    if not result:
        print("ERROR: Lambda invocation failed")
        sys.exit(1)

    if not result.get("success"):
        msg = result.get("message") or result.get("error") or "Unknown error"
        print(f"ERROR: Migration blocked - {msg}")
        sys.exit(1)

    count = result.get("migration_count", "?")
    print(f"  Migration successful (count: {count})")
    return result


def step_manual_verification():
    """Step 4: Pause for manual GUI verification."""
    print("\n" + "=" * 60)
    print("STEP 4: Manual verification")
    print("=" * 60)
    print()
    print("Verify the following in the GUI:")
    print("  [ ] Log in as the migrated user")
    print("  [ ] Experiments list loads correctly")
    print("  [ ] Visualizations render properly")
    print("  [ ] Can start a new workflow run")
    print()
    print("Press Enter when done (Ctrl+C to abort)...")
    try:
        input()
    except EOFError:
        print("(non-interactive mode, skipping)")


def _premium_cleanup(
    ec2_client,
    lambda_client,
    cleanup_lambda: str,
    source_instance: str,
):
    """Stop empty source instance and reconcile DB state.

    Runs in the finally block -- all errors are warnings to
    avoid masking the real test outcome.
    """
    if not source_instance:
        print("  Source instance unknown, skipping stop")
        return

    # Check if source instance still has users
    try:
        result = _invoke_cleanup_lambda(
            lambda_client,
            cleanup_lambda,
            "get_instance_users",
            instance_id=source_instance,
        )
        if result is None:
            print("  WARNING: Could not query instance users")
        elif result.get("count", 0) > 0:
            print(
                f"  Source {source_instance} still has "
                f"{result['count']} user(s): "
                f"{result['user_ids']} -- skipping stop"
            )
        else:
            print(f"  Source {source_instance} has 0 users," " stopping...")
            try:
                ec2_client.stop_instances(InstanceIds=[source_instance])
                waiter = ec2_client.get_waiter("instance_stopped")
                waiter.wait(
                    InstanceIds=[source_instance],
                    WaiterConfig={
                        "Delay": POLL_INTERVAL_SECONDS,
                        "MaxAttempts": (
                            EC2_STOP_TIMEOUT_SECONDS // POLL_INTERVAL_SECONDS
                        ),
                    },
                )
                print(f"  Instance {source_instance} stopped")
            except ClientError as e:
                print(f"  WARNING: Failed to stop: {e}")
    except Exception as e:
        print(f"  WARNING: Instance check failed: {e}")

    # Reconcile DB state regardless of stop outcome
    try:
        rec = _invoke_cleanup_lambda(lambda_client, cleanup_lambda, "reconcile")
        if rec is None:
            print("  WARNING: Reconcile call failed")
        else:
            print(
                f"  Reconcile done: {rec.get('update_count', 0)}"
                f" updated, "
                f"{rec.get('cleanup_count', 0)} cleaned"
            )
    except Exception as e:
        print(f"  WARNING: Reconcile failed: {e}")


def step_cleanup(
    tier: str,
    ecs_client,
    ec2_client,
    lambda_client,
    cleanup_lambda: str,
    current_instance: str,
    cluster_name: str,
    service_name: str,
    scale_down: bool,
):
    """Step 5: Cleanup (stop empty source / scale down ECS)."""
    print("\n" + "=" * 60)
    print("STEP 5: Cleanup")
    print("=" * 60)

    if not scale_down:
        print("Skipping scale-down (--no-scale-down)")
        return

    if tier == TIER_PREMIUM:
        _premium_cleanup(
            ec2_client,
            lambda_client,
            cleanup_lambda,
            current_instance,
        )
        return

    print("Scaling ECS service back to 1...")
    try:
        ecs_client.update_service(
            cluster=cluster_name,
            service=service_name,
            desiredCount=1,
        )
        print("  Service scaled to desiredCount=1")
    except ClientError as e:
        print(f"WARNING: Failed to scale down: {e}")


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Hybrid user migration test")
    parser.add_argument(
        "tier",
        choices=[TIER_FREE, TIER_PREMIUM],
        help="User tier to test",
    )
    parser.add_argument(
        "user_email",
        help="Email of the user to migrate",
    )
    parser.add_argument(
        "--no-scale-down",
        action="store_true",
        help="Skip scaling ECS back to 1 after test",
    )
    args = parser.parse_args()

    region = os.environ.get("AWS_REGION", DEFAULT_REGION)
    outputs = _load_terraform_outputs()
    cfg = _resolve_tier_config(args.tier, outputs)

    lambda_client = boto3.client("lambda", region_name=region)
    ecs_client = boto3.client("ecs", region_name=region)
    ec2_client = boto3.client("ec2", region_name=region)

    print("=" * 60)
    print(f"USER MIGRATION TEST ({args.tier.upper()})")
    print("=" * 60)
    print(f"  User:    {args.user_email}")
    print(f"  Tier:    {args.tier}")
    print(f"  Region:  {region}")
    print(f"  Cluster: {cfg['cluster_name']}")
    print(f"  Service: {cfg['service_name']}")
    print(f"  Lambda:  {cfg['cleanup_lambda']}")

    scale_down = not args.no_scale_down
    current_instance = ""

    try:
        assignment = step_lookup_user(
            lambda_client,
            cfg["cleanup_lambda"],
            args.user_email,
            args.tier,
        )
        current_instance = assignment["instance_id"]

        instance_ids = step_ensure_instances(
            args.tier,
            ecs_client,
            ec2_client,
            cfg["cluster_name"],
            cfg["service_name"],
            cfg["premium_instance_ids"],
            cfg.get("private_subnet_ids", []),
        )

        step_migrate_user(
            lambda_client,
            cfg["cleanup_lambda"],
            args.user_email,
            current_instance,
            instance_ids,
        )

        step_manual_verification()

    except KeyboardInterrupt:
        print("\n\nAborted by user.")
    finally:
        step_cleanup(
            args.tier,
            ecs_client,
            ec2_client,
            lambda_client,
            cfg["cleanup_lambda"],
            current_instance,
            cfg["cluster_name"],
            cfg["service_name"],
            scale_down,
        )

    print("\nDone.")


if __name__ == "__main__":
    main()
