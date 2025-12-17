#!/usr/bin/env python3
"""
Premium Instance Load Test

RUNTIME ENVIRONMENT:
Run locally (with AWS credentials and Terraform state)

WHAT IT TESTS:
Tests how a single premium instance handles concurrent load by:
1. Assigning premium user to a dedicated instance (waits for migration if needed)
2. Creating 10 workspaces with sample data
3. Fetching workflow structure from first workspace
4. Submitting 10 concurrent workflows across multiple workspaces
5. Monitoring CPU, Memory, and workflow execution status
6. Observing performance degradation vs crash vs stability

This test REQUIRES a dedicated premium instance - if the user is initially
assigned to autoscaling-pool, the test will wait up to 10 minutes for migration
to a dedicated instance. If no dedicated instance becomes available, the test
will fail rather than run on shared infrastructure.

This test does NOT expect autoscaling to trigger - it's designed to stress
a single premium instance to understand its limits and behavior under load.

REQUIREMENTS:
- AWS credentials configured (AWS CLI or environment variables)
- IAM permissions: ec2:DescribeInstances, ecs:*, cloudwatch:*, lambda:InvokeFunction
- Terraform outputs with premium instance configuration
- Test user: optinist_test_user_premium@araya.org

EXPECTED BEHAVIOR:
- Instance may slow down under heavy load
- CPU/Memory should increase significantly
- Workflows may queue or take longer to complete
- Instance should NOT crash (graceful degradation)
- No autoscaling expected (single premium user = single instance)

Usage:
    python test_premium_load.py
    python test_premium_load.py --workspaces 5 --workflows 5  # Lighter load
    python test_premium_load.py --duration 1800  # 30 minute monitoring
    python test_premium_load.py --skip-token-gen  # Use existing tokens.json
"""

import argparse
import concurrent.futures
import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, Optional

import boto3
import requests

# Add current directory to path for imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from get_jwt_tokens import generate_jwt_tokens
except ImportError as e:
    print(f"Warning: Could not import get_jwt_tokens: {e}")
    generate_jwt_tokens = None

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


class PremiumInstanceMonitor:
    """Monitor premium instance metrics during load test"""

    def __init__(
        self, instance_id: str, cluster_name: str, service_name: str, region: str
    ):
        self.instance_id = instance_id
        self.cluster_name = cluster_name
        self.service_name = service_name
        self.region = region
        self.ec2 = boto3.client("ec2", region_name=region)
        self.ecs = boto3.client("ecs", region_name=region)
        self.cloudwatch = boto3.client("cloudwatch", region_name=region)
        self.monitoring = True
        self.metrics_data = []

    def get_instance_metrics(self) -> Dict:
        """Get EC2 instance CPU and other metrics"""
        try:
            from datetime import timezone

            end_time = datetime.now(timezone.utc)
            start_time = end_time - timedelta(minutes=5)

            # Get CPU utilization at instance level
            cpu_response = self.cloudwatch.get_metric_statistics(
                Namespace="AWS/EC2",
                MetricName="CPUUtilization",
                Dimensions=[{"Name": "InstanceId", "Value": self.instance_id}],
                StartTime=start_time,
                EndTime=end_time,
                Period=60,
                Statistics=["Average", "Maximum"],
            )

            cpu_avg = 0
            cpu_max = 0
            if cpu_response["Datapoints"]:
                latest = sorted(
                    cpu_response["Datapoints"], key=lambda x: x["Timestamp"]
                )[-1]
                cpu_avg = latest.get("Average", 0)
                cpu_max = latest.get("Maximum", 0)

            # Get instance status
            instance_response = self.ec2.describe_instances(
                InstanceIds=[self.instance_id]
            )
            instance_state = "unknown"
            if instance_response["Reservations"]:
                instance = instance_response["Reservations"][0]["Instances"][0]
                instance_state = instance["State"]["Name"]

            return {
                "cpu_average": round(cpu_avg, 2),
                "cpu_maximum": round(cpu_max, 2),
                "instance_state": instance_state,
            }
        except Exception as e:
            logging.error(f"Error getting instance metrics: {e}")
            return {"cpu_average": 0, "cpu_maximum": 0, "instance_state": "error"}

    def get_ecs_metrics(self) -> Dict:
        """Get ECS task/service metrics"""
        try:
            from datetime import timezone

            end_time = datetime.now(timezone.utc)
            start_time = end_time - timedelta(minutes=5)

            # Get ECS service CPU and Memory
            cpu_response = self.cloudwatch.get_metric_statistics(
                Namespace="AWS/ECS",
                MetricName="CPUUtilization",
                Dimensions=[
                    {"Name": "ServiceName", "Value": self.service_name},
                    {"Name": "ClusterName", "Value": self.cluster_name},
                ],
                StartTime=start_time,
                EndTime=end_time,
                Period=60,
                Statistics=["Average", "Maximum"],
            )

            memory_response = self.cloudwatch.get_metric_statistics(
                Namespace="AWS/ECS",
                MetricName="MemoryUtilization",
                Dimensions=[
                    {"Name": "ServiceName", "Value": self.service_name},
                    {"Name": "ClusterName", "Value": self.cluster_name},
                ],
                StartTime=start_time,
                EndTime=end_time,
                Period=60,
                Statistics=["Average", "Maximum"],
            )

            cpu_avg = 0
            cpu_max = 0
            if cpu_response["Datapoints"]:
                latest = sorted(
                    cpu_response["Datapoints"], key=lambda x: x["Timestamp"]
                )[-1]
                cpu_avg = latest.get("Average", 0)
                cpu_max = latest.get("Maximum", 0)

            mem_avg = 0
            mem_max = 0
            if memory_response["Datapoints"]:
                latest = sorted(
                    memory_response["Datapoints"], key=lambda x: x["Timestamp"]
                )[-1]
                mem_avg = latest.get("Average", 0)
                mem_max = latest.get("Maximum", 0)

            # Get task count
            task_arns = []
            paginator = self.ecs.get_paginator("list_tasks")
            for page in paginator.paginate(
                cluster=self.cluster_name,
                serviceName=self.service_name,
                desiredStatus="RUNNING",
            ):
                task_arns.extend(page["taskArns"])

            return {
                "cpu_average": round(cpu_avg, 2),
                "cpu_maximum": round(cpu_max, 2),
                "memory_average": round(mem_avg, 2),
                "memory_maximum": round(mem_max, 2),
                "running_tasks": len(task_arns),
            }
        except Exception as e:
            logging.error(f"Error getting ECS metrics: {e}")
            return {
                "cpu_average": 0,
                "cpu_maximum": 0,
                "memory_average": 0,
                "memory_maximum": 0,
                "running_tasks": 0,
            }

    def monitor_metrics(self, interval: int = 30):
        """Continuously monitor metrics during load test"""
        logging.info(f"Starting metrics monitoring (interval: {interval}s)...")

        while self.monitoring:
            try:
                timestamp = datetime.now()
                instance_metrics = self.get_instance_metrics()
                ecs_metrics = self.get_ecs_metrics()

                current_metrics = {
                    "timestamp": timestamp.isoformat(),
                    "instance": instance_metrics,
                    "ecs": ecs_metrics,
                }

                self.metrics_data.append(current_metrics)

                # Log current status
                logging.info(
                    f"Metrics - Instance CPU: {instance_metrics['cpu_average']:.1f}% "
                    f"(max: {instance_metrics['cpu_maximum']:.1f}%), "
                    f"ECS CPU: {ecs_metrics['cpu_average']:.1f}%, "
                    f"Memory: {ecs_metrics['memory_average']:.1f}%, "
                    f"Tasks: {ecs_metrics['running_tasks']}, "
                    f"State: {instance_metrics['instance_state']}"
                )

                time.sleep(interval)

            except Exception as e:
                logging.error(f"Error in monitoring loop: {e}")
                time.sleep(5)

    def stop_monitoring(self):
        """Stop metrics monitoring"""
        self.monitoring = False
        logging.info("Stopped metrics monitoring")


class PremiumLoadTester:
    """Premium instance load testing orchestrator"""

    def __init__(
        self,
        terraform_dir: str,
        api_url: str,
        num_workspaces: int,
        num_workflows: int,
        duration: int,
        skip_token_gen: bool,
        region: str = "ap-northeast-1",
    ):
        self.terraform_dir = terraform_dir
        self.api_url = api_url
        self.num_workspaces = num_workspaces
        self.num_workflows = num_workflows
        self.duration = duration
        self.skip_token_gen = skip_token_gen
        self.region = region

        # Get Terraform outputs
        terraform_outputs = get_terraform_outputs(terraform_dir)

        # Get API URL if not provided
        if not self.api_url:
            self.api_url = "https://araya-optinist.com"

        # Get cluster and service names
        self.cluster_name = terraform_outputs.get("ecs_cluster_name", {}).get("value")
        self.premium_service_name = terraform_outputs.get(
            "ecs_service_name_premium", {}
        ).get("value")

        if not self.cluster_name or not self.premium_service_name:
            raise ValueError("Could not find ECS cluster/service in Terraform outputs")

        self.premium_token = None
        self.assigned_instance_id = None
        self.workspaces = []
        self.submitted_workflows = []
        self.monitor = None

    def setup_authentication(self):
        """Get JWT token for premium test user"""
        if self.skip_token_gen:
            try:
                tokens_file = os.path.join(os.path.dirname(__file__), "tokens.json")
                if os.path.exists(tokens_file):
                    with open(tokens_file, "r") as f:
                        tokens = json.load(f)
                        self.premium_token = tokens.get("premium_token")
                        if self.premium_token:
                            logging.info("Loaded premium token from tokens.json")
                            return True
            except Exception as e:
                logging.warning(f"Failed to load tokens.json: {e}")

        if not generate_jwt_tokens:
            logging.error("Token generation not available")
            return False

        logging.info("Generating JWT token for premium user...")
        tokens = generate_jwt_tokens(
            environment="cloud",
            terraform_dir=self.terraform_dir,
            user_type="premium",
        )

        if tokens and "premium_token" in tokens:
            self.premium_token = tokens["premium_token"]
            logging.info("Successfully generated premium token")
            return True

        logging.error("Failed to generate premium token")
        return False

    def assign_premium_instance(self) -> bool:
        """Assign premium user to dedicated instance and wait for migration if needed"""
        try:
            headers = {
                "Authorization": f"Bearer {self.premium_token}",
                "Content-Type": "application/json",
            }

            logging.info("Assigning premium user to dedicated instance...")
            response = requests.post(
                f"{self.api_url}/users/me/premium/assign",
                headers=headers,
                timeout=180,
            )

            if response.status_code == 200:
                result = response.json()
                self.assigned_instance_id = result.get("instance_id")
                logging.info(f"Initial assignment: {self.assigned_instance_id}")

                # Check if user was assigned to autoscaling-pool (temporary assignment)
                if self.assigned_instance_id == "autoscaling-pool":
                    logging.warning(
                        "User assigned to autoscaling-pool (shared instance). "
                        "Premium instance is being provisioned..."
                    )
                    logging.info(
                        "Waiting for migration to dedicated premium instance..."
                    )

                    # Poll for dedicated instance assignment (wait up to 15 minutes)
                    max_wait_time = 900  # 15 minutes
                    poll_interval = 30  # Check every 30 seconds
                    elapsed = 0

                    while elapsed < max_wait_time:
                        time.sleep(poll_interval)
                        elapsed += poll_interval

                        # Check current assignment status
                        status_response = requests.get(
                            f"{self.api_url}/users/me/premium/status",
                            headers=headers,
                            timeout=30,
                        )

                        if status_response.status_code == 200:
                            status_result = status_response.json()
                            assignment = status_result.get("assignment", {})
                            current_instance = assignment.get("instance_id")

                            if (
                                current_instance
                                and current_instance != "autoscaling-pool"
                            ):
                                self.assigned_instance_id = current_instance
                                logging.info(
                                    f"Migrated to dedicated instance: "
                                    f"{self.assigned_instance_id} "
                                    f"(waited {elapsed}s)"
                                )
                                # Wait for instance to stabilize
                                logging.info(
                                    "Waiting 30 seconds for instance to stabilize..."
                                )
                                time.sleep(30)
                                return True

                        logging.info(
                            f"Still on autoscaling-pool... "
                            f"({elapsed}/{max_wait_time}s elapsed)"
                        )

                    # Timeout - still on autoscaling-pool
                    logging.error(
                        f"Timeout: User still assigned to autoscaling-pool "
                        f"after {max_wait_time}s. "
                        f"Premium instance may not be available."
                    )
                    return False

                else:
                    # Already assigned to dedicated instance
                    logging.info(
                        f"Assigned to dedicated instance: {self.assigned_instance_id}"
                    )
                    # Wait for instance to be fully ready
                    logging.info("Waiting 30 seconds for instance to stabilize...")
                    time.sleep(30)
                    return True

            else:
                logging.error(
                    f"Assignment failed: {response.status_code} - {response.text}"
                )
                return False

        except Exception as e:
            logging.error(f"Error assigning premium instance: {e}")
            return False

    def create_workspace(self, workspace_index: int) -> Optional[int]:
        """Create a workspace for load testing"""
        try:
            headers = {
                "Authorization": f"Bearer {self.premium_token}",
                "Content-Type": "application/json",
            }

            workspace_name = f"premium_load_test_{workspace_index}_{int(time.time())}"
            response = requests.post(
                f"{self.api_url}/workspace",
                json={"name": workspace_name},
                headers=headers,
                timeout=30,
            )

            if response.status_code == 200:
                workspace = response.json()
                workspace_id = workspace.get("id")
                logging.info(
                    f"Created workspace {workspace_index}: "
                    f"{workspace_id} ({workspace_name})"
                )
                return workspace_id
            else:
                logging.error(
                    f"Workspace {workspace_index} creation failed: "
                    f"{response.status_code} - {response.text}"
                )

        except Exception as e:
            logging.error(f"Error creating workspace {workspace_index}: {e}")

        return None

    def import_sample_data(self, workspace_id: int, workspace_index: int) -> bool:
        """Import tutorial sample data into workspace"""
        try:
            headers = {"Authorization": f"Bearer {self.premium_token}"}

            response = requests.get(
                f"{self.api_url}/workflow/sample_data/{workspace_id}/tutorial",
                headers=headers,
                timeout=120,
            )

            if response.status_code == 200:
                logging.info(f"Imported sample data for workspace {workspace_index}")
                return True
            else:
                logging.error(
                    f"Sample data import failed for workspace {workspace_index}: "
                    f"{response.status_code} - {response.text}"
                )

        except Exception as e:
            logging.error(
                f"Error importing sample data for workspace {workspace_index}: {e}"
            )

        return False

    def get_existing_workspaces(self) -> list:
        """Get list of existing workspaces for the current user"""
        try:
            headers = {"Authorization": f"Bearer {self.premium_token}"}

            # Use pagination parameters to get all workspaces
            response = requests.get(
                f"{self.api_url}/workspaces",
                headers=headers,
                params={"offset": 0, "limit": 50},  # Get up to 50 workspaces
                timeout=30,
            )

            if response.status_code == 200:
                data = response.json()
                # Handle paginated response - the items are in the "items" field
                if isinstance(data, dict) and "items" in data:
                    workspaces = data["items"]
                else:
                    workspaces = data

                # Extract workspace IDs
                workspace_ids = [ws["id"] for ws in workspaces if "id" in ws]
                logging.info(
                    f"Found {len(workspace_ids)} existing workspaces: {workspace_ids}"
                )
                return workspace_ids
            else:
                logging.warning(
                    f"Failed to get existing workspaces: "
                    f"{response.status_code} - {response.text}"
                )
                return []

        except Exception as e:
            logging.warning(f"Error getting existing workspaces: {e}")
            return []

    def ensure_workspace_has_data(self, workspace_id: int) -> bool:
        """Ensure a workspace has sample data - import if needed"""
        try:
            # Check if workspace has any experiments
            headers = {"Authorization": f"Bearer {self.premium_token}"}

            response = requests.get(
                f"{self.api_url}/experiments/{workspace_id}",
                headers=headers,
                timeout=30,
            )

            if response.status_code == 200:
                experiments = response.json()
                if experiments and len(experiments) > 0:
                    logging.info(
                        f"Workspace {workspace_id} already has "
                        f"{len(experiments)} experiment(s)"
                    )
                    return True
                else:
                    # No experiments, need to import sample data
                    logging.info(
                        f"Workspace {workspace_id} has no data, importing sample data"
                    )
                    return self.import_sample_data(workspace_id, workspace_id)
            else:
                logging.warning(
                    f"Could not check experiments for workspace {workspace_id}: "
                    f"{response.status_code}"
                )
                # Try importing sample data anyway
                return self.import_sample_data(workspace_id, workspace_id)

        except Exception as e:
            logging.warning(
                f"Error checking workspace {workspace_id} data: {e}, "
                f"attempting to import sample data"
            )
            return self.import_sample_data(workspace_id, workspace_id)

    def fetch_workflow_from_workspace(self, workspace_id: int) -> Optional[Dict]:
        """Fetch workflow structure from a workspace that has sample data imported"""
        try:
            headers = {"Authorization": f"Bearer {self.premium_token}"}

            response = requests.get(
                f"{self.api_url}/workflow/fetch/{workspace_id}",
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

                logging.info(f"Fetched workflow from workspace {workspace_id}")
                return run_item
            else:
                logging.error(
                    f"Failed to fetch workflow from workspace {workspace_id}: "
                    f"{response.status_code} - {response.text}"
                )
                return None

        except Exception as e:
            logging.error(f"Error fetching workflow from workspace {workspace_id}: {e}")
            return None

    def submit_workflow(
        self, workspace_id: int, workflow_index: int, workflow_data: Dict
    ) -> Optional[str]:
        """Submit a workflow to a workspace"""
        try:
            headers = {
                "Authorization": f"Bearer {self.premium_token}",
                "Content-Type": "application/json",
            }

            workflow_copy = workflow_data.copy()
            workflow_copy["name"] = f"premium_load_{workflow_index}_{int(time.time())}"

            response = requests.post(
                f"{self.api_url}/run/{workspace_id}",
                json=workflow_copy,
                headers=headers,
                timeout=60,
            )

            if response.status_code == 200:
                unique_id = response.text.strip('"')
                logging.info(
                    f"Submitted workflow {workflow_index}: {unique_id[:12]}..."
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
                    f"Workflow {workflow_index} submission failed: "
                    f"{response.status_code} - {response.text}"
                )

        except Exception as e:
            logging.error(f"Error submitting workflow {workflow_index}: {e}")

        return None

    def check_workflow_status(self, workspace_id: int, unique_id: str) -> Optional[str]:
        """Check the status of a workflow"""
        try:
            headers = {"Authorization": f"Bearer {self.premium_token}"}

            # Use the experiments endpoint to get all experiments for the workspace
            response = requests.get(
                f"{self.api_url}/experiments/{workspace_id}",
                headers=headers,
                timeout=30,
            )

            if response.status_code == 200:
                experiments = response.json()

                # Find the experiment with matching unique_id
                if unique_id in experiments:
                    experiment = experiments[unique_id]
                    # Map success field to status
                    # 0 = running, 1 = success, -1 = failed
                    success_value = experiment.get("success", 0)
                    if success_value == 1:
                        return "success"
                    elif success_value == -1:
                        return "failed"
                    else:
                        return "running"
                else:
                    # Experiment not found, might still be pending
                    return "pending"

        except Exception as e:
            logging.debug(f"Error checking workflow status: {e}")

        return None

    def setup_workspaces(self):
        """Setup workspaces - reuse existing ones and create new ones if needed"""
        logging.info(f"\nSetting up {self.num_workspaces} workspaces...")

        # Get existing workspaces
        existing_workspaces = self.get_existing_workspaces()

        # Use existing workspaces up to the number we need
        num_existing = min(len(existing_workspaces), self.num_workspaces)
        existing_to_use = existing_workspaces[:num_existing]

        if num_existing > 0:
            logging.info(
                f"Reusing {num_existing} existing workspace(s): {existing_to_use}"
            )

            # Ensure existing workspaces have sample data
            logging.info("Ensuring existing workspaces have sample data...")
            valid_workspaces = []

            def ensure_data(workspace_id):
                if self.ensure_workspace_has_data(workspace_id):
                    return workspace_id
                return None

            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                futures = [
                    executor.submit(ensure_data, ws_id) for ws_id in existing_to_use
                ]

                for future in concurrent.futures.as_completed(futures):
                    workspace_id = future.result()
                    if workspace_id:
                        valid_workspaces.append(workspace_id)

            self.workspaces = valid_workspaces
            logging.info(
                f"{len(valid_workspaces)} existing workspace(s) ready with data"
            )

        # Calculate how many more we need to create
        num_to_create = self.num_workspaces - len(self.workspaces)

        if num_to_create > 0:
            logging.info(f"Creating {num_to_create} additional workspace(s)...")

            def setup_workspace(workspace_index):
                workspace_id = self.create_workspace(workspace_index)
                if workspace_id:
                    if self.import_sample_data(workspace_id, workspace_index):
                        return workspace_id
                return None

            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                futures = [
                    executor.submit(setup_workspace, i)
                    for i in range(len(self.workspaces), self.num_workspaces)
                ]

                for future in concurrent.futures.as_completed(futures):
                    workspace_id = future.result()
                    if workspace_id:
                        self.workspaces.append(workspace_id)

        logging.info(
            f"Workspace setup complete: {len(self.workspaces)} total workspaces "
            f"({num_existing} reused, "
            f"{len(self.workspaces) - num_existing} newly created)"
        )
        return len(self.workspaces) >= self.num_workspaces

    def submit_all_workflows(self):
        """Submit workflows across all workspaces"""
        if not self.workspaces:
            logging.error("No workspaces available for workflow submission")
            logging.info("Attempting to create workspaces now...")
            if not self.setup_workspaces():
                logging.error("Failed to create workspaces, cannot submit workflows")
                return False

        logging.info(f"\nSubmitting {self.num_workflows} workflows...")

        # Fetch workflow from each workspace that has sample data
        logging.info("Fetching workflows from all workspaces...")
        workspace_workflows = {}
        for workspace_id in self.workspaces:
            workflow = self.fetch_workflow_from_workspace(workspace_id)
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

        # Submit workflows - each workspace gets its own workflow
        def submit_workflow_task(workflow_index):
            workspace_id = self.workspaces[workflow_index % len(self.workspaces)]

            # Use the workflow from this specific workspace
            if workspace_id in workspace_workflows:
                workflow_data = workspace_workflows[workspace_id]
            else:
                # Fallback to first available workflow
                # if this workspace doesn't have one
                workspace_id = list(workspace_workflows.keys())[0]
                workflow_data = workspace_workflows[workspace_id]
                logging.warning(f"Using fallback workflow for index {workflow_index}")

            return self.submit_workflow(workspace_id, workflow_index, workflow_data)

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [
                executor.submit(submit_workflow_task, i)
                for i in range(self.num_workflows)
            ]

            successful_submissions = 0
            for future in concurrent.futures.as_completed(futures):
                if future.result():
                    successful_submissions += 1

        logging.info(
            f"Successfully submitted "
            f"{successful_submissions}/{self.num_workflows} workflows"
        )
        return successful_submissions > 0

    def monitor_workflows(self):
        """Monitor workflow completion status"""
        logging.info("\nMonitoring workflow execution...")

        completed = 0
        failed = 0
        running = 0
        pending = 0

        for workflow in self.submitted_workflows:
            status = self.check_workflow_status(
                workflow["workspace_id"], workflow["unique_id"]
            )

            if status == "success":
                completed += 1
            elif status in ["failed", "error"]:
                failed += 1
            elif status == "running":
                running += 1
            else:
                pending += 1

        logging.info(
            f"Workflow status - Completed: {completed}, Running: {running}, "
            f"Pending: {pending}, Failed: {failed}"
        )

        return {
            "completed": completed,
            "running": running,
            "pending": pending,
            "failed": failed,
        }

    def generate_report(self) -> str:
        """Generate load test report"""
        report = []
        report.append("=" * 70)
        report.append("PREMIUM INSTANCE LOAD TEST REPORT")
        report.append("=" * 70)
        report.append("")

        # Test configuration
        report.append("TEST CONFIGURATION:")
        report.append("Premium User: optinist_test_user_premium@araya.org")
        report.append(f"Instance ID: {self.assigned_instance_id}")
        report.append(f"Workspaces Created: {len(self.workspaces)}")
        report.append(f"Workflows Submitted: {len(self.submitted_workflows)}")
        report.append(f"Test Duration: {self.duration} seconds")
        report.append("")

        # Performance metrics
        if (
            self.monitor
            and self.monitor.metrics_data
            and len(self.monitor.metrics_data) > 0
        ):
            report.append("PERFORMANCE METRICS:")

            # Calculate peak values
            peak_instance_cpu = max(
                m["instance"]["cpu_average"] for m in self.monitor.metrics_data
            )
            peak_ecs_cpu = max(
                m["ecs"]["cpu_average"] for m in self.monitor.metrics_data
            )
            peak_memory = max(
                m["ecs"]["memory_average"] for m in self.monitor.metrics_data
            )

            # Calculate average values
            avg_instance_cpu = sum(
                m["instance"]["cpu_average"] for m in self.monitor.metrics_data
            ) / len(self.monitor.metrics_data)
            avg_ecs_cpu = sum(
                m["ecs"]["cpu_average"] for m in self.monitor.metrics_data
            ) / len(self.monitor.metrics_data)
            avg_memory = sum(
                m["ecs"]["memory_average"] for m in self.monitor.metrics_data
            ) / len(self.monitor.metrics_data)

            report.append(f"Peak Instance CPU: {peak_instance_cpu:.1f}%")
            report.append(f"Peak ECS CPU: {peak_ecs_cpu:.1f}%")
            report.append(f"Peak Memory: {peak_memory:.1f}%")
            report.append(f"Average Instance CPU: {avg_instance_cpu:.1f}%")
            report.append(f"Average ECS CPU: {avg_ecs_cpu:.1f}%")
            report.append(f"Average Memory: {avg_memory:.1f}%")
            report.append("")

            # Check for instance health and state transitions
            instance_states = [
                m["instance"]["instance_state"] for m in self.monitor.metrics_data
            ]
            unique_states = set(instance_states)

            report.append("INSTANCE HEALTH:")
            report.append(
                f"Instance States Observed: {', '.join(sorted(unique_states))}"
            )

            # Analyze state stability
            if len(unique_states) == 1:
                only_state = list(unique_states)[0]
                if only_state == "running":
                    report.append("Instance remained stable (running) throughout test")
                else:
                    report.append(
                        f"Instance remained in {only_state} state throughout test"
                    )
            else:
                # Multiple states - check for problematic transitions
                report.append(
                    f"Instance experienced {len(unique_states)} different states"
                )

                # Track state transitions
                transitions = []
                for i in range(1, len(instance_states)):
                    if instance_states[i] != instance_states[i - 1]:
                        transitions.append(
                            f"{instance_states[i-1]} → {instance_states[i]}"
                        )

                if transitions:
                    report.append(f"State transitions: {', '.join(transitions)}")

                # Check for critical states
                critical_states = {"stopped", "stopping", "terminated", "terminating"}
                if unique_states & critical_states:
                    report.append(
                        f"CRITICAL: Instance entered problematic state(s): "
                        f"{unique_states & critical_states}"
                    )

                # Check for concerning states
                concerning_states = {"pending", "shutting-down", "error"}
                if unique_states & concerning_states:
                    report.append(
                        f"Instance entered concerning state(s): "
                        f"{unique_states & concerning_states}"
                    )

            report.append("")

        # Final workflow status
        final_status = self.monitor_workflows()
        report.append("WORKFLOW EXECUTION:")
        report.append(f"Completed: {final_status['completed']}")
        report.append(f"Running: {final_status['running']}")
        report.append(f"Pending: {final_status['pending']}")
        report.append(f"Failed: {final_status['failed']}")
        report.append("")

        # Analysis
        report.append("ANALYSIS:")
        total_workflows = len(self.submitted_workflows)
        success_rate = (
            (final_status["completed"] / total_workflows * 100)
            if (total_workflows > 0)
            else 0
        )

        report.append(f"Success Rate: {success_rate:.1f}%")

        if success_rate >= 80:
            report.append("Instance handled load well (≥80% success)")
        elif success_rate >= 50:
            report.append("Instance showed degraded performance (50-80% success)")
        else:
            report.append("Instance struggled under load (<50% success)")

            if peak_instance_cpu < 70:
                report.append("CPU usage remained reasonable (<70%)")
            elif peak_instance_cpu < 90:
                report.append("CPU usage was high (70-90%)")
            else:
                report.append("CPU usage was critical (≥90%)")

            if peak_memory < 70:
                report.append("Memory usage remained reasonable (<70%)")
            elif peak_memory < 90:
                report.append("Memory usage was high (70-90%)")
            else:
                report.append("Memory usage was critical (≥90%)")

        report.append("")
        report.append("=" * 70)

        return "\n".join(report)

    def unassign_premium_instance(self):
        """Release premium instance assignment"""
        if not self.premium_token:
            return

        try:
            headers = {
                "Authorization": f"Bearer {self.premium_token}",
                "Content-Type": "application/json",
            }

            logging.info("Releasing premium instance assignment...")
            response = requests.delete(
                f"{self.api_url}/users/me/premium/assign",
                headers=headers,
                timeout=60,
            )

            if response.status_code == 200:
                logging.info("Successfully released premium instance")
            else:
                logging.warning(
                    f"Failed to release instance: "
                    f"{response.status_code} - {response.text}"
                )

        except Exception as e:
            logging.error(f"Error releasing premium instance: {e}")

    def run_load_test(self):
        """Execute the complete load test"""
        try:
            # Step 1: Setup authentication
            logging.info("=" * 70)
            logging.info("PREMIUM INSTANCE LOAD TEST")
            logging.info("=" * 70)

            if not self.setup_authentication():
                logging.error("Failed to setup authentication")
                return False

            # Step 2: Assign premium instance (waits for dedicated instance)
            if not self.assign_premium_instance():
                logging.error("Failed to assign premium instance")
                return False

            # Verify we have a real instance (not autoscaling-pool)
            if self.assigned_instance_id == "autoscaling-pool":
                logging.error(
                    "CRITICAL: User is still on autoscaling-pool. Cannot run load test "
                    "without dedicated premium instance."
                )
                return False

            logging.info(
                f"Ready to test with premium instance: " f"{self.assigned_instance_id}"
            )

            # Step 3: Initialize monitor
            self.monitor = PremiumInstanceMonitor(
                instance_id=self.assigned_instance_id,
                cluster_name=self.cluster_name,
                service_name=self.premium_service_name,
                region=self.region,
            )

            # Start monitoring in background (30 second interval)
            monitor_thread = threading.Thread(
                target=self.monitor.monitor_metrics, args=(30,), daemon=True
            )
            monitor_thread.start()

            # Wait for initial metrics
            time.sleep(5)

            # Step 4: Create workspaces
            if not self.setup_workspaces():
                logging.error("Failed to create workspaces")
                return False

            # Step 5: Submit workflows
            if not self.submit_all_workflows():
                logging.error("Failed to submit workflows")
                return False

            # Step 6: Monitor for duration
            logging.info(f"\nMonitoring for {self.duration} seconds...")
            logging.info("Observing CPU, Memory, and workflow execution...")

            # Check workflow status periodically
            elapsed = 0
            check_interval = 60  # Check every minute

            while elapsed < self.duration:
                time.sleep(min(check_interval, self.duration - elapsed))
                elapsed += check_interval

                if elapsed % 120 == 0:  # Log status every 2 minutes
                    status = self.monitor_workflows()
                    logging.info(
                        f"[{elapsed}s] Workflows - Completed: {status['completed']}, "
                        f"Running: {status['running']}, Pending: {status['pending']}"
                    )

            # Stop monitoring
            self.monitor.stop_monitoring()

            # Step 7: Generate and display report
            logging.info("\n" + "=" * 70)
            logging.info("GENERATING REPORT...")
            logging.info("=" * 70)

            report = self.generate_report()
            print("\n" + report)

            # Save detailed results
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = f"premium_load_test_{timestamp}.json"

            detailed_results = {
                "config": {
                    "instance_id": self.assigned_instance_id,
                    "num_workspaces": self.num_workspaces,
                    "num_workflows": self.num_workflows,
                    "duration": self.duration,
                },
                "workspaces": self.workspaces,
                "workflows": self.submitted_workflows,
                "metrics": self.monitor.metrics_data if self.monitor else [],
                "report": report,
            }

            with open(output_file, "w") as f:
                json.dump(detailed_results, f, indent=2, default=str)

            logging.info(f"\nDetailed results saved to: {output_file}")
            logging.info("\nLoad test completed successfully!")

            return True

        except KeyboardInterrupt:
            logging.info("\nLoad test interrupted by user")
            if self.monitor:
                self.monitor.stop_monitoring()
            return False

        except Exception as e:
            logging.error(f"\nLoad test failed: {e}")
            import traceback

            traceback.print_exc()
            if self.monitor:
                self.monitor.stop_monitoring()
            return False

        finally:
            # Always cleanup: unassign premium instance
            self.unassign_premium_instance()


def main():
    parser = argparse.ArgumentParser(description="Premium Instance Load Test")

    parser.add_argument(
        "--terraform-dir",
        default="terraform",
        help="Path to Terraform directory (default: terraform)",
    )
    parser.add_argument(
        "--api-url",
        help="API URL (default: auto-detected from Terraform)",
    )
    parser.add_argument(
        "--workspaces",
        type=int,
        default=10,
        help="Number of workspaces to create (default: 10)",
    )
    parser.add_argument(
        "--workflows",
        type=int,
        default=10,
        help="Number of workflows to submit (default: 10)",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=900,
        help="Test duration in seconds (default: 900 = 15 minutes)",
    )
    parser.add_argument(
        "--skip-token-gen",
        action="store_true",
        help="Skip token generation, use existing tokens.json",
    )
    parser.add_argument(
        "--region",
        default="ap-northeast-1",
        help="AWS region (default: ap-northeast-1)",
    )

    args = parser.parse_args()

    # Resolve terraform directory path
    script_dir = os.path.dirname(os.path.abspath(__file__))
    terraform_dir = os.path.abspath(os.path.join(script_dir, args.terraform_dir))

    if not os.path.exists(terraform_dir):
        logging.error(f"Terraform directory not found: {terraform_dir}")
        sys.exit(1)

    tester = PremiumLoadTester(
        terraform_dir=terraform_dir,
        api_url=args.api_url,
        num_workspaces=args.workspaces,
        num_workflows=args.workflows,
        duration=args.duration,
        skip_token_gen=args.skip_token_gen,
        region=args.region,
    )

    success = tester.run_load_test()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
