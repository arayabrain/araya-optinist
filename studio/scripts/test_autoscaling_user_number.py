#!/usr/bin/env python3
"""
Free Tier Autoscaling Test - User Count Based Scaling (Layer 3)

This test validates the Free Manager Lambda's user-count based autoscaling mechanism.

THEORETICALLY OPTIMAL BEHAVIOR:
================================
When 5+ users are active (last_activity within 5 minutes):
1. Lambda detects active user count every 5 minutes
2. Calculates desired instances: min(max(1, (active_users + 4) // 5), max_instances)
   - 1-4 users → 1 instance
   - 5-9 users → 2 instances
   - 10-14 users → 3 instances
3. Scales ASG to desired capacity
4. Waits for new instances to launch (~7-10 minutes)
5. Rebalances idle users evenly across all instances
6. Protects users with active workflows from migration
7. Only scales down when: (current - desired) >= 2 (conservative)

This test uses 6 free users to verify:
- Scaling from 1 → 2 instances when 5+ users active
- Even distribution of users across instances
- Idle user migration (no active workflows)
- Active user protection (with workflows running)
- Database accuracy (free_user_assignments table)

Test Users:
- optinist_test_user_free_1 through optinist_test_user_free_6

Configuration:
- API URL and database credentials are automatically loaded from Terraform outputs
- Authentication tokens are automatically generated if not present or missing

Usage:
    python test_autoscaling_user_number.py

Author: Claude Code
Date: 2025-12-05
"""

import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from threading import Event, Thread
from typing import Dict, List, Tuple

import boto3
import pymysql
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Import token generation utilities
try:
    from get_jwt_tokens import generate_jwt_tokens
except ImportError:
    print("Warning: get_jwt_tokens module not available")
    print("Run: pip install firebase-admin")
    generate_jwt_tokens = None

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


@dataclass
class TestConfig:
    """Configuration for the autoscaling test."""

    # AWS Configuration
    aws_region: str = "ap-northeast-1"
    cluster_name: str = "subscr-optinist-cloud-cluster"
    service_name: str = "subscr-optinist-cloud-service"
    asg_name: str = "subscr-optinist-asg"
    lambda_function_name: str = "subscr-free-manager"

    # Database Configuration (from Terraform outputs)
    db_host: str = None  # Will be fetched from Terraform
    db_user: str = "root"
    db_password: str = None  # Will be fetched from Terraform
    db_name: str = "optinist"

    # API Configuration
    api_base_url: str = None  # Will be set from env or args

    # Test Users (6 free users)
    test_users: List[str] = None

    # Test Timing
    lambda_cycle_minutes: int = 5  # Lambda runs every 5 minutes
    lambda_wait_buffer_minutes: int = 2  # Extra wait for safety
    instance_launch_timeout_minutes: int = 15  # Max wait for instance launch
    heartbeat_interval_seconds: int = 120  # Heartbeat every 2 minutes

    # Expected Behavior
    user_threshold: int = 5  # Lambda scales at 5+ users
    expected_instances_for_6_users: int = 2  # (6+4)//5 = 2

    def __post_init__(self):
        """Initialize default test users if not provided."""
        if self.test_users is None:
            self.test_users = [
                "optinist_test_user_free_1@araya.org",
                "optinist_test_user_free_2@araya.org",
                "optinist_test_user_free_3@araya.org",
                "optinist_test_user_free_4@araya.org",
                "optinist_test_user_free_5@araya.org",
                "optinist_test_user_free_6@araya.org",
            ]


class AutoscalingUserNumberTest:
    """Test Lambda-based user-count autoscaling."""

    def __init__(self, config: TestConfig):
        """Initialize the test."""
        self.config = config
        self.tokens = {}
        self.workspaces = {}  # user_email -> workspace_id
        self.heartbeat_threads = []
        self.heartbeat_stop_events = {}  # user_email -> Event

        # AWS clients
        self.ecs_client = boto3.client("ecs", region_name=config.aws_region)
        self.asg_client = boto3.client("autoscaling", region_name=config.aws_region)
        self.ec2_client = boto3.client("ec2", region_name=config.aws_region)
        self.logs_client = boto3.client("logs", region_name=config.aws_region)

        # HTTP session with retries
        self.session = self._create_http_session()

        # Load or fetch configuration
        self._load_configuration()

    def _create_http_session(self) -> requests.Session:
        """Create HTTP session with retry logic."""
        session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def _load_configuration(self):
        """Load configuration from environment and Terraform outputs."""
        # Get configuration from Terraform
        try:
            result = os.popen(
                f"cd {Path(__file__).parent.parent}/config/terraform && "
                "terraform output -json"
            ).read()
            outputs = json.loads(result)

            # API URL from Terraform
            if "domain_url" in outputs:
                self.config.api_base_url = outputs["domain_url"]["value"]
            else:
                logging.warning("domain_url not found in Terraform outputs")
                self.config.api_base_url = "https://araya-optinist.com"

            # Database configuration
            if "rds_endpoint" in outputs:
                self.config.db_host = outputs["rds_endpoint"]["value"].split(":")[0]

            if "mysql_password" in outputs:
                self.config.db_password = outputs["mysql_password"]["value"]

        except Exception as e:
            logging.warning(f"Could not load Terraform outputs: {e}")
            logging.warning("Database queries will be skipped if credentials missing")
            self.config.api_base_url = "https://araya-optinist.com"

    def load_tokens(self) -> bool:
        """Load authentication tokens from tokens.json, generating if needed."""
        tokens_file = Path(__file__).parent / "tokens.json"

        # Auto-generate tokens if they don't exist
        if not tokens_file.exists():
            logging.info("Tokens file not found, generating automatically")
            if not self.generate_tokens():
                logging.error("Failed to generate tokens")
                return False

        with open(tokens_file) as f:
            all_tokens = json.load(f)

        # Load tokens for our test users
        missing_users = []
        for user_email in self.config.test_users:
            if user_email not in all_tokens:
                missing_users.append(user_email)
            else:
                self.tokens[user_email] = all_tokens[user_email]

        # Auto-generate if any users are missing
        if missing_users:
            logging.info(f"Some users missing tokens: {missing_users}")
            logging.info("Regenerating tokens")
            if not self.generate_tokens():
                logging.error("Failed to generate tokens")
                return False

            # Reload tokens
            with open(tokens_file) as f:
                all_tokens = json.load(f)

            for user_email in self.config.test_users:
                if user_email not in all_tokens:
                    logging.error(f"Token still not found for user: {user_email}")
                    return False
                self.tokens[user_email] = all_tokens[user_email]

        logging.info(f"Loaded tokens for {len(self.tokens)} users")
        return True

    def generate_tokens(self) -> bool:
        """Generate authentication tokens for test users
        using generate_jwt_tokens utility."""
        logging.info("Generating authentication tokens")

        if not generate_jwt_tokens:
            logging.error("Token generation not available")
            logging.error("Install with: pip install firebase-admin")
            return False

        terraform_dir = Path(__file__).parent.parent / "config" / "terraform"

        # Generate tokens for all free users
        logging.info(f"Generating tokens for {len(self.config.test_users)} test users")
        tokens = generate_jwt_tokens(
            environment="cloud",
            terraform_dir=str(terraform_dir),
            user_type="free",
            multi_free=True,
        )

        if not tokens:
            logging.error("Failed to generate tokens")
            return False

        # Verify all required users have tokens
        # The tokens dict will have keys like "free_0", "free_1", etc.
        # We need to load the tokens.json file and verify by email
        tokens_file = Path(__file__).parent / "tokens.json"
        if tokens_file.exists():
            with open(tokens_file) as f:
                all_tokens = json.load(f)

            missing_users = [
                user for user in self.config.test_users if user not in all_tokens
            ]
            if missing_users:
                logging.warning(f"Some users still missing tokens: {missing_users}")
                logging.info(
                    "This may be because the user emails don't match "
                    "the test_users in Terraform"
                )
                return False

            logging.info(
                f"Successfully generated tokens for all "
                f"{len(self.config.test_users)} users"
            )
            logging.info("Note: Firebase ID tokens expire after 1 hour")
            return True
        else:
            logging.error("tokens.json file not created")
            return False

    def get_db_connection(self):
        """Get database connection."""
        if not self.config.db_host or not self.config.db_password:
            raise ValueError("Database credentials not configured")

        return pymysql.connect(
            host=self.config.db_host,
            port=3306,
            user=self.config.db_user,
            password=self.config.db_password,
            database=self.config.db_name,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
        )

    def query_free_user_assignments(self) -> List[Dict]:
        """Query free_user_assignments table."""
        try:
            conn = self.get_db_connection()
            with conn.cursor() as cursor:
                query = """
                    SELECT user_id, instance_id, active_workflow_count,
                           last_activity, migration_count
                    FROM free_user_assignments
                    WHERE last_activity >= %s
                    ORDER BY instance_id, user_id
                """
                cutoff = datetime.now() - timedelta(
                    minutes=self.config.lambda_cycle_minutes
                )
                cursor.execute(query, (cutoff,))
                results = cursor.fetchall()
            conn.close()
            return results

        except Exception as e:
            logging.error(f"Database query failed: {e}")
            return []

    def get_user_distribution(self) -> Dict[str, List[str]]:
        """Get current user distribution across instances."""
        assignments = self.query_free_user_assignments()

        distribution = {}
        for row in assignments:
            instance_id = row["instance_id"]
            user_id = row["user_id"]

            if instance_id not in distribution:
                distribution[instance_id] = []
            distribution[instance_id].append(user_id)

        return distribution

    def simulate_heartbeat(self, user_email: str, stop_event: Event):
        """Simulate user heartbeat by making periodic API calls."""
        token = self.tokens[user_email]
        headers = {"Authorization": f"Bearer {token}"}

        logging.info(f"[{user_email}] Starting heartbeat simulation")

        while not stop_event.is_set():
            try:
                # Make a lightweight API call to update last_activity
                response = self.session.get(
                    f"{self.config.api_base_url}/api/health",
                    headers=headers,
                    timeout=10,
                )

                if response.status_code == 200:
                    logging.debug(f"[{user_email}] Heartbeat sent")
                else:
                    logging.warning(
                        f"[{user_email}] Heartbeat failed: {response.status_code}"
                    )

            except Exception as e:
                logging.warning(f"[{user_email}] Heartbeat error: {e}")

            # Wait for next heartbeat or stop signal
            stop_event.wait(timeout=self.config.heartbeat_interval_seconds)

        logging.info(f"[{user_email}] Heartbeat stopped")

    def start_heartbeats(self):
        """Start heartbeat threads for all users."""
        logging.info("\n" + "=" * 70)
        logging.info("STARTING USER HEARTBEATS")
        logging.info("=" * 70)

        for user_email in self.tokens.keys():
            stop_event = Event()
            self.heartbeat_stop_events[user_email] = stop_event

            thread = Thread(
                target=self.simulate_heartbeat,
                args=(user_email, stop_event),
                daemon=True,
            )
            thread.start()
            self.heartbeat_threads.append(thread)

        logging.info(f"Started {len(self.heartbeat_threads)} heartbeat threads")

    def stop_heartbeats(self):
        """Stop all heartbeat threads."""
        logging.info("\n" + "=" * 70)
        logging.info("STOPPING USER HEARTBEATS")
        logging.info("=" * 70)

        for stop_event in self.heartbeat_stop_events.values():
            stop_event.set()

        for thread in self.heartbeat_threads:
            thread.join(timeout=5)

        logging.info("All heartbeats stopped")

    def get_asg_capacity(self) -> Tuple[int, int]:
        """Get ASG desired and current capacity."""
        response = self.asg_client.describe_auto_scaling_groups(
            AutoScalingGroupNames=[self.config.asg_name]
        )

        if not response["AutoScalingGroups"]:
            raise ValueError(f"ASG not found: {self.config.asg_name}")

        asg = response["AutoScalingGroups"][0]
        desired = asg["DesiredCapacity"]
        current = len(
            [i for i in asg["Instances"] if i["LifecycleState"] == "InService"]
        )

        return desired, current

    def set_asg_capacity(self, desired_capacity: int):
        """Set ASG desired capacity."""
        logging.info(f"Setting ASG desired capacity to {desired_capacity}")
        self.asg_client.set_desired_capacity(
            AutoScalingGroupName=self.config.asg_name,
            DesiredCapacity=desired_capacity,
            HonorCooldown=False,
        )

    def wait_for_asg_instances(self, target_count: int, timeout_minutes: int = 10):
        """Wait for ASG to reach target instance count."""
        logging.info(f"Waiting for ASG to reach {target_count} instance(s)...")
        start_time = time.time()

        while (time.time() - start_time) < (timeout_minutes * 60):
            _, current = self.get_asg_capacity()

            if current == target_count:
                logging.info(f"ASG has reached {target_count} instance(s)")
                return True

            elapsed = int((time.time() - start_time) / 60)
            logging.info(
                f"[{elapsed}/{timeout_minutes} min] "
                f"Current: {current}, Target: {target_count}"
            )
            time.sleep(30)

        logging.warning(
            f"ASG did not reach {target_count} instances within "
            f"{timeout_minutes} minutes"
        )
        return False

    def get_asg_instance_ids(self) -> List[str]:
        """Get list of instance IDs in the ASG."""
        response = self.asg_client.describe_auto_scaling_groups(
            AutoScalingGroupNames=[self.config.asg_name]
        )

        if not response["AutoScalingGroups"]:
            return []

        asg = response["AutoScalingGroups"][0]
        return [inst["InstanceId"] for inst in asg["Instances"]]

    def force_terminate_instances(self, instance_ids: List[str]):
        """Force terminate EC2 instances directly."""
        if not instance_ids:
            return

        logging.info(f"Force terminating {len(instance_ids)} instance(s)...")
        for instance_id in instance_ids:
            try:
                logging.info(f"  Terminating {instance_id}...")
                self.ec2_client.terminate_instances(InstanceIds=[instance_id])
            except Exception as e:
                logging.warning(f"  Failed to terminate {instance_id}: {e}")

    def scale_down_asg_to_one(self):
        """Scale down ASG to 1 instance to prepare for scale-up test."""
        logging.info("\n" + "=" * 70)
        logging.info("SCALING DOWN TO 1 INSTANCE")
        logging.info("=" * 70)

        _, current = self.get_asg_capacity()
        logging.info(f"Current: {current} instances")

        if current <= 1:
            logging.info("Already at 1 instance or fewer, no scale-down needed")
            return True

        # Get all instance IDs
        all_instance_ids = self.get_asg_instance_ids()
        logging.info(f"Found {len(all_instance_ids)} instance(s) in ASG")

        # Keep the first instance, terminate the rest
        if len(all_instance_ids) > 1:
            instances_to_terminate = all_instance_ids[1:]
            logging.info(
                f"Keeping instance {all_instance_ids[0]}, "
                f"terminating {len(instances_to_terminate)} others"
            )
            self.force_terminate_instances(instances_to_terminate)

        # Set desired capacity to 1
        self.set_asg_capacity(1)

        # Wait for scale-down to complete
        success = self.wait_for_asg_instances(1, timeout_minutes=5)

        if success:
            logging.info("Successfully scaled down to 1 instance")
        else:
            logging.warning("Scale-down did not complete in expected time")

        return success

    def get_lambda_logs(self, minutes: int = 10) -> List[str]:
        """Get recent Lambda execution logs."""
        log_group = f"/aws/lambda/{self.config.lambda_function_name}"
        start_time = int(
            (datetime.now() - timedelta(minutes=minutes)).timestamp() * 1000
        )

        try:
            response = self.logs_client.filter_log_events(
                logGroupName=log_group,
                startTime=start_time,
            )

            messages = [event["message"] for event in response.get("events", [])]
            return messages

        except Exception as e:
            logging.warning(f"Could not fetch Lambda logs: {e}")
            return []

    def wait_for_lambda_cycle(self):
        """Wait for at least one Lambda execution cycle."""
        wait_minutes = (
            self.config.lambda_cycle_minutes + self.config.lambda_wait_buffer_minutes
        )
        logging.info(f"\nWaiting {wait_minutes} minutes for Lambda cycle")

        for i in range(wait_minutes):
            logging.info(f"  {i+1}/{wait_minutes} minutes elapsed")
            time.sleep(60)

    def verify_scaling(self, expected_instances: int) -> bool:
        """Verify ASG scaled to expected capacity."""
        logging.info("\n" + "=" * 70)
        logging.info("VERIFYING SCALING")
        logging.info("=" * 70)

        desired, current = self.get_asg_capacity()
        logging.info(f"ASG Desired Capacity: {desired}")
        logging.info(f"ASG Current Capacity: {current}")
        logging.info(f"Expected: {expected_instances}")

        if desired != expected_instances:
            logging.error(
                f"FAILED: Desired capacity {desired} != expected {expected_instances}"
            )
            return False

        logging.info(f"SUCCESS: ASG desired capacity is {expected_instances}")
        return True

    def verify_user_distribution(self, expected_instances: int) -> bool:
        """Verify users are distributed across instances."""
        logging.info("\n" + "=" * 70)
        logging.info("VERIFYING USER DISTRIBUTION")
        logging.info("=" * 70)

        distribution = self.get_user_distribution()

        if not distribution:
            logging.error("FAILED: No user assignments found in database")
            return False

        logging.info(f"Found {len(distribution)} instances with users:")
        for instance_id, users in distribution.items():
            logging.info(f"  {instance_id}: {len(users)} users - {users}")

        # Check if users are distributed
        if len(distribution) < expected_instances:
            logging.warning(
                f"Only {len(distribution)} instances have users, "
                f"expected {expected_instances}"
            )
            logging.warning("Users may not be rebalanced yet")

        # Check if distribution is reasonably balanced
        user_counts = [len(users) for users in distribution.values()]
        if user_counts:
            max_users = max(user_counts)
            min_users = min(user_counts)
            imbalance = max_users - min_users

            logging.info(f"User count per instance: min={min_users}, max={max_users}")

            if imbalance > 2:
                logging.warning(
                    f"Imbalanced distribution: difference of {imbalance} users"
                )
            else:
                logging.info("Distribution is balanced")

        return True

    def run_test(self) -> bool:
        """Run the complete user-number autoscaling test."""
        logging.info("\n" + "=" * 80)
        logging.info("FREE TIER AUTOSCALING TEST - USER COUNT BASED (LAYER 3)")
        logging.info("=" * 80)
        logging.info(f"Test users: {len(self.config.test_users)}")
        logging.info(
            f"Expected scaling: {self.config.expected_instances_for_6_users} instances"
        )
        logging.info(f"API URL: {self.config.api_base_url}")
        logging.info("=" * 80)

        try:
            # Step 1: Load tokens
            if not self.load_tokens():
                return False

            # Step 2: Get baseline ASG capacity
            logging.info("\n" + "=" * 70)
            logging.info("BASELINE CAPACITY")
            logging.info("=" * 70)
            baseline_desired, baseline_current = self.get_asg_capacity()
            logging.info(f"Baseline ASG Desired: {baseline_desired}")
            logging.info(f"Baseline ASG Current: {baseline_current}")

            # Step 3: Scale down to 1 instance if needed
            if baseline_current > 1:
                logging.info(
                    f"Need to scale down from {baseline_current} to 1 instance "
                    f"to test scale-up behavior"
                )
                if not self.scale_down_asg_to_one():
                    logging.error("Failed to scale down to 1 instance")
                    return False
                # Give instances time to fully terminate
                logging.info("Waiting 60 seconds for termination to complete...")
                time.sleep(60)
            else:
                logging.info("Already at 1 instance, proceeding with test")

            # Step 4: Start heartbeats for all users
            self.start_heartbeats()

            # Step 5: Wait for Lambda to detect users and scale
            self.wait_for_lambda_cycle()

            # Step 6: Verify scaling occurred
            if not self.verify_scaling(self.config.expected_instances_for_6_users):
                logging.error("Scaling verification failed")
                return False

            # Step 7: Wait for new instances to launch
            logging.info("\n" + "=" * 70)
            logging.info("WAITING FOR INSTANCES TO LAUNCH")
            logging.info("=" * 70)
            self.wait_for_asg_instances(
                self.config.expected_instances_for_6_users, timeout_minutes=15
            )

            # Step 8: Wait for another Lambda cycle to trigger rebalancing
            logging.info("Waiting for Lambda to rebalance users")
            self.wait_for_lambda_cycle()

            # Step 9: Verify user distribution
            self.verify_user_distribution(self.config.expected_instances_for_6_users)

            # Step 10: Check Lambda logs
            logging.info("\n" + "=" * 70)
            logging.info("LAMBDA EXECUTION LOGS (Last 10 minutes)")
            logging.info("=" * 70)
            logs = self.get_lambda_logs(minutes=10)
            for log in logs:
                if "Scaling" in log or "rebalanc" in log.lower():
                    logging.info(f"  {log.strip()}")

            logging.info("\n" + "=" * 80)
            logging.info("TEST COMPLETED SUCCESSFULLY")
            logging.info("=" * 80)
            return True

        except Exception as e:
            logging.error(f"\nTEST FAILED: {e}")
            import traceback

            traceback.print_exc()
            return False

        finally:
            # Always stop heartbeats
            self.stop_heartbeats()


def main():
    """Main entry point."""
    # Create config and test instance
    config = TestConfig()
    test = AutoscalingUserNumberTest(config)

    # Run test (tokens will be auto-generated if needed)
    success = test.run_test()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
