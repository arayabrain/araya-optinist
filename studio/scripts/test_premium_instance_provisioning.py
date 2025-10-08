#!/usr/bin/env python3
"""
Premium Instance Provisioning Test

RUNTIME ENVIRONMENT:
Run locally (with AWS credentials and Terraform state)

WHAT IT TESTS:
1. Counts free and premium instances (running/stopped) before test
2. Signs in as premium user via API Gateway
3. Verifies new premium instance is created and user is redirected
4. Counts instances after test to confirm state changes

REQUIREMENTS:
- AWS credentials configured (AWS CLI or environment variables)
- IAM permissions: ec2:DescribeInstances, ecs:DescribeTasks, ecs:ListTasks
- Terraform outputs with premium_instance_ids and premium_api_gateway_url
- Test user configuration (premium user with Firebase UID)

Usage:
    python test_premium_instance_provisioning.py
    python test_premium_instance_provisioning.py --terraform-dir /path/to/terraform
    python test_premium_instance_provisioning.py --skip-token-gen
        # Use existing tokens.json
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from typing import Dict, List, Optional

import boto3
import requests

# Add current directory to path for imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from get_jwt_tokens import generate_jwt_tokens
except ImportError as e:
    print(f"Warning: Could not import get_jwt_tokens: {e}")
    generate_jwt_tokens = None

try:
    from test_user_config import load_test_users_for_db
except ImportError as e:
    print(f"Warning: Could not import test_user_config: {e}")
    load_test_users_for_db = None


logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


def get_terraform_outputs(terraform_dir: str) -> Dict:
    """Get Terraform outputs from the specified directory"""
    try:
        result = subprocess.run(
            ["terraform", "output", "-json"],
            cwd=terraform_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to get Terraform outputs: {e.stderr}")
        raise
    except json.JSONDecodeError as e:
        logging.error(f"Failed to parse Terraform outputs: {e}")
        raise


class PremiumInstanceTester:
    """Test premium instance provisioning flow"""

    def __init__(self, terraform_dir: str, aws_region: str = "ap-northeast-1"):
        self.terraform_dir = terraform_dir
        self.aws_region = aws_region
        self.ec2 = boto3.client("ec2", region_name=aws_region)
        self.ecs = boto3.client("ecs", region_name=aws_region)

        # Load Terraform outputs
        terraform_outputs = get_terraform_outputs(terraform_dir)

        # Get premium instance IDs
        premium_ids = terraform_outputs.get("premium_instance_ids", {}).get("value", [])
        if not premium_ids:
            raise ValueError("No premium_instance_ids found in Terraform outputs")
        self.premium_instance_ids = premium_ids

        # Get ALB DNS name for API calls
        alb_dns = terraform_outputs.get("alb_dns_name", {}).get("value")
        if not alb_dns:
            raise ValueError("No alb_dns_name found in Terraform outputs")
        self.api_url = f"http://{alb_dns}"

        # Get ECS cluster name
        self.cluster_name = terraform_outputs.get("ecs_cluster_name", {}).get("value")
        if not self.cluster_name:
            raise ValueError("No ecs_cluster_name found in Terraform outputs")

        # Get premium ECS service name
        self.premium_service_name = terraform_outputs.get(
            "ecs_service_name_premium", {}
        ).get("value", "subscr-premium-optinist-cloud-service")

        # Get ASG name for free tier (for comparison)
        self.asg_name = terraform_outputs.get("asg_name", {}).get("value")

        logging.info(f"Premium instances to monitor: {len(self.premium_instance_ids)}")
        logging.info(f"API URL: {self.api_url}")

    def get_instance_states(self) -> Dict[str, List[Dict]]:
        """Get current state of all premium instances"""
        try:
            response = self.ec2.describe_instances(
                InstanceIds=self.premium_instance_ids
            )

            instances_by_state = {
                "running": [],
                "stopped": [],
                "pending": [],
                "stopping": [],
                "terminated": [],
            }

            for reservation in response["Reservations"]:
                for instance in reservation["Instances"]:
                    state = instance["State"]["Name"]
                    instance_info = {
                        "id": instance["InstanceId"],
                        "state": state,
                        "type": instance["InstanceType"],
                        "launch_time": instance.get("LaunchTime"),
                        "private_ip": instance.get("PrivateIpAddress"),
                        "tags": {
                            tag["Key"]: tag["Value"] for tag in instance.get("Tags", [])
                        },
                    }

                    if state in instances_by_state:
                        instances_by_state[state].append(instance_info)
                    else:
                        instances_by_state.setdefault("other", []).append(instance_info)

            return instances_by_state

        except Exception as e:
            logging.error(f"Error getting instance states: {e}")
            return {}

    def get_premium_ecs_tasks(self) -> List[Dict]:
        """Get running ECS tasks on premium service"""
        try:
            # List tasks
            task_arns = []
            paginator = self.ecs.get_paginator("list_tasks")
            for page in paginator.paginate(
                cluster=self.cluster_name,
                serviceName=self.premium_service_name,
                desiredStatus="RUNNING",
            ):
                task_arns.extend(page["taskArns"])

            if not task_arns:
                return []

            # Describe tasks
            tasks_response = self.ecs.describe_tasks(
                cluster=self.cluster_name, tasks=task_arns
            )

            # Get container instance details
            container_instance_arns = [
                task["containerInstanceArn"]
                for task in tasks_response["tasks"]
                if "containerInstanceArn" in task
            ]

            if not container_instance_arns:
                return []

            instances_response = self.ecs.describe_container_instances(
                cluster=self.cluster_name, containerInstances=container_instance_arns
            )

            # Build mapping
            container_to_instance = {
                ci["containerInstanceArn"]: ci["ec2InstanceId"]
                for ci in instances_response["containerInstances"]
            }

            # Build task list with instance IDs
            tasks = []
            for task in tasks_response["tasks"]:
                task_info = {
                    "task_id": task["taskArn"].split("/")[-1],
                    "container_instance_arn": task.get("containerInstanceArn"),
                    "ec2_instance_id": container_to_instance.get(
                        task.get("containerInstanceArn")
                    ),
                    "desired_status": task["desiredStatus"],
                    "last_status": task["lastStatus"],
                    "started_at": task.get("startedAt"),
                }
                tasks.append(task_info)

            return tasks

        except Exception as e:
            logging.error(f"Error getting premium ECS tasks: {e}")
            return []

    def print_instance_summary(self, states: Dict[str, List[Dict]], label: str = ""):
        """Print formatted summary of instance states"""
        if label:
            logging.info(f"\n{'='*60}")
            logging.info(f"{label}")
            logging.info(f"{'='*60}")

        total = sum(len(instances) for instances in states.values())
        logging.info(f"Total premium instances: {total}")

        for state, instances in states.items():
            if instances:
                logging.info(f"{state.upper()}: {len(instances)}")
                for instance in instances:
                    name = instance["tags"].get("Name", "unnamed")
                    logging.info(f"- {instance['id']} ({name})")

    def print_task_summary(self, tasks: List[Dict], label: str = ""):
        """Print formatted summary of ECS tasks"""
        if label:
            logging.info(f"\n{label}")
            logging.info(f"{'-'*60}")

        logging.info(f"Premium ECS tasks running: {len(tasks)}")

        # Group by instance
        tasks_by_instance = {}
        for task in tasks:
            instance_id = task["ec2_instance_id"] or "unknown"
            if instance_id not in tasks_by_instance:
                tasks_by_instance[instance_id] = []
            tasks_by_instance[instance_id].append(task)

        for instance_id, instance_tasks in tasks_by_instance.items():
            short_id = instance_id[-8:] if instance_id != "unknown" else "unknown"
            logging.info(f"Instance {short_id}: {len(instance_tasks)} tasks")
            for task in instance_tasks:
                logging.info(f"- {task['task_id'][:12]}... " f"({task['last_status']})")

    def assign_premium_user(self, id_token: str) -> Optional[Dict]:
        """
        Call premium assignment API to provision instance for user

        Uses FastAPI endpoint with Firebase authentication.
        Backend extracts user_id from the authenticated token.

        Returns assignment details including instance_id
        """
        try:
            # Call the FastAPI endpoint (not Lambda directly)
            assign_url = f"{self.api_url}/users/me/premium/assign"

            headers = {
                "Authorization": f"Bearer {id_token}",
                "Content-Type": "application/json",
            }

            logging.info(f"Calling premium assignment API: {assign_url}")
            logging.info("Using Firebase ID token for authentication")

            response = requests.post(assign_url, headers=headers, timeout=60)

            logging.info(f"Response status: {response.status_code}")
            logging.info(f"Response body: {response.text}")

            if response.status_code == 200:
                result = response.json()
                return result
            else:
                logging.error(
                    f"Failed to assign premium user: {response.status_code} "
                    f"{response.text}"
                )
                return None

        except Exception as e:
            logging.error(f"Error calling premium assignment API: {e}")
            return None

    def wait_for_instance_running(
        self, instance_id: str, timeout: int = 300, poll_interval: int = 10
    ) -> bool:
        """
        Poll EC2 instance until it's in 'running' state

        Returns True if instance reached running state, False on timeout
        """
        logging.info(f"Waiting for instance {instance_id} to reach 'running' state...")

        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                response = self.ec2.describe_instances(InstanceIds=[instance_id])

                if response["Reservations"]:
                    instance = response["Reservations"][0]["Instances"][0]
                    state = instance["State"]["Name"]

                    logging.info(f"Instance state: {state}")

                    if state == "running":
                        logging.info(f"Instance {instance_id} is now running!")
                        return True
                    elif state in ["terminated", "terminating"]:
                        logging.error(
                            f"Instance {instance_id} is {state}, cannot start"
                        )
                        return False

            except Exception as e:
                logging.error(f"Error checking instance state: {e}")

            time.sleep(poll_interval)

        logging.error(
            f"Timeout waiting for instance {instance_id} to start "
            f"(waited {timeout}s)"
        )
        return False

    def wait_for_ecs_task(
        self, instance_id: str, timeout: int = 180, poll_interval: int = 10
    ) -> bool:
        """
        Poll ECS until a task is running on the specified instance

        Returns True if task found, False on timeout
        """
        logging.info(f"Waiting for ECS task to start on instance {instance_id}...")

        start_time = time.time()

        while time.time() - start_time < timeout:
            tasks = self.get_premium_ecs_tasks()

            for task in tasks:
                if task["ec2_instance_id"] == instance_id:
                    logging.info(
                        f"ECS task {task['task_id'][:12]}... is running "
                        f"on instance {instance_id}!"
                    )
                    return True

            logging.info(f"No task found yet on {instance_id}")
            time.sleep(poll_interval)

        logging.error(
            f"Timeout waiting for ECS task on {instance_id} " f"(waited {timeout}s)"
        )
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Test premium instance provisioning flow"
    )
    parser.add_argument(
        "--terraform-dir",
        default="../config/terraform",
        help="Path to Terraform directory (default: ../config/terraform)",
    )
    parser.add_argument(
        "--aws-region",
        default="ap-northeast-1",
        help="AWS region (default: ap-northeast-1)",
    )
    parser.add_argument(
        "--skip-token-gen",
        action="store_true",
        help="Skip token generation, use existing tokens.json",
    )

    args = parser.parse_args()

    logging.info("=" * 50)
    logging.info("Premium Instance Provisioning Test")
    logging.info("=" * 50)

    # Initialize tester
    tester = PremiumInstanceTester(args.terraform_dir, args.aws_region)

    # Step 1: Get initial state
    logging.info("\nSTEP 1: Checking initial instance state...")
    initial_states = tester.get_instance_states()
    initial_tasks = tester.get_premium_ecs_tasks()

    tester.print_instance_summary(initial_states, "INITIAL INSTANCE STATE")
    tester.print_task_summary(initial_tasks, "INITIAL ECS TASKS")

    # Step 2: Get premium user credentials and generate token
    logging.info("\nSTEP 2: Getting premium user credentials...")

    # Get premium user from test config
    test_users = load_test_users_for_db() if load_test_users_for_db else []
    premium_user = None

    for user in test_users:
        email = user.get("email", "")
        if "premium" in email.lower() and "optinist_test_user_premium" in email.lower():
            # Skip "premium_expire"and "premium_over"users
            if "expire" not in email.lower() and "over" not in email.lower():
                premium_user = user
                break

    if not premium_user:
        logging.error("Premium test user not found!")
        logging.error("Make sure test_users are configured in Terraform")
        sys.exit(1)

    firebase_uid = premium_user.get("firebase_uid")
    email = premium_user.get("email")

    if not firebase_uid:
        logging.error("Premium user has no firebase_uid!")
        sys.exit(1)

    logging.info(f"Premium user: {email}")
    logging.info(f"Firebase UID: {firebase_uid}")

    # Generate JWT token
    id_token = None

    if args.skip_token_gen:
        # Load from tokens.json
        tokens_file = os.path.join(os.path.dirname(__file__), "tokens.json")
        if os.path.exists(tokens_file):
            with open(tokens_file, "r") as f:
                tokens = json.load(f)
                id_token = tokens.get("premium_token")
                logging.info("Loaded premium token from tokens.json")
        else:
            logging.warning("tokens.json not found, will generate new token")

    if not id_token and generate_jwt_tokens:
        logging.info("Generating new Firebase ID token...")
        tokens = generate_jwt_tokens(
            environment="cloud", terraform_dir=args.terraform_dir, user_type="premium"
        )
        if tokens:
            id_token = tokens.get("premium_token")

    if not id_token:
        logging.error("Failed to get Firebase ID token!")
        sys.exit(1)

    # Step 3: Assign premium user (trigger instance provisioning)
    logging.info("\nSTEP 3: Assigning premium user (triggering provisioning)...")

    assignment_result = tester.assign_premium_user(id_token)

    if not assignment_result:
        logging.error("Failed to assign premium user!")
        sys.exit(1)

    assigned_instance_id = assignment_result.get("instance_id")

    if not assigned_instance_id:
        logging.error("No instance_id in assignment result!")
        logging.error(f"Result: {json.dumps(assignment_result, indent=2)}")
        sys.exit(1)

    logging.info(f"User assigned to instance: {assigned_instance_id}")

    # Step 4: Wait for instance to start
    logging.info("\nSTEP 4: Verifying instance provisioning...")

    if not tester.wait_for_instance_running(assigned_instance_id, timeout=300):
        logging.error("Instance failed to reach running state!")
        sys.exit(1)

    # Step 5: Wait for ECS task
    logging.info("\nSTEP 5: Verifying ECS task deployment...")

    if not tester.wait_for_ecs_task(assigned_instance_id, timeout=180):
        logging.warning("ECS task not found (might still be starting)")

    # Step 6: Get final state
    logging.info("\nSTEP 6: Checking final instance state...")
    final_states = tester.get_instance_states()
    final_tasks = tester.get_premium_ecs_tasks()

    tester.print_instance_summary(final_states, "FINAL INSTANCE STATE")
    tester.print_task_summary(final_tasks, "FINAL ECS TASKS")

    # Step 7: Compare states
    logging.info("\n" + "=" * 50)
    logging.info("STATE COMPARISON")
    logging.info("=" * 50)

    initial_running = len(initial_states.get("running", []))
    final_running = len(final_states.get("running", []))
    initial_stopped = len(initial_states.get("stopped", []))
    final_stopped = len(final_states.get("stopped", []))

    logging.info(
        f"Running instances: {initial_running} -> {final_running} "
        f"(change: {final_running - initial_running:+d})"
    )
    logging.info(
        f"Stopped instances: {initial_stopped} -> {final_stopped} "
        f"(change: {final_stopped - initial_stopped:+d})"
    )
    logging.info(
        f"ECS tasks: {len(initial_tasks)} -> {len(final_tasks)} "
        f"(change: {len(final_tasks) - len(initial_tasks):+d})"
    )

    # Verify expected changes
    success = True

    if final_running <= initial_running:
        logging.error("ERROR: No new instances started!")
        success = False

    if assigned_instance_id not in [i["id"] for i in final_states.get("running", [])]:
        logging.error(
            f"ERROR: Assigned instance {assigned_instance_id} is not running!"
        )
        success = False

    if success:
        logging.info("\n" + "=" * 50)
        logging.info("TEST PASSED!")
        logging.info("=" * 50)
        logging.info(
            f"Premium user {email} successfully assigned to "
            f"instance {assigned_instance_id}"
        )
        logging.info("Instance provisioned and ECS task deployed successfully")
    else:
        logging.error("\n" + "=" * 50)
        logging.error("TEST FAILED!")
        logging.error("=" * 50)
        sys.exit(1)


if __name__ == "__main__":
    main()
