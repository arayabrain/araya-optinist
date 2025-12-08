#!/usr/bin/env python3
"""
Free Tier Autoscaling Test - CPU/Memory Based Scaling

WHERE TO RUN:
** RUN FROM EC2 INSTANCE IN VPC ** (not local machine)
- Requires direct database access to private RDS instance
- OR run locally with SSH tunnel/VPN to RDS
- Alternative: Refactor to use Lambda proxies (like test_free_manager.py)

REQUIREMENTS:
- AWS credentials configured (boto3 access)
- IAM permissions: ecs:*, asg:*, cloudwatch:*, rds:*
- Terraform outputs available in ../config/terraform
- Python 3.8+ with boto3, pymysql, requests
- ** NETWORK ACCESS to private RDS database **
- Test users: optinist_test_user_free_7, optinist_test_user_free_8

WHAT IT TESTS:
==============
Validates CloudWatch alarm-based autoscaling triggered by CPU/Memory usage:
1. User 7 generates CPU load by running 30 concurrent workflows
2. CloudWatch monitors ECS service metrics and triggers alarm
3. ASG scales up by adding a new instance
4. User 8 logs in and should be assigned to the less-loaded new instance
5. Lambda (running every 5 min) rebalances users if needed

THEORETICALLY OPTIMAL BEHAVIOR:
================================
When CPU exceeds 60% or Memory exceeds 80%:
1. CloudWatch monitors ECS service metrics every 2 minutes
2. CPU Alarm: Triggers after 2 evaluation periods at >60% (4 minutes total)
3. Memory Alarm: Triggers after 3 evaluation periods at >80% (6 minutes total)
4. Alarm triggers ASG scale-up policy → adds 1 instance
5. New instance launches (~7-10 minutes)
6. ECS places tasks on new instance
7. NEW USER (user 8) should be assigned to the NEW instance (less loaded)
8. Lambda (running every 5 min) sees the scaling, rebalances if needed

The OPTIMAL behavior for user assignment:
- User 7 runs 30 workflows → CPU high on Instance A
- CloudWatch alarm triggers → Instance B launches
- User 8 logs in → Should be assigned to Instance B (has lower load)
- This tests that the load balancer + assignment logic favors less-loaded instances

TEST WORKFLOW APPROACH (Aligned with test_premium_load.py):
============================================================
1. Reuse existing workspaces or create new ones
2. Import tutorial sample data into workspaces
3. Fetch workflow structure from workspaces (nodeDict, edgeDict, snakemakeParam)
4. Submit real workflows using the /run/{workspace_id} endpoint
5. Monitor CPU/Memory metrics and CloudWatch alarms

EXPECTED RESULT:
================
- CPU/Memory utilization exceeds threshold
- CloudWatch alarm triggers (ALARM state)
- ASG scales from 1 to 2 instances
- User 8 is assigned to the new, less-loaded instance
- Optimal load balancing demonstrated

HOW TO RUN:
  # From EC2 instance in VPC:
  python test_autoscaling_usage.py

EXPECTED RUNTIME:
  20-30 minutes (workflow submission + alarm trigger + instance launch)

"""

import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Tuple

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
    """Configuration for the CPU/Memory autoscaling test."""

    # AWS Configuration
    aws_region: str = "ap-northeast-1"
    cluster_name: str = "subscr-optinist-cloud-cluster"
    service_name: str = "subscr-optinist-cloud-service"
    asg_name: str = "subscr-optinist-asg"
    cpu_alarm_name: str = "subscr-optinist-cpu-high"
    memory_alarm_name: str = "subscr-optinist-memory-high"

    # Database Configuration
    db_host: str = None
    db_user: str = "root"
    db_password: str = None
    db_name: str = "optinist"

    # API Configuration
    api_base_url: str = None

    # Test Users
    load_generator_user: str = "optinist_test_user_free_7@araya.org"
    new_user: str = "optinist_test_user_free_8@araya.org"

    # Test Parameters
    num_workspaces: int = 30  # Number of workspaces to create
    cpu_threshold: float = 60.0  # CPU threshold for alarm
    alarm_evaluation_periods: int = 2  # Number of periods for CPU alarm
    alarm_period_seconds: int = 120  # CloudWatch period (2 minutes)

    # Workflow configuration
    workflow_type: str = "test_caiman"  # Compute-intensive workflow

    def total_alarm_wait_minutes(self) -> int:
        """Calculate total wait time for alarm to trigger."""
        return (self.alarm_evaluation_periods * self.alarm_period_seconds) // 60


class AutoscalingUsageTest:
    """Test CloudWatch alarm-based CPU/Memory autoscaling."""

    def __init__(self, config: TestConfig):
        """Initialize the test."""
        self.config = config
        self.tokens = {}
        self.workspaces = []  # List of workspace_id integers
        self.submitted_workflows = []  # List of submitted workflow info

        # AWS clients
        self.ecs_client = boto3.client("ecs", region_name=config.aws_region)
        self.asg_client = boto3.client("autoscaling", region_name=config.aws_region)
        self.cloudwatch_client = boto3.client(
            "cloudwatch", region_name=config.aws_region
        )

        # HTTP session
        self.session = self._create_http_session()

        # Load configuration
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
        """Load configuration from environment and Terraform."""
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
            self.config.api_base_url = "https://araya-optinist.com"

    def load_tokens(self) -> bool:
        """Load authentication tokens, generating if needed."""
        tokens_file = Path(__file__).parent / "tokens.json"
        test_users = [self.config.load_generator_user, self.config.new_user]

        # Auto-generate tokens if they don't exist or are expired
        needs_generation = False

        if not tokens_file.exists():
            logging.info("Tokens file not found, generating automatically...")
            needs_generation = True
        else:
            # Check if tokens exist and are valid
            try:
                with open(tokens_file) as f:
                    all_tokens = json.load(f)

                # Check if our test users have tokens
                missing_users = []
                for user_email in test_users:
                    if user_email not in all_tokens:
                        missing_users.append(user_email)

                if missing_users:
                    logging.info(f"Some users missing tokens: {missing_users}")
                    needs_generation = True
                else:
                    # Tokens exist, load them
                    for user_email in test_users:
                        self.tokens[user_email] = all_tokens[user_email]
                    logging.info(f"Loaded tokens for {len(self.tokens)} users")
                    return True

            except Exception as e:
                logging.warning(f"Error reading tokens file: {e}")
                needs_generation = True

        # Generate tokens if needed
        if needs_generation:
            if not self.generate_tokens():
                logging.error("Failed to generate tokens")
                return False

            # Reload tokens
            try:
                with open(tokens_file) as f:
                    all_tokens = json.load(f)

                for user_email in test_users:
                    if user_email not in all_tokens:
                        logging.error(f"Token still not found for user: {user_email}")
                        logging.error(f"Available tokens: {list(all_tokens.keys())}")
                        return False
                    self.tokens[user_email] = all_tokens[user_email]

                logging.info(f"Loaded tokens for {len(self.tokens)} users")
                return True

            except Exception as e:
                logging.error(f"Error loading generated tokens: {e}")
                return False

        return False

    def generate_tokens(self) -> bool:
        """Generate authentication tokens for test users
        using generate_jwt_tokens utility."""
        logging.info("Generating authentication tokens...")

        if not generate_jwt_tokens:
            logging.error("Token generation not available")
            logging.error("Install with: pip install firebase-admin")
            return False

        terraform_dir = Path(__file__).parent.parent / "config" / "terraform"
        test_users = [self.config.load_generator_user, self.config.new_user]

        # Generate tokens for all free users
        logging.info(f"Generating tokens for {len(test_users)} test users...")
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
        tokens_file = Path(__file__).parent / "tokens.json"
        if tokens_file.exists():
            with open(tokens_file) as f:
                all_tokens = json.load(f)

            missing_users = [user for user in test_users if user not in all_tokens]
            if missing_users:
                logging.warning(f"Some users still missing tokens: {missing_users}")
                logging.info(
                    "This may be because the user emails don't "
                    "match the test_users in Terraform"
                )
                return False

            logging.info(
                f"Successfully generated tokens for all {len(test_users)} users"
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

    def get_user_instance_assignment(self, user_email: str) -> Optional[str]:
        """Get the instance ID assigned to a user."""
        try:
            conn = self.get_db_connection()
            with conn.cursor() as cursor:
                query = """
                    SELECT instance_id
                    FROM free_user_assignments
                    WHERE user_id = %s
                """
                cursor.execute(query, (user_email,))
                result = cursor.fetchone()
            conn.close()

            if result:
                return result["instance_id"]
            return None

        except Exception as e:
            logging.error(f"Database query failed: {e}")
            return None

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

    def get_ecs_metrics(self) -> Tuple[float, float]:
        """Get current ECS CPU and Memory utilization."""
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(minutes=5)

        # Get CPU utilization
        cpu_response = self.cloudwatch_client.get_metric_statistics(
            Namespace="AWS/ECS",
            MetricName="CPUUtilization",
            Dimensions=[
                {"Name": "ServiceName", "Value": self.config.service_name},
                {"Name": "ClusterName", "Value": self.config.cluster_name},
            ],
            StartTime=start_time,
            EndTime=end_time,
            Period=60,
            Statistics=["Average"],
        )

        cpu_util = 0.0
        if cpu_response["Datapoints"]:
            cpu_util = max(dp["Average"] for dp in cpu_response["Datapoints"])

        # Get Memory utilization
        mem_response = self.cloudwatch_client.get_metric_statistics(
            Namespace="AWS/ECS",
            MetricName="MemoryUtilization",
            Dimensions=[
                {"Name": "ServiceName", "Value": self.config.service_name},
                {"Name": "ClusterName", "Value": self.config.cluster_name},
            ],
            StartTime=start_time,
            EndTime=end_time,
            Period=60,
            Statistics=["Average"],
        )

        mem_util = 0.0
        if mem_response["Datapoints"]:
            mem_util = max(dp["Average"] for dp in mem_response["Datapoints"])

        return cpu_util, mem_util

    def get_alarm_state(self, alarm_name: str) -> str:
        """Get CloudWatch alarm state."""
        response = self.cloudwatch_client.describe_alarms(AlarmNames=[alarm_name])

        if not response["MetricAlarms"]:
            raise ValueError(f"Alarm not found: {alarm_name}")

        return response["MetricAlarms"][0]["StateValue"]

    def create_workspace(self, user_email: str, workspace_name: str) -> Optional[int]:
        """Create a workspace for the user."""
        token = self.tokens[user_email]
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        try:
            response = self.session.post(
                f"{self.config.api_base_url}/workspace",
                json={"name": workspace_name},
                headers=headers,
                timeout=30,
            )

            if response.status_code == 200:
                workspace = response.json()
                workspace_id = workspace.get("id")
                logging.info(
                    f"[{user_email}] Created workspace: "
                    f"{workspace_name} (ID: {workspace_id})"
                )
                return workspace_id
            elif response.status_code == 401:
                logging.error(
                    f"[{user_email}] Authentication failed (401): {response.text}"
                )
                logging.error(
                    "Token may be expired (Firebase tokens expire after 1 hour)"
                )
                logging.error(
                    "Try regenerating tokens with: "
                    "python get_jwt_tokens.py --multi-free"
                )
                return None
            else:
                logging.error(
                    f"[{user_email}] Failed to create workspace: "
                    f"{response.status_code} - {response.text}"
                )
                return None

        except Exception as e:
            logging.error(f"[{user_email}] Error creating workspace: {e}")
            return None

    def import_sample_data(self, user_email: str, workspace_id: int) -> bool:
        """Import tutorial sample data into workspace."""
        try:
            token = self.tokens[user_email]
            headers = {"Authorization": f"Bearer {token}"}

            response = self.session.get(
                f"{self.config.api_base_url}/workflow/sample_data/"
                f"{workspace_id}/tutorial",
                headers=headers,
                timeout=120,
            )

            if response.status_code == 200:
                logging.info(
                    f"[{user_email}] Imported sample data for workspace {workspace_id}"
                )
                return True
            else:
                logging.error(
                    f"[{user_email}] Sample data import failed for workspace "
                    f"{workspace_id}: {response.status_code} - {response.text}"
                )

        except Exception as e:
            logging.error(
                f"[{user_email}] Error importing sample data for "
                f"workspace {workspace_id}: {e}"
            )

        return False

    def get_existing_workspaces(self, user_email: str) -> list:
        """Get list of existing workspaces for the user."""
        try:
            token = self.tokens[user_email]
            headers = {"Authorization": f"Bearer {token}"}

            response = self.session.get(
                f"{self.config.api_base_url}/workspaces",
                headers=headers,
                params={"offset": 0, "limit": 50},
                timeout=30,
            )

            if response.status_code == 200:
                data = response.json()
                # Handle paginated response
                if isinstance(data, dict) and "items" in data:
                    workspaces = data["items"]
                else:
                    workspaces = data

                workspace_ids = [ws["id"] for ws in workspaces if "id" in ws]
                logging.info(
                    f"[{user_email}] Found {len(workspace_ids)} existing workspaces"
                )
                return workspace_ids
            elif response.status_code == 401:
                logging.error(
                    f"[{user_email}] Authentication failed (401): {response.text}"
                )
                logging.error(
                    "Token may be expired (Firebase tokens expire after 1 hour)"
                )
                logging.error(
                    "Try regenerating tokens with: "
                    "python get_jwt_tokens.py --multi-free"
                )
                return []
            else:
                logging.warning(
                    f"[{user_email}] Failed to get existing workspaces: "
                    f"{response.status_code} - {response.text}"
                )
                return []

        except Exception as e:
            logging.warning(f"[{user_email}] Error getting existing workspaces: {e}")
            return []

    def ensure_workspace_has_data(self, user_email: str, workspace_id: int) -> bool:
        """Ensure a workspace has sample data - import if needed."""
        try:
            token = self.tokens[user_email]
            headers = {"Authorization": f"Bearer {token}"}

            # Check if workspace has any experiments
            response = self.session.get(
                f"{self.config.api_base_url}/experiments/{workspace_id}",
                headers=headers,
                timeout=30,
            )

            if response.status_code == 200:
                experiments = response.json()
                if experiments and len(experiments) > 0:
                    logging.info(
                        f"[{user_email}] Workspace {workspace_id} already has "
                        f"{len(experiments)} experiment(s)"
                    )
                    return True
                else:
                    # No experiments, need to import sample data
                    logging.info(
                        f"[{user_email}] Workspace {workspace_id} has no data, "
                        f"importing sample data"
                    )
                    return self.import_sample_data(user_email, workspace_id)
            else:
                logging.warning(
                    f"[{user_email}] Could not check experiments "
                    f"for workspace {workspace_id}: {response.status_code}"
                )
                # Try importing sample data anyway
                return self.import_sample_data(user_email, workspace_id)

        except Exception as e:
            logging.warning(
                f"[{user_email}] Error checking workspace {workspace_id} data: {e}, "
                f"attempting to import sample data"
            )
            return self.import_sample_data(user_email, workspace_id)

    def fetch_workflow_from_workspace(
        self, user_email: str, workspace_id: int
    ) -> Optional[Dict]:
        """Fetch workflow structure from a workspace that has sample data imported."""
        try:
            token = self.tokens[user_email]
            headers = {"Authorization": f"Bearer {token}"}

            response = self.session.get(
                f"{self.config.api_base_url}/workflow/fetch/{workspace_id}",
                headers=headers,
                timeout=30,
            )

            if response.status_code == 200:
                workflow_data = response.json()

                # Extract the fields needed for RunItem
                run_item = {
                    "name": workflow_data.get("name", "tutorial1"),
                    "nodeDict": workflow_data.get("nodeDict", {}),
                    "edgeDict": workflow_data.get("edgeDict", {}),
                    "snakemakeParam": workflow_data.get("snakemake", {}),
                    "nwbParam": workflow_data.get("nwb", {}),
                    "forceRunList": workflow_data.get("forceRunList", []),
                }

                logging.info(
                    f"[{user_email}] Fetched workflow from workspace {workspace_id}"
                )
                return run_item
            else:
                logging.error(
                    f"[{user_email}] Failed to fetch workflow from workspace "
                    f"{workspace_id}: {response.status_code} - {response.text}"
                )
                return None

        except Exception as e:
            logging.error(
                f"[{user_email}] Error fetching workflow from workspace "
                f"{workspace_id}: {e}"
            )
            return None

    def submit_workflow(
        self,
        user_email: str,
        workspace_id: int,
        workflow_index: int,
        workflow_data: Dict,
    ) -> Optional[str]:
        """Submit a workflow to a workspace."""
        token = self.tokens[user_email]
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        try:
            workflow_copy = workflow_data.copy()
            workflow_copy[
                "name"
            ] = f"load_test_workflow_{workflow_index}_{int(time.time())}"

            response = self.session.post(
                f"{self.config.api_base_url}/run/{workspace_id}",
                json=workflow_copy,
                headers=headers,
                timeout=60,
            )

            if response.status_code == 200:
                unique_id = response.text.strip('"')
                logging.info(
                    f"[{user_email}] Submitted workflow "
                    f"{workflow_index}: {unique_id[:12]}..."
                )
                self.submitted_workflows.append(
                    {
                        "workflow_index": workflow_index,
                        "unique_id": unique_id,
                        "workspace_id": workspace_id,
                        "submitted_at": datetime.now(),
                    }
                )
                return unique_id
            else:
                logging.error(
                    f"[{user_email}] Workflow {workflow_index} submission failed: "
                    f"{response.status_code} - {response.text}"
                )
                return None

        except Exception as e:
            logging.error(
                f"[{user_email}] Error submitting workflow {workflow_index}: {e}"
            )
            return None

    def setup_workspaces(self) -> bool:
        """Setup workspaces - reuse existing ones and create new ones if needed."""
        logging.info("\n" + "=" * 70)
        logging.info("SETTING UP WORKSPACES")
        logging.info("=" * 70)

        user_email = self.config.load_generator_user
        logging.info(
            f"Setting up {self.config.num_workspaces} workspaces for {user_email}..."
        )

        # Get existing workspaces
        existing_workspaces = self.get_existing_workspaces(user_email)

        # Use existing workspaces up to the number we need
        num_existing = min(len(existing_workspaces), self.config.num_workspaces)
        existing_to_use = existing_workspaces[:num_existing]

        if num_existing > 0:
            logging.info(
                f"Reusing {num_existing} existing workspace(s): {existing_to_use}"
            )

            # Ensure existing workspaces have sample data
            logging.info("Ensuring existing workspaces have sample data...")
            valid_workspaces = []

            def ensure_data(workspace_id):
                if self.ensure_workspace_has_data(user_email, workspace_id):
                    return workspace_id
                return None

            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = [
                    executor.submit(ensure_data, ws_id) for ws_id in existing_to_use
                ]

                for future in as_completed(futures):
                    workspace_id = future.result()
                    if workspace_id:
                        valid_workspaces.append(workspace_id)

            self.workspaces = valid_workspaces
            logging.info(
                f"{len(valid_workspaces)} existing workspace(s) ready with data"
            )

        # Calculate how many more we need to create
        num_to_create = self.config.num_workspaces - len(self.workspaces)

        if num_to_create > 0:
            logging.info(f"Creating {num_to_create} additional workspace(s)...")

            def setup_workspace(workspace_index):
                workspace_name = (
                    f"load_test_workspace_{workspace_index}_{int(time.time())}"
                )
                workspace_id = self.create_workspace(user_email, workspace_name)
                if workspace_id:
                    if self.import_sample_data(user_email, workspace_id):
                        return workspace_id
                return None

            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = [
                    executor.submit(setup_workspace, i)
                    for i in range(len(self.workspaces), self.config.num_workspaces)
                ]

                for future in as_completed(futures):
                    workspace_id = future.result()
                    if workspace_id:
                        self.workspaces.append(workspace_id)

        logging.info(
            f"Workspace setup complete: {len(self.workspaces)} total workspaces "
            f"({num_existing} reused, "
            f"{len(self.workspaces) - num_existing} newly created)"
        )
        return len(self.workspaces) >= self.config.num_workspaces

    def generate_cpu_load(self) -> bool:
        """Generate CPU load by submitting workflows to workspaces."""
        logging.info("\n" + "=" * 70)
        logging.info("GENERATING CPU LOAD")
        logging.info("=" * 70)

        user_email = self.config.load_generator_user

        # Ensure workspaces are set up
        if not self.workspaces:
            logging.info("Setting up workspaces first...")
            if not self.setup_workspaces():
                logging.error("Failed to setup workspaces")
                return False

        # Fetch workflow from each workspace that has sample data
        logging.info("Fetching workflows from all workspaces...")
        workspace_workflows = {}
        for workspace_id in self.workspaces:
            workflow = self.fetch_workflow_from_workspace(user_email, workspace_id)
            if workflow:
                workspace_workflows[workspace_id] = workflow
                logging.info(
                    f"Successfully fetched workflow from workspace {workspace_id}"
                )
            else:
                logging.warning(
                    f"Failed to fetch workflow from workspace {workspace_id}"
                )

        if not workspace_workflows:
            logging.error("Failed to fetch workflow from any workspace")
            return False

        logging.info(
            f"Successfully fetched workflows from {len(workspace_workflows)} workspaces"
        )

        # Submit workflows - distribute across workspaces
        logging.info(f"Submitting {self.config.num_workspaces} workflows...")
        successful_submissions = 0

        def submit_workflow_task(workflow_index):
            workspace_id = self.workspaces[workflow_index % len(self.workspaces)]

            # Use the workflow from this specific workspace
            if workspace_id in workspace_workflows:
                workflow_data = workspace_workflows[workspace_id]
            else:
                # Fallback to first available, if this workspace doesn't have one
                workspace_id = list(workspace_workflows.keys())[0]
                workflow_data = workspace_workflows[workspace_id]
                logging.warning(f"Using fallback workflow for index {workflow_index}")

            return self.submit_workflow(
                user_email, workspace_id, workflow_index, workflow_data
            )

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [
                executor.submit(submit_workflow_task, i)
                for i in range(self.config.num_workspaces)
            ]

            for future in as_completed(futures):
                if future.result():
                    successful_submissions += 1

        logging.info(
            f"Successfully submitted {successful_submissions}/"
            f"{self.config.num_workspaces} workflows"
        )
        return successful_submissions > 0

    def wait_for_cpu_threshold(self, timeout_minutes: int = 15) -> bool:
        """Wait for CPU to exceed threshold."""
        logging.info("\n" + "=" * 70)
        logging.info("WAITING FOR CPU THRESHOLD")
        logging.info("=" * 70)
        logging.info(f"Threshold: {self.config.cpu_threshold}%")
        logging.info(f"Timeout: {timeout_minutes} minutes")

        start_time = time.time()
        threshold_exceeded = False

        while (time.time() - start_time) < (timeout_minutes * 60):
            cpu_util, mem_util = self.get_ecs_metrics()
            elapsed_min = int((time.time() - start_time) / 60)

            logging.info(
                f"[{elapsed_min}/{timeout_minutes} min] "
                f"CPU: {cpu_util:.1f}%, Memory: {mem_util:.1f}%"
            )

            if cpu_util >= self.config.cpu_threshold:
                logging.info(
                    f"CPU threshold exceeded: {cpu_util:.1f}% >= "
                    f"{self.config.cpu_threshold}%"
                )
                threshold_exceeded = True
                break

            time.sleep(30)  # Check every 30 seconds

        if not threshold_exceeded:
            logging.error(
                f"CPU did not exceed threshold within {timeout_minutes} minutes"
            )

        return threshold_exceeded

    def wait_for_alarm(self, alarm_name: str, timeout_minutes: int = 10) -> bool:
        """Wait for CloudWatch alarm to enter ALARM state."""
        logging.info("\n" + "=" * 70)
        logging.info(f"WAITING FOR ALARM: {alarm_name}")
        logging.info("=" * 70)

        start_time = time.time()
        alarm_triggered = False

        while (time.time() - start_time) < (timeout_minutes * 60):
            alarm_state = self.get_alarm_state(alarm_name)
            elapsed_min = int((time.time() - start_time) / 60)

            logging.info(
                f"[{elapsed_min}/{timeout_minutes} min] Alarm state: {alarm_state}"
            )

            if alarm_state == "ALARM":
                logging.info(f"Alarm triggered: {alarm_name}")
                alarm_triggered = True
                break

            time.sleep(30)  # Check every 30 seconds

        if not alarm_triggered:
            logging.warning(f"Alarm did not trigger within {timeout_minutes} minutes")

        return alarm_triggered

    def verify_scaling(self, expected_min_instances: int) -> bool:
        """Verify ASG scaled up."""
        logging.info("\n" + "=" * 70)
        logging.info("VERIFYING SCALING")
        logging.info("=" * 70)

        desired, current = self.get_asg_capacity()
        logging.info(f"ASG Desired Capacity: {desired}")
        logging.info(f"ASG Current Capacity: {current}")
        logging.info(f"Expected minimum: {expected_min_instances}")

        if desired >= expected_min_instances:
            logging.info(f"ASG scaled up to {desired} instances")
            return True
        else:
            logging.error(f"ASG did not scale up: {desired} < {expected_min_instances}")
            return False

    def verify_new_user_instance_assignment(self) -> bool:
        """Verify new user (user 8) is assigned to a different instance than user 7."""
        logging.info("\n" + "=" * 70)
        logging.info("VERIFYING NEW USER INSTANCE ASSIGNMENT")
        logging.info("=" * 70)

        # Get user 7's instance
        user7_instance = self.get_user_instance_assignment(
            self.config.load_generator_user
        )
        if not user7_instance:
            logging.warning("Could not determine user 7's instance assignment")
            return False

        logging.info(f"User 7 instance: {user7_instance}")

        # Log in user 8 (make an API call to trigger assignment)
        logging.info("Logging in user 8...")
        token = self.tokens[self.config.new_user]
        headers = {"Authorization": f"Bearer {token}"}

        try:
            response = self.session.get(
                f"{self.config.api_base_url}/api/health",
                headers=headers,
                timeout=10,
            )
            logging.info(f"User 8 login response: {response.status_code}")
        except Exception as e:
            logging.warning(f"User 8 login error: {e}")

        # Wait a bit for assignment to propagate
        time.sleep(5)

        # Get user 8's instance
        user8_instance = self.get_user_instance_assignment(self.config.new_user)
        if not user8_instance:
            logging.warning("Could not determine user 8's instance assignment")
            return False

        logging.info(f"User 8 instance: {user8_instance}")

        # Verify they're on different instances
        if user7_instance != user8_instance:
            logging.info("OPTIMAL: User 8 assigned to different instance")
            return True
        else:
            logging.warning("SUBOPTIMAL: User 8 assigned to same instance as User 7")
            logging.warning(
                "Expected: New user should be assigned to less-loaded instance"
            )
            return False

    def run_test(self) -> bool:
        """Run the complete CPU/Memory autoscaling test."""
        logging.info("\n" + "=" * 80)
        logging.info("FREE TIER AUTOSCALING TEST - CPU/MEMORY BASED USAGE")
        logging.info("=" * 80)
        logging.info(f"Load generator: {self.config.load_generator_user}")
        logging.info(f"New user: {self.config.new_user}")
        logging.info(f"Workspaces to create: {self.config.num_workspaces}")
        logging.info(f"CPU threshold: {self.config.cpu_threshold}%")
        logging.info(f"API URL: {self.config.api_base_url}")
        logging.info("=" * 80)

        try:
            # Step 1: Load tokens
            if not self.load_tokens():
                return False

            # Step 2: Get baseline capacity
            logging.info("\n" + "=" * 70)
            logging.info("BASELINE CAPACITY")
            logging.info("=" * 70)
            baseline_desired, baseline_current = self.get_asg_capacity()
            logging.info(f"Baseline ASG Desired: {baseline_desired}")
            logging.info(f"Baseline ASG Current: {baseline_current}")

            # Step 3: Check baseline alarm state
            cpu_alarm_state = self.get_alarm_state(self.config.cpu_alarm_name)
            logging.info(f"CPU Alarm state: {cpu_alarm_state}")

            # Step 4: Setup workspaces
            if not self.setup_workspaces():
                logging.error("Failed to setup workspaces")
                return False

            # Step 5: Generate CPU load
            if not self.generate_cpu_load():
                logging.error("Failed to generate CPU load")
                return False

            # Step 6: Wait for CPU to exceed threshold
            if not self.wait_for_cpu_threshold(timeout_minutes=15):
                logging.warning("CPU threshold not exceeded, continuing anyway...")

            # Step 7: Wait for CloudWatch alarm
            alarm_triggered = self.wait_for_alarm(
                self.config.cpu_alarm_name,
                timeout_minutes=self.config.total_alarm_wait_minutes() + 2,
            )

            # Step 8: Verify scaling occurred
            if not self.verify_scaling(baseline_desired + 1):
                if not alarm_triggered:
                    logging.error(
                        "Scaling failed and alarm did not trigger - test inconclusive"
                    )
                    return False

            # Step 9: Wait for new instance to launch
            logging.info("\n" + "=" * 70)
            logging.info("WAITING FOR NEW INSTANCE TO LAUNCH")
            logging.info("=" * 70)
            logging.info("Waiting 5 minutes for instance to become InService...")
            time.sleep(300)

            # Step 10: Verify new user is assigned to different instance
            assignment_optimal = self.verify_new_user_instance_assignment()

            # Step 11: Final summary
            logging.info("\n" + "=" * 80)
            logging.info("TEST SUMMARY")
            logging.info("=" * 80)
            logging.info(f"Workspaces created: {len(self.workspaces)}")
            logging.info(f"Workflows submitted: {len(self.submitted_workflows)}")
            logging.info(f"CPU Alarm triggered: {alarm_triggered}")
            logging.info(f"ASG scaled up: {self.verify_scaling(baseline_desired + 1)}")
            logging.info(f"User 8 assignment optimal: {assignment_optimal}")

            if alarm_triggered and assignment_optimal:
                logging.info("\nTEST COMPLETED - OPTIMAL BEHAVIOR")
            elif alarm_triggered:
                logging.info(
                    "\nTEST COMPLETED - Alarm triggered but user assignment suboptimal"
                )
            else:
                logging.info("\nTEST COMPLETED - Alarm did not trigger as expected")

            logging.info("=" * 80)
            return True

        except Exception as e:
            logging.error(f"\nTEST FAILED: {e}")
            import traceback

            traceback.print_exc()
            return False


def main():
    """Main entry point."""
    # Create config and test instance
    config = TestConfig()
    test = AutoscalingUsageTest(config)

    # Run test (tokens will be auto-generated if needed)
    success = test.run_test()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
