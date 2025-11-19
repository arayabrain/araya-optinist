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
import pymysql
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


def cleanup_test_user_assignments(
    test_user_emails: List[str], terraform_dir: str
) -> int:
    """
    Clean up database assignments for test users by invoking the premium_cleanup Lambda.

    This function uses a two-tier cleanup approach:

    1. PRIMARY METHOD (Lambda invocation):
       - Invokes the premium_cleanup Lambda with action="cleanup_test_users"
       - Lambda has VPC access to RDS and can connect from anywhere
       - Works from local machines, CI/CD, or any environment with AWS credentials
       - Also cleans up AWS resources (ALB rules, target groups)

    2. FALLBACK METHOD (Direct RDS connection):
       - Only used if Lambda invocation fails
       - Attempts direct pymysql connection to RDS (5 second timeout)
       - Will timeout from local machines (RDS is VPC-isolated)
       - May succeed if running from within VPC (e.g., from ECS task)
       - Only cleans database records, not AWS resources

    Usage:
        # Before running tests, clean up any stale assignments
        cleanup_test_user_assignments(
            test_user_emails=["user1@test.com", "user2@test.com"],
            terraform_dir="/path/to/terraform"
        )

    Args:
        test_user_emails: List of user emails to clean up
        (e.g., ["user1@test.com", "user2@test.com"])
        terraform_dir: Path to terraform directory to get Lambda function name
            and RDS connection details from Terraform outputs

    Returns:
        Number of assignments deleted (0 if both methods fail)

    Note:
        This function is non-fatal - if cleanup fails, it logs a warning and returns 0.
        The test can still proceed with potentially stale data.
    """
    try:
        # Get Terraform outputs to find Lambda function name
        terraform_outputs = get_terraform_outputs(terraform_dir)
        cleanup_lambda_name = terraform_outputs.get(
            "premium_cleanup_lambda_name", {}
        ).get("value", "")

        if not cleanup_lambda_name:
            logging.error(
                "Could not find premium_cleanup_lambda_name in Terraform outputs"
            )
            return 0

        logging.info(
            f"Invoking Lambda {cleanup_lambda_name} to clean up "
            f"{len(test_user_emails)} test users"
        )

        # Invoke the Lambda function with cleanup_test_users action
        lambda_client = boto3.client("lambda")
        payload = {"action": "cleanup_test_users", "user_emails": test_user_emails}

        response = lambda_client.invoke(
            FunctionName=cleanup_lambda_name,
            InvocationType="RequestResponse",  # Synchronous invocation
            Payload=json.dumps(payload),
        )

        # Parse the response
        response_payload = json.loads(response["Payload"].read())
        status_code = response_payload.get("statusCode", 500)

        if status_code != 200:
            logging.error(
                f"Lambda cleanup failed with status {status_code}: {response_payload}"
            )
            return 0

        # Parse the body (which is JSON string)
        body = json.loads(response_payload.get("body", "{}"))
        result = body.get("result", {})
        deleted_count = result.get("assignments_deleted", 0)
        message = result.get("message", "No message")

        logging.info(f"Lambda cleanup result: {message}")
        logging.info(f"Deleted {deleted_count} assignment(s)")

        return deleted_count

    except Exception as e:
        logging.warning(f"Lambda cleanup failed (non-fatal): {e}")
        # Fallback: try direct RDS connection (may not work from local machine)
        logging.info("Attempting fallback direct RDS connection...")
        try:
            terraform_outputs = get_terraform_outputs(terraform_dir)
            rds_endpoint = terraform_outputs.get("rds_endpoint", {}).get("value", "")
            rds_host = (
                rds_endpoint.split(":")[0]
                if rds_endpoint
                else os.environ.get("RDS_HOST", "localhost")
            )
            rds_user = os.environ.get("RDS_USER", "admin")
            rds_password = os.environ.get("RDS_PASSWORD", "")
            rds_database = os.environ.get("RDS_DATABASE", "optinist")

            connection = pymysql.connect(
                host=rds_host,
                port=3306,
                user=rds_user,
                password=rds_password,
                database=rds_database,
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
                connect_timeout=5,
            )

            deleted_count = 0
            with connection.cursor() as cursor:
                email_placeholders = ", ".join(["%s"] * len(test_user_emails))
                cursor.execute(
                    f"SELECT id FROM users WHERE email IN ({email_placeholders})",
                    test_user_emails,
                )
                user_rows = cursor.fetchall()
                user_ids = [str(row["id"]) for row in user_rows]

                if user_ids:
                    id_placeholders = ", ".join(["%s"] * len(user_ids))
                    query = (
                        f"DELETE FROM premium_user_assignments "
                        f"WHERE user_id IN ({id_placeholders})"
                    )
                    cursor.execute(query, user_ids)
                    deleted_count = cursor.rowcount
                    connection.commit()

            connection.close()
            logging.info(
                f"Fallback RDS cleanup succeeded: {deleted_count} assignments deleted"
            )
            return deleted_count

        except Exception as fallback_error:
            logging.warning(f"Fallback RDS cleanup also failed: {fallback_error}")
            return 0


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
        # Use the domain name instead of ELB DNS to match SSL certificate
        self.api_url = "https://araya-optinist.com"

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
        """Get current state of all premium instances (includes dynamically-created)"""
        try:
            # Query ALL premium instances by tag, not just Terraform-managed ones
            # This is crucial for detecting instances created by autoscaling
            response = self.ec2.describe_instances(
                Filters=[
                    {
                        "Name": "instance-state-name",
                        "Values": ["pending", "running", "stopping", "stopped"],
                    },
                    # Match instances with "Tier=premium" OR "premium" in Name tag
                    {"Name": "tag:Tier", "Values": ["premium", "Premium"]},
                ]
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

            # Timeout increased to 180s to accommodate cold starts
            # (stopped instance startup)
            response = requests.post(assign_url, headers=headers, timeout=180)

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

    def unassign_premium_user(self, id_token: str) -> bool:
        """
        Call premium unassignment API to release instance for user.
        Returns True on success, False on failure.
        """
        try:
            unassign_url = f"{self.api_url}/users/me/premium/assign"
            headers = {
                "Authorization": f"Bearer {id_token}",
                "Content-Type": "application/json",
            }
            logging.info("Calling premium unassignment API for user...")
            response = requests.delete(unassign_url, headers=headers, timeout=60)
            logging.info(
                f"Unassignment response: {response.status_code} {response.text}"
            )
            if response.status_code != 200:
                logging.warning(f"Could not unassign user: {response.text}")
            return response.status_code == 200
        except Exception as e:
            logging.error(f"Error calling premium unassignment API: {e}")
            return False

    def cleanup_and_reset_state(
        self, test_users: List[Dict], terraform_dir: str, skip_token_gen: bool
    ):
        """Unassigns users and stops all premium instances to ensure a clean state."""
        logging.info("=" * 50)
        logging.info("STEP 0: CLEANUP AND RESET")
        logging.info("=" * 50)

        # Unassign all known test users
        users_to_unassign = [
            {"email_keyword": "optinist_test_user_premium", "token_type": "premium"},
            {
                "email_keyword": "optinist_test_user_premium_over",
                "token_type": "premium_over",
            },
        ]

        for user_info in users_to_unassign:
            # Find user
            target_user = None
            for user in test_users:
                email = user.get("email", "")
                if user_info["email_keyword"] in email.lower():
                    if user_info["email_keyword"] == "optinist_test_user_premium" and (
                        "expire" in email.lower() or "over" in email.lower()
                    ):
                        continue
                    target_user = user
                    break

            if not target_user:
                logging.warning(
                    f"User for cleanup not found: {user_info['email_keyword']}"
                )
                continue

            # Get token
            id_token = None
            token_key = f"{user_info['token_type']}_token"
            if skip_token_gen:
                tokens_file = os.path.join(os.path.dirname(__file__), "tokens.json")
                if os.path.exists(tokens_file):
                    with open(tokens_file, "r") as f:
                        tokens = json.load(f)
                        id_token = tokens.get(token_key)

            if not id_token and generate_jwt_tokens:
                logging.info(f"Generating token for cleanup: {user_info['token_type']}")
                tokens = generate_jwt_tokens(
                    environment="cloud",
                    terraform_dir=terraform_dir,
                    user_type=user_info["token_type"],
                )
                if tokens:
                    id_token = tokens.get(token_key)

            if id_token:
                logging.info(f"Unassigning user: {target_user['email']}")
                self.unassign_premium_user(id_token)
            else:
                logging.warning(
                    f"Could not get token to unassign user: {target_user['email']}"
                )

        # Force cleanup database assignments for test users
        # This ensures a clean state even if API unassignment failed
        # Extract emails from test users that were being unassigned
        test_user_emails = []
        for user_info in users_to_unassign:
            for user in test_users:
                email = user.get("email", "")
                if user_info["email_keyword"] in email.lower():
                    if user_info["email_keyword"] == "optinist_test_user_premium" and (
                        "expire" in email.lower() or "over" in email.lower()
                    ):
                        continue
                    test_user_emails.append(email)
                    break

        if test_user_emails:
            logging.info(f"Forcing database cleanup for test users: {test_user_emails}")
            cleanup_test_user_assignments(test_user_emails, terraform_dir)
        else:
            logging.warning("No test user emails found for database cleanup")

        # Stop any running premium instances
        logging.info("Stopping all premium instances...")
        try:
            initial_states = self.get_instance_states()
            running_instances = initial_states.get("running", [])
            if running_instances:
                instance_ids = [inst["id"] for inst in running_instances]
                logging.info(f"Found running instances to stop: {instance_ids}")
                self.ec2.stop_instances(InstanceIds=instance_ids)
                waiter = self.ec2.get_waiter("instance_stopped")
                waiter.wait(InstanceIds=instance_ids)
                logging.info("Successfully stopped instances.")
            else:
                logging.info("No running premium instances found to stop.")
        except Exception as e:
            logging.error(f"Failed to stop instances during cleanup: {e}")

    def get_user_assignment_status(self, id_token: str) -> Optional[Dict]:
        """Get the current premium assignment status for the user via the API."""
        try:
            status_url = f"{self.api_url}/users/me/premium/status"
            headers = {"Authorization": f"Bearer {id_token}"}
            response = requests.get(status_url, headers=headers, timeout=30)

            if response.status_code == 200:
                # Check if response has content before parsing JSON
                if not response.text or response.text.strip() == "":
                    logging.warning("API returned 200 but empty response body")
                    return None
                return response.json()
            else:
                logging.warning(
                    f"Failed to get user status: {response.status_code} {response.text}"
                )
                return None
        except Exception as e:
            logging.error(f"Error getting user assignment status: {e}")
            return None

    def wait_for_instance_running(
        self, instance_id: str, timeout: int = 300, poll_interval: int = 10
    ) -> bool:
        """
        Poll EC2 instance until it's in 'running' state

        Returns True if instance reached running state, False on timeout
        """
        # Handle special case: autoscaling-pool is not a real instance
        if instance_id == "autoscaling-pool":
            logging.info(
                "Instance is autoscaling-pool (shared pool), skipping EC2 state checks"
            )
            return True

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
        self, instance_id: str, timeout: int = 600, poll_interval: int = 20
    ) -> bool:
        """
        Poll ECS until a task is running on the specified instance
        Returns True if task found and running, False on timeout
        """
        # Handle special case: autoscaling-pool is not a real instance
        if instance_id == "autoscaling-pool":
            logging.info(
                "Instance is autoscaling-pool (shared pool), skipping ECS task checks"
            )
            return True

        logging.info(f"Waiting for ECS task to start on instance {instance_id}...")
        start_time = time.time()
        while time.time() - start_time < timeout:
            tasks = self.get_premium_ecs_tasks()
            for task in tasks:
                if task["ec2_instance_id"] == instance_id:
                    logging.info(
                        f"Found ECS task {task['task_id'][:12]}... on instance "
                        f"{instance_id} with status: {task['last_status']}"
                    )
                    if task["last_status"] == "RUNNING":
                        logging.info(
                            f"ECS task {task['task_id'][:12]}... is RUNNING "
                            f"on instance {instance_id}!"
                        )
                        return True
            logging.info(f"No running task found yet on {instance_id}")
            time.sleep(poll_interval)
        logging.error(
            f"Timeout waiting for ECS task on {instance_id} " f"(waited {timeout}s)"
        )
        return False


def main():
    parser = argparse.ArgumentParser(
        description="End-to-end test for premium instance provisioning, "
        "sharing, and migration."
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

    logging.info("=" * 60)
    logging.info("Premium User Lifecycle Test (Assign, Share, Scale, Migrate)")
    logging.info("=" * 60)

    # Initialize tester and load users
    tester = PremiumInstanceTester(args.terraform_dir, args.aws_region)
    if not load_test_users_for_db:
        logging.error("Function 'load_test_users_for_db' not available!")
        sys.exit(1)
    test_users = load_test_users_for_db()

    # --- STEP 0: CLEANUP ---
    logging.info("\nSTEP 0: Ensuring a clean environment...")
    tester.cleanup_and_reset_state(test_users, args.terraform_dir, args.skip_token_gen)
    initial_states = tester.get_instance_states()
    assert (
        len(initial_states.get("running", [])) == 0
    ), "Test must start with 0 running instances!"

    # --- Get Tokens for Both Users ---
    user1_email_keyword = "optinist_test_user_premium"
    user2_email_keyword = "optinist_test_user_premium_over"

    user1 = next(
        (
            u
            for u in test_users
            if user1_email_keyword in u.get("email", "")
            and "over" not in u.get("email", "")
        ),
        None,
    )
    user2 = next(
        (u for u in test_users if user2_email_keyword in u.get("email", "")), None
    )

    if not user1 or not user2:
        logging.error("Could not find both test users!")
        sys.exit(1)

    logging.info(
        f"Generating tokens for User 1 ({user1['email']}) "
        f"and User 2 ({user2['email']})..."
    )
    tokens_all = generate_jwt_tokens(
        environment="cloud", terraform_dir=args.terraform_dir, user_type="all"
    )
    user1_token = tokens_all.get("premium_token")
    user2_token = tokens_all.get("premium_over_token")

    if not user1_token or not user2_token:
        logging.error("Failed to generate tokens for both users!")
        sys.exit(1)

    # --- STEP 1: Assign User 1 (Dedicated Instance) ---
    logging.info(f"\nSTEP 1: Assigning User 1 ({user1['email']}) to a new instance...")
    assign_result1 = tester.assign_premium_user(user1_token)
    assert assign_result1 and "instance_id" in assign_result1, "Failed to assign User 1"
    instance_A_id = assign_result1["instance_id"]
    logging.info(f"User 1 assigned to instance {instance_A_id}. Verifying startup...")
    assert tester.wait_for_instance_running(
        instance_A_id
    ), f"Instance {instance_A_id} failed to start!"
    assert tester.wait_for_ecs_task(
        instance_A_id
    ), f"ECS task on {instance_A_id} failed to start!"
    logging.info("STEP 1 PASSED: User 1 is running on a dedicated instance.")

    # --- STEP 2: Assign User 2 (Shared or Autoscaling Pool) ---
    logging.info(f"\nSTEP 2: Assigning User 2 ({user2['email']})")
    assign_result2 = tester.assign_premium_user(user2_token)
    assert assign_result2 and "instance_id" in assign_result2, "Failed to assign User 2"
    instance_2_id = assign_result2.get("instance_id")

    # Due to timing, User 2 might be on autoscaling-pool or sharing a premium instance
    if instance_2_id == instance_A_id:
        logging.info(f"User 2 assigned to same instance as User 1: {instance_2_id}")
    elif instance_2_id == "autoscaling-pool":
        logging.info("User 2 assigned to autoscaling-pool (will be migrated)")
    else:
        logging.info(
            f"User 2 assigned to different instance: {instance_2_id} "
            f"(User 1 may have been migrated)"
        )

    logging.info("STEP 2 PASSED: User 2 successfully assigned.")

    # --- STEP 3: Verify Background Scaling ---
    logging.info(
        "\nSTEP 3: Verifying that premium instances are available or launching..."
    )

    # Get all real premium instances (exclude autoscaling-pool)
    # Wait longer for instances to provision - can take 6-8 minutes for:
    # Lambda detect → scale instances → boot → join ECS → start tasks
    premium_instances = set()
    for attempt in range(60):  # Wait up to 10 minutes
        all_instances = tester.get_instance_states()
        all_instance_ids = {i["id"] for state in all_instances.values() for i in state}
        # Filter out autoscaling-pool to get real premium instances
        premium_instances = {
            iid for iid in all_instance_ids if iid != "autoscaling-pool"
        }

        if attempt % 6 == 0:  # Log every minute
            logging.info(
                f"[{attempt*10}s] Found {len(premium_instances)} premium instances "
                f"(need 2): {premium_instances if premium_instances else 'none yet'}"
            )

        if len(premium_instances) >= 2:
            logging.info(
                f"Found {len(premium_instances)} premium instances "
                f"after {attempt*10}s: {premium_instances}"
            )
            break
        time.sleep(10)

    assert len(premium_instances) >= 2, (
        f"System did not provision enough premium instances! "
        f"Found {len(premium_instances)}: {premium_instances}"
    )
    logging.info("STEP 3 PASSED: Background scaling verified.")

    # --- STEP 4: Verify Both Users on Dedicated Premium Instances ---
    logging.info(
        "\nSTEP 4: Verifying both users are on separate dedicated premium instances..."
    )
    both_users_migrated = False
    for attempt in range(60):  # Wait up to 10 minutes for migration
        status_user1 = tester.get_user_assignment_status(user1_token)
        status_user2 = tester.get_user_assignment_status(user2_token)

        user1_instance = (
            (status_user1.get("assignment") or {}).get("instance_id")
            if status_user1
            else None
        )
        user2_instance = (
            (status_user2.get("assignment") or {}).get("instance_id")
            if status_user2
            else None
        )

        if attempt % 6 == 0:  # Log every minute
            logging.info(
                f"[{attempt*10}s] User 1: {user1_instance}, User 2: {user2_instance}"
            )

        # Check if both users are on premium instances (not autoscaling-pool)
        # and on different instances
        if (
            user1_instance
            and user2_instance
            and user1_instance != "autoscaling-pool"
            and user2_instance != "autoscaling-pool"
            and user1_instance != user2_instance
        ):
            logging.info(
                f"Both users successfully migrated after {attempt*10}s: "
                f"User 1 on {user1_instance}, User 2 on {user2_instance}"
            )
            both_users_migrated = True
            break

        time.sleep(10)

    assert both_users_migrated, (
        f"Users were not migrated to separate dedicated instances! "
        f"User 1: {user1_instance}, User 2: {user2_instance}"
    )

    # Store the instance IDs for verification in STEP 5
    instance_A_id = user1_instance
    instance_B_id = user2_instance

    logging.info("STEP 4 PASSED: Both users on separate dedicated premium instances.")

    # --- STEP 5: Final State Verification ---
    logging.info("\nSTEP 5: Verifying final system state...")
    final_states = tester.get_instance_states()
    running_instances = final_states.get("running", [])

    # Check we have at least 2 running instances (allow for standby/extra instances)
    assert (
        len(running_instances) >= 2
    ), f"Expected at least 2 running instances, but found {len(running_instances)}"

    logging.info(f"Found {len(running_instances)} running premium instance(s)")

    user1_final_status = tester.get_user_assignment_status(user1_token)
    user2_final_status = tester.get_user_assignment_status(user2_token)

    # Verify User 1 assignment
    assert user1_final_status is not None, "Failed to get User 1 status"
    user1_instance = user1_final_status.get("assignment", {}).get("instance_id")
    assert user1_instance == instance_A_id, (
        f"User 1 is not on the correct instance! "
        f"Expected {instance_A_id}, got {user1_instance}"
    )

    # Verify User 2 assignment
    assert user2_final_status is not None, "Failed to get User 2 status"
    user2_instance = user2_final_status.get("assignment", {}).get("instance_id")
    assert user2_instance == instance_B_id, (
        f"User 2 is not on the correct instance! "
        f"Expected {instance_B_id}, got {user2_instance}"
    )

    logging.info(
        "Final state check complete. "
        "All users are on their correct dedicated instances."
    )
    logging.info("STEP 5 PASSED: Final state verified.")

    # --- TEST SUCCESS ---
    logging.info("\n" + "=" * 60)
    logging.info("FULL PREMIUM LIFECYCLE TEST PASSED!")
    logging.info("=" * 60)


if __name__ == "__main__":
    main()
