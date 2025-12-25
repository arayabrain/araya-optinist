#!/usr/bin/env python3
"""
Free Tier Autoscaling Test - User Count Based Scaling

WHERE TO RUN:
** RUN FROM EC2 INSTANCE IN VPC ** (not local machine)
- Requires direct database access to private RDS instance
- OR run locally with SSH tunnel/VPN to RDS
- Alternative: Refactor to use Lambda proxies (like test_free_manager.py)

REQUIREMENTS:
- AWS credentials configured (boto3 access)
- IAM permissions: ecs:*, asg:*, cloudwatch:*, lambda:*, rds:*
- Terraform outputs available in terraform
- Python 3.8+ with boto3, pymysql, requests
- ** NETWORK ACCESS to private RDS database **
- Test users: optinist_test_user_free_1 through free_6

WHAT IT TESTS:
==============
Validates the Free Manager Lambda's user-count based autoscaling mechanism:
1. User activity tracking in free_user_assignments table
2. Lambda detects 6 active users (above threshold of 5)
3. Lambda scales ASG from 1 to 2 instances
4. Lambda waits for new instances to be ready
5. Lambda rebalances users across instances
6. Users migrate to new instances via cookie expiration
7. Final verification of even distribution

TEST FLOW:
==========
Phase 1: Setup (2 min)
  - Clean database and scaling locks
  - Scale down to 1 instance

Phase 2: Establish User Tracking (2 min)
  - Start heartbeats to get users tracked in database
  - Verify users appear in free_user_assignments table

Phase 3: Prepare for Scaling (6 min)
  - STOP heartbeats (users become idle)
  - Wait for ALB cookies to expire (5 min + buffer)
  - Users now have NO cookies and are idle

Phase 4: Trigger Lambda Scaling (14-20 min)
  - Manually invoke Lambda (for deterministic timing)
  - Lambda scales ASG, waits for instances, rebalances database
  - Poll for Lambda completion via scaling lock

Phase 5: Trigger User Migration (1 min)
  - Resume heartbeats to trigger user requests
  - Users get NEW cookies pointing to NEW instances (from database)
  - Users migrate to their assigned instances

Phase 6: Verification (1 min)
  - Verify ASG scaled to 2 instances
  - Verify users distributed evenly across instances

Total Duration: 26-32 minutes

KEY INSIGHTS:
=============
- ALB cookies expire after 5 min of INACTIVITY (each request resets timer)
- Lambda updates database immediately, but users don't migrate until cookies expire
- Heartbeats must be STOPPED before Lambda runs to allow cookies to expire
- After Lambda completes, heartbeats must RESUME to trigger migration
- Phase 5 only needs 1 minute (to trigger requests), not 6 minutes

EXPECTED RESULT:
================
- ASG scales from 1 to 2 instances
- All 6 users tracked in database
- Users distributed evenly (3 per instance)
- No errors in Lambda logs

HOW TO RUN:
  # From EC2 instance in VPC:
  python test_autoscaling_user_number.py

EXPECTED RUNTIME:
  26-32 minutes (includes 6 min cookie expiration + 14-20 min Lambda execution)

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
    lambda_timeout_minutes: int = 20  # Lambda timeout (typical: 14 min, max: 20 min)
    heartbeat_interval_seconds: int = 240  # Heartbeat every 4 minutes
    activity_threshold_minutes: int = (
        5  # Must match Lambda's FREE_IDLE_THRESHOLD_MINUTES
    )
    cookie_expiration_wait_seconds: int = (
        360  # Wait 6 minutes for ALB cookies to expire (5 min + buffer)
    )
    initial_tracking_wait_seconds: int = 120  # Wait for users to be tracked in database
    migration_trigger_wait_seconds: int = (
        60  # Wait for users to make requests after Lambda completes
    )

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
        self.lambda_client = boto3.client("lambda", region_name=config.aws_region)

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
                f"cd {Path(__file__).parent}/terraform && " "terraform output -json"
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

        terraform_dir = Path(__file__).parent / "terraform"

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
                    minutes=self.config.activity_threshold_minutes
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

        # First request to ensure user is in free_user_assignments table
        try:
            response = self.session.get(
                f"{self.config.api_base_url}/workspaces",
                headers=headers,
                timeout=10,
            )
            if response.status_code == 200:
                logging.info(f"[{user_email}] Initial request successful")
            else:
                logging.warning(
                    f"[{user_email}] Initial request failed: {response.status_code}"
                )
        except Exception as e:
            logging.warning(f"[{user_email}] Initial request error: {e}")

        while not stop_event.is_set():
            try:
                # Use /workspaces endpoint which requires auth and
                # triggers activity tracking
                response = self.session.get(
                    f"{self.config.api_base_url}/workspaces",
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

        for i, user_email in enumerate(self.tokens.keys()):
            stop_event = Event()
            self.heartbeat_stop_events[user_email] = stop_event

            thread = Thread(
                target=self.simulate_heartbeat,
                args=(user_email, stop_event),
                daemon=True,
            )
            thread.start()
            self.heartbeat_threads.append(thread)

            # Stagger thread starts by 2 seconds to avoid overwhelming the server
            if i < len(self.tokens) - 1:
                time.sleep(2)

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

    def wait_for_lambda_completion(self, timeout_minutes: int = 20) -> bool:
        """Wait for Lambda to complete by polling scaling lock."""
        import time

        start_time = time.time()
        max_wait = timeout_minutes * 60

        logging.info(f"Polling for Lambda completion (max {timeout_minutes} min)")

        while time.time() - start_time < max_wait:
            try:
                response = self.cloudwatch_client.get_metric_data(
                    MetricDataQueries=[
                        {
                            "Id": "scaling_lock",
                            "MetricStat": {
                                "Metric": {
                                    "Namespace": "OptiNiSt/FreeManager",
                                    "MetricName": "ScalingInProgress",
                                },
                                "Period": 60,
                                "Stat": "Maximum",
                            },
                            "ReturnData": True,
                        }
                    ],
                    StartTime=int((datetime.now() - timedelta(minutes=2)).timestamp()),
                    EndTime=int(datetime.now().timestamp()),
                )

                values = response["MetricDataResults"][0].get("Values", [])
                if not values or values[0] == 0:
                    logging.info("Lambda completed (scaling lock cleared)")
                    return True

                elapsed = int(time.time() - start_time)
                logging.info(f"Lambda still running... ({elapsed}s / {max_wait}s)")
                time.sleep(30)  # Check every 30 seconds

            except Exception as e:
                logging.warning(f"Error checking Lambda status: {e}")
                time.sleep(30)

        logging.error(f"Lambda did not complete within {timeout_minutes} minutes")
        return False

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

    def wait_for_ecs_instances(self, target_count: int, timeout_minutes: int = 13):
        """Wait for ECS instances to be registered and ready.

        Default timeout is 13 minutes to account for:
        - EC2 instance launch: ~5 minutes
        - ECS task startup: ~7 minutes
        - Buffer: ~1 minute
        """
        logging.info(
            f"Waiting for {target_count} ECS-registered instance(s) "
            f"(timeout: {timeout_minutes} min)"
        )
        start_time = time.time()

        while (time.time() - start_time) < (timeout_minutes * 60):
            # Check ECS container instances
            try:
                response = self.ecs_client.list_container_instances(
                    cluster=self.config.cluster_name, status="ACTIVE"
                )
                container_arns = response.get("containerInstanceArns", [])

                if container_arns:
                    # Describe to check agent connectivity
                    details = self.ecs_client.describe_container_instances(
                        cluster=self.config.cluster_name,
                        containerInstances=container_arns,
                    )
                    ready_count = sum(
                        1
                        for inst in details["containerInstances"]
                        if inst["status"] == "ACTIVE" and inst["agentConnected"]
                    )
                else:
                    ready_count = 0

                if ready_count >= target_count:
                    logging.info(f"ECS has {ready_count} ready instance(s)")
                    return True

                elapsed = int((time.time() - start_time) / 60)
                logging.info(
                    f"[{elapsed}/{timeout_minutes} min] "
                    f"Ready: {ready_count}, Target: {target_count}"
                )
            except Exception as e:
                logging.warning(f"Error checking ECS instances: {e}")

            time.sleep(30)

        logging.warning(
            f"ECS did not reach {target_count} ready instances within "
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

        logging.info(f"Force terminating {len(instance_ids)} instance(s)")
        for instance_id in instance_ids:
            try:
                logging.info(f"  Terminating {instance_id}")
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

        # Wait for scale-down to complete in ECS
        success = self.wait_for_ecs_instances(1, timeout_minutes=5)

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

    def invoke_free_manager_lambda(self) -> bool:
        """Invoke free manager Lambda asynchronously."""
        logging.info("\n" + "=" * 70)
        logging.info("INVOKING FREE MANAGER LAMBDA (ASYNC)")
        logging.info("=" * 70)

        try:
            payload = {
                "source": "aws.events",
                "detail-type": "Scheduled Event",
                "detail": {"action": "monitor"},
            }

            logging.info(f"Invoking Lambda: {self.config.lambda_function_name}")
            response = self.lambda_client.invoke(
                FunctionName=self.config.lambda_function_name,
                InvocationType="Event",  # Async invocation
                Payload=json.dumps(payload),
            )

            status_code = response["StatusCode"]
            logging.info(f"Lambda invocation status: {status_code}")

            if status_code == 202:  # Async invocation accepted
                logging.info("Lambda invocation accepted (async)")
                return True
            else:
                logging.error(f"Lambda invocation failed: {status_code}")
                return False

        except Exception as e:
            logging.error(f"Error invoking Lambda: {e}")
            return False

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
            logging.error(
                f"FAILED: Only {len(distribution)} instances have users, "
                f"expected {expected_instances}"
            )
            return False

        # Check if distribution is reasonably balanced
        user_counts = [len(users) for users in distribution.values()]
        if user_counts:
            max_users = max(user_counts)
            min_users = min(user_counts)
            imbalance = max_users - min_users

            logging.info(f"User count per instance: min={min_users}, max={max_users}")

            if imbalance > 2:
                logging.error(
                    f"FAILED: Imbalanced distribution: difference of {imbalance} users"
                )
                return False
            else:
                logging.info("Distribution is balanced")

        return True

    def cleanup_scaling_lock(self) -> bool:
        """Clear scaling lock before test."""
        logging.info("\n" + "=" * 70)
        logging.info("CLEARING SCALING LOCK")
        logging.info("=" * 70)

        try:
            self.cloudwatch_client = boto3.client(
                "cloudwatch", region_name=self.config.aws_region
            )
            self.cloudwatch_client.put_metric_data(
                Namespace="OptiNiSt/FreeManager",
                MetricData=[
                    {
                        "MetricName": "ScalingInProgress",
                        "Value": 0.0,
                        "Unit": "None",
                    }
                ],
            )
            logging.info("Cleared scaling lock")
            return True
        except Exception as e:
            logging.warning(f"Could not clear scaling lock: {e}")
            return False

    def cleanup_free_user_assignments(self) -> bool:
        """Clean up free_user_assignments table before test."""
        logging.info("\n" + "=" * 70)
        logging.info("CLEANING UP FREE USER ASSIGNMENTS")
        logging.info("=" * 70)

        try:
            conn = self.get_db_connection()
            with conn.cursor() as cursor:
                # Delete all test user assignments
                email_placeholders = ", ".join(["%s"] * len(self.config.test_users))
                query = f"""
                    DELETE fua FROM free_user_assignments fua
                    INNER JOIN users u ON fua.user_id = u.id
                    WHERE u.email IN ({email_placeholders})
                """
                cursor.execute(query, self.config.test_users)
                deleted_count = cursor.rowcount
                conn.commit()
            conn.close()

            logging.info(f"Deleted {deleted_count} stale assignment(s)")
            return True

        except Exception as e:
            logging.warning(f"Database cleanup failed (non-fatal): {e}")
            return False

    def run_test(self) -> bool:
        """Run the complete user-number autoscaling test."""
        logging.info("\n" + "=" * 80)
        logging.info("FREE TIER AUTOSCALING TEST - USER COUNT BASED SCALING")
        logging.info("=" * 80)
        logging.info(f"Test users: {len(self.config.test_users)}")
        logging.info(
            f"Expected scaling: {self.config.expected_instances_for_6_users} instances"
        )
        logging.info(f"API URL: {self.config.api_base_url}")
        logging.info("=" * 80)

        try:
            # Step 0: Clean up database and scaling lock
            self.cleanup_scaling_lock()
            self.cleanup_free_user_assignments()

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
                logging.info("Waiting 60 seconds for termination to complete")
                time.sleep(60)
            else:
                logging.info("Already at 1 instance, proceeding with test")

            # Step 4: Start heartbeats for all users
            self.start_heartbeats()

            # Step 4.5: Verify users are being tracked in database
            logging.info("\n" + "=" * 70)
            logging.info("VERIFYING USER TRACKING")
            logging.info("=" * 70)
            logging.info(
                f"Waiting {self.config.initial_tracking_wait_seconds}s "
                f"for users to be tracked (middleware caches for 60s)"
            )
            time.sleep(self.config.initial_tracking_wait_seconds)

            tracked_users = self.query_free_user_assignments()
            if not tracked_users:
                logging.warning("No users found in database after initial wait")
                logging.info("Waiting additional 60s for middleware cache to flush")
                time.sleep(60)
                tracked_users = self.query_free_user_assignments()

                if not tracked_users:
                    logging.error("No users found in database after heartbeats started")
                    logging.error("Check: 1) Backend is running on EC2 (not local)")
                    logging.error("       2) Users exist in 'users' table")
                    logging.error("       3) Middleware is enabled")
                    return False

            logging.info(f"Found {len(tracked_users)} tracked users:")
            for user in tracked_users:
                logging.info(
                    f"  {user['user_id']}: instance={user['instance_id']}, "
                    f"workflows={user['active_workflow_count']}, "
                    f"last_activity={user['last_activity']}"
                )

            if len(tracked_users) < len(self.config.test_users):
                logging.warning(
                    f"Only {len(tracked_users)}/{len(self.config.test_users)} "
                    f"users tracked, continuing anyway"
                )

            # Step 5: Stop heartbeats and wait for cookies to expire
            logging.info("\n" + "=" * 70)
            logging.info("PHASE 3: PREPARING FOR SCALING")
            logging.info("=" * 70)
            logging.info("Stopping heartbeats so users become idle")
            self.stop_heartbeats()

            logging.info(
                f"\nWaiting {self.config.cookie_expiration_wait_seconds}s "
                f"for ALB cookies to expire...\n"
                f"ALB cookies expire after 5 minutes of inactivity.\n"
                f"Users will have NO cookies after this wait."
            )
            time.sleep(self.config.cookie_expiration_wait_seconds)
            logging.info("Cookies expired. Users are idle with no active cookies.")

            # Step 6: Invoke Lambda asynchronously
            logging.info("\n" + "=" * 70)
            logging.info("PHASE 4: TRIGGERING LAMBDA SCALING")
            logging.info("=" * 70)
            logging.info(
                "Invoking Lambda manually for deterministic timing.\n"
                "Expected duration: 14 min (typical), 20 min (max)\n"
                "Lambda will:\n"
                "  1. Detect 6 active users in database\n"
                "  2. Scale ASG to 2 instances\n"
                "  3. Wait for instances to be ready\n"
                "  4. Update database with new user assignments\n"
                "  5. Clear scaling lock when complete"
            )

            if not self.invoke_free_manager_lambda():
                logging.error("Lambda invocation failed")
                return False

            # Step 7: Wait for Lambda to complete (poll for scaling lock to clear)
            logging.info("\n" + "=" * 70)
            logging.info("WAITING FOR LAMBDA TO COMPLETE")
            logging.info("=" * 70)
            if not self.wait_for_lambda_completion():
                logging.error("Lambda did not complete in time")
                return False

            # Step 8: Verify scaling occurred
            logging.info("\n" + "=" * 70)
            logging.info("VERIFYING SCALING RESULT")
            logging.info("=" * 70)
            if not self.verify_scaling(self.config.expected_instances_for_6_users):
                logging.error("Scaling verification failed")
                return False

            # Step 9: Trigger user migration
            logging.info("\n" + "=" * 70)
            logging.info("PHASE 5: TRIGGERING USER MIGRATION")
            logging.info("=" * 70)
            logging.info(
                "Lambda updated database with new assignments.\n"
                "Users still have NO cookies (expired in Phase 3).\n"
                "Resuming heartbeats to trigger user requests...\n"
                "Users will get NEW cookies pointing to NEW instances."
            )
            self.start_heartbeats()

            logging.info(
                f"Waiting {self.config.migration_trigger_wait_seconds}s "
                f"for users to make requests"
            )
            time.sleep(self.config.migration_trigger_wait_seconds)
            logging.info("Users have made requests and received new cookies.")

            # Step 10: Verify user distribution
            logging.info("\n" + "=" * 70)
            logging.info("PHASE 6: VERIFICATION")
            logging.info("=" * 70)
            if not self.verify_user_distribution(
                self.config.expected_instances_for_6_users
            ):
                logging.error("User distribution verification failed")
                return False

            # Step 11: Check Lambda logs for errors
            logging.info("\n" + "=" * 70)
            logging.info("CHECKING LAMBDA LOGS FOR ERRORS")
            logging.info("=" * 70)
            logs = self.get_lambda_logs(minutes=20)
            errors = [
                log
                for log in logs
                if "ERROR" in log or "Exception" in log or "Traceback" in log
            ]

            if errors:
                logging.warning(f"Found {len(errors)} error(s) in Lambda logs:")
                for error in errors[:5]:  # Show first 5 errors
                    logging.warning(f"  {error.strip()}")
            else:
                logging.info("No errors found in Lambda logs")

            logging.info("\n" + "=" * 80)
            logging.info("TEST COMPLETED SUCCESSFULLY")
            logging.info("=" * 80)
            logging.info("Total test duration: ~26-32 minutes")
            logging.info(
                f"ASG scaled: 1 → "
                f"{self.config.expected_instances_for_6_users} instances"
            )
            logging.info(
                f"Users distributed: {len(self.config.test_users)} "
                f"users across {self.config.expected_instances_for_6_users} instances"
            )
            return True

        except Exception as e:
            logging.error(f"\nTEST FAILED: {e}")
            import traceback

            traceback.print_exc()
            return False

        finally:
            # Always stop heartbeats and clear lock
            self.stop_heartbeats()
            self.cleanup_scaling_lock()


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
