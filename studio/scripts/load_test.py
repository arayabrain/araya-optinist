#!/usr/bin/env python3
"""
OptiNiSt Autoscaling Load Test

IMPORTANT: This script should be run LOCALLY, NOT in the cloud ECS container.

WHERE TO RUN:
- Local development machine - RECOMMENDED (generates external load)
- CI/CD runner - Works (for automated load testing)
- Cloud ECS container - DO NOT RUN HERE (wrong environment)

WHY RUN LOCALLY:
This load test generates external load AGAINST your cloud infrastructure.
Running it inside the ECS container would:
1. Test the container against itself (incorrect)
2. Consume resources that should be monitored (skews results)
3. Not have proper IAM permissions for monitoring

REQUIREMENTS:
- AWS credentials configured (AWS CLI or environment variables)
- IAM permissions: autoscaling:Describe*, cloudwatch:GetMetricStatistics, ecs:Describe*
- Python 3.7+ with boto3, requests
- Terraform state with deployed infrastructure
- JWT tokens for authentication (auto-generated or from tokens.json)

WHAT IT TESTS:
Autoscaling behavior by generating controlled load to trigger CPU and memory
thresholds, then validates that the Auto Scaling Group responds correctly
according to configured CloudWatch alarms.

Autoscaling Configuration:
- Scale-up: CPU >60% or Memory >80% for 3 evaluation periods
- Scale-down: CPU <20% and Memory <10% for 3 evaluation periods
- Cooldown: 300 seconds
- Health check grace period: 180 seconds

Usage:
    python load_test.py                                    # Full test with auto-detected settings
    python load_test.py --cpu-only                         # CPU stress test only
    python load_test.py --memory-only                      # Memory stress test only
    python load_test.py --duration 600                     # 10-minute test duration
    python load_test.py --concurrent-workflows 10          # Custom workflow count
    python load_test.py --terraform-dir /path/to/terraform # Custom terraform directory
    python load_test.py --api-url http://custom-lb.com     # Override API URL

Features:
- CPU stress testing via compute-intensive workflows
- Memory stress testing via large data processing
- Real-time CloudWatch metrics monitoring
- Autoscaling behavior validation
- Detailed performance analysis and reporting
- Automatic API URL detection from Terraform outputs
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
from typing import Dict, List, Optional

import boto3
import requests
import yaml

# Add the current directory to Python path for imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from get_jwt_tokens import generate_jwt_tokens
except ImportError as e:
    print(f"Warning: Could not import get_jwt_tokens module: {e}")
    print("Token generation functionality may be limited")


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


class LoadTestConfig:
    """Configuration for load testing parameters"""

    def __init__(self, args):
        self.terraform_dir = args.terraform_dir
        self.api_url = args.api_url
        self.duration = args.duration
        self.concurrent_workflows = args.concurrent_workflows
        self.cpu_only = args.cpu_only
        self.memory_only = args.memory_only
        self.target_cpu_threshold = args.target_cpu
        self.target_memory_threshold = args.target_memory
        self.cooldown_period = args.cooldown
        self.monitoring_interval = args.monitoring_interval
        self.aws_region = args.aws_region
        self.skip_token_gen = args.skip_token_gen

        # Get configuration from Terraform outputs
        terraform_outputs = get_terraform_outputs(self.terraform_dir)

        # Auto-detect API URL from Terraform if not provided
        if not self.api_url:
            lb_dns = terraform_outputs.get("alb_dns_name", {}).get("value")
            if not lb_dns:
                raise ValueError(
                    "Could not find alb_dns_name in Terraform outputs. "
                    "Please ensure your infrastructure is deployed."
                )
            self.api_url = f"http://{lb_dns}"
            logging.info(f"Detected API URL from Terraform: {self.api_url}")

        # Get ASG name from Terraform
        self.asg_name = args.asg_name or terraform_outputs.get("asg_name", {}).get("value")
        if not self.asg_name:
            raise ValueError("Could not find asg_name in Terraform outputs")

        # Get ECS cluster name from Terraform
        self.cluster_name = args.cluster_name or terraform_outputs.get("ecs_cluster_name", {}).get("value")
        if not self.cluster_name:
            raise ValueError("Could not find ecs_cluster_name in Terraform outputs")

        # Get ECS service name from Terraform
        self.service_name = args.service_name or terraform_outputs.get("ecs_service_name_autoscaling", {}).get("value")
        if not self.service_name:
            raise ValueError("Could not find ecs_service_name_autoscaling in Terraform outputs")

        self.output_file = args.output_file


class CloudWatchMonitor:
    """Monitor CloudWatch metrics for autoscaling behavior"""

    def __init__(self, config: LoadTestConfig):
        self.config = config
        self.cloudwatch = boto3.client("cloudwatch", region_name=config.aws_region)
        self.autoscaling = boto3.client("autoscaling", region_name=config.aws_region)
        self.ecs = boto3.client("ecs", region_name=config.aws_region)
        self.monitoring = True
        self.metrics_data = []

    def get_asg_metrics(self) -> Dict:
        """Get current Auto Scaling Group metrics"""
        try:
            response = self.autoscaling.describe_auto_scaling_groups(
                AutoScalingGroupNames=[self.config.asg_name]
            )

            if not response["AutoScalingGroups"]:
                return {}

            asg = response["AutoScalingGroups"][0]

            return {
                "desired_capacity": asg["DesiredCapacity"],
                "min_size": asg["MinSize"],
                "max_size": asg["MaxSize"],
                "instances": len(asg["Instances"]),
                "in_service": len(
                    [i for i in asg["Instances"] if i["LifecycleState"] == "InService"]
                ),
                "pending": len(
                    [i for i in asg["Instances"] if i["LifecycleState"] == "Pending"]
                ),
                "terminating": len(
                    [
                        i
                        for i in asg["Instances"]
                        if i["LifecycleState"] == "Terminating"
                    ]
                ),
            }
        except Exception as e:
            logging.error(f"Error getting ASG metrics: {e}")
            return {}

    def get_ecs_metrics(self) -> Dict:
        """Get current ECS service metrics"""
        try:
            end_time = datetime.utcnow()
            start_time = end_time - timedelta(minutes=5)

            # Get CPU utilization
            cpu_response = self.cloudwatch.get_metric_statistics(
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

            # Get Memory utilization
            memory_response = self.cloudwatch.get_metric_statistics(
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

            cpu_latest = 0
            if cpu_response["Datapoints"]:
                cpu_latest = sorted(
                    cpu_response["Datapoints"], key=lambda x: x["Timestamp"]
                )[-1]["Average"]

            memory_latest = 0
            if memory_response["Datapoints"]:
                memory_latest = sorted(
                    memory_response["Datapoints"], key=lambda x: x["Timestamp"]
                )[-1]["Average"]

            return {
                "cpu_utilization": round(cpu_latest, 2),
                "memory_utilization": round(memory_latest, 2),
            }
        except Exception as e:
            logging.error(f"Error getting ECS metrics: {e}")
            return {"cpu_utilization": 0, "memory_utilization": 0}

    def get_scaling_activities(self) -> List[Dict]:
        """Get recent scaling activities"""
        try:
            response = self.autoscaling.describe_scaling_activities(
                AutoScalingGroupName=self.config.asg_name, MaxRecords=10
            )

            activities = []
            for activity in response["Activities"]:
                activities.append(
                    {
                        "activity_id": activity["ActivityId"],
                        "description": activity["Description"],
                        "cause": activity["Cause"],
                        "start_time": activity["StartTime"],
                        "status_code": activity["StatusCode"],
                        "progress": activity.get("Progress", 0),
                    }
                )

            return activities
        except Exception as e:
            logging.error(f"Error getting scaling activities: {e}")
            return []

    def get_task_instance_mapping(self) -> Dict[str, List[str]]:
        """Get mapping of EC2 instances to running ECS tasks"""
        try:
            # List all tasks in the service
            task_arns = []
            paginator = self.ecs.get_paginator('list_tasks')
            for page in paginator.paginate(
                cluster=self.config.cluster_name,
                serviceName=self.config.service_name,
                desiredStatus='RUNNING'
            ):
                task_arns.extend(page['taskArns'])

            if not task_arns:
                return {}

            # Describe tasks to get container instance ARNs
            tasks_response = self.ecs.describe_tasks(
                cluster=self.config.cluster_name,
                tasks=task_arns
            )

            # Get container instance details
            container_instance_arns = list(set(
                task['containerInstanceArn']
                for task in tasks_response['tasks']
                if 'containerInstanceArn' in task
            ))

            if not container_instance_arns:
                return {}

            instances_response = self.ecs.describe_container_instances(
                cluster=self.config.cluster_name,
                containerInstances=container_instance_arns
            )

            # Build mapping: instance_id -> [task_ids]
            container_to_instance = {
                ci['containerInstanceArn']: ci['ec2InstanceId']
                for ci in instances_response['containerInstances']
            }

            instance_tasks = {}
            for task in tasks_response['tasks']:
                if 'containerInstanceArn' in task:
                    instance_id = container_to_instance.get(
                        task['containerInstanceArn']
                    )
                    if instance_id:
                        task_id = task['taskArn'].split('/')[-1]
                        if instance_id not in instance_tasks:
                            instance_tasks[instance_id] = []
                        instance_tasks[instance_id].append(task_id)

            return instance_tasks

        except Exception as e:
            logging.error(f"Error getting task-instance mapping: {e}")
            return {}

    def monitor_metrics(self):
        """Continuously monitor metrics during load test"""
        logging.info(" Starting CloudWatch metrics monitoring...")

        while self.monitoring:
            try:
                timestamp = datetime.now()
                asg_metrics = self.get_asg_metrics()
                ecs_metrics = self.get_ecs_metrics()

                current_metrics = {
                    "timestamp": timestamp.isoformat(),
                    "asg": asg_metrics,
                    "ecs": ecs_metrics,
                }

                self.metrics_data.append(current_metrics)

                # Log current status with task distribution
                if asg_metrics and ecs_metrics:
                    # Get task-instance mapping every 5th iteration to reduce API calls
                    task_mapping = {}
                    if len(self.metrics_data) % 5 == 0:
                        task_mapping = self.get_task_instance_mapping()

                    logging.info(
                        f" Metrics - CPU: {ecs_metrics['cpu_utilization']}%, "
                        f"Memory: {ecs_metrics['memory_utilization']}%, "
                        f"Instances: {asg_metrics['in_service']}/"
                        f"{asg_metrics['desired_capacity']}"
                    )

                    # Display task distribution if we have multiple instances
                    if task_mapping and len(task_mapping) > 1:
                        for instance_id, tasks in task_mapping.items():
                            logging.info(
                                f"   Instance {instance_id[-8:]}: "
                                f"{len(tasks)} tasks running"
                            )

                    # Check for scaling thresholds
                    if (
                        ecs_metrics["cpu_utilization"]
                        > self.config.target_cpu_threshold
                    ):
                        logging.warning(
                            f"CPU threshold exceeded: "
                            f"{ecs_metrics['cpu_utilization']}% > "
                            f"{self.config.target_cpu_threshold}%"
                        )

                    if (
                        ecs_metrics["memory_utilization"]
                        > self.config.target_memory_threshold
                    ):
                        logging.warning(
                            f"Memory threshold exceeded: "
                            f"{ecs_metrics['memory_utilization']}% > "
                            f"{self.config.target_memory_threshold}%"
                        )

                time.sleep(self.config.monitoring_interval)

            except Exception as e:
                logging.error(f"Error in monitoring loop: {e}")
                time.sleep(5)

    def stop_monitoring(self):
        """Stop metrics monitoring"""
        self.monitoring = False
        logging.info("Stopped CloudWatch metrics monitoring")


class WorkflowLoadGenerator:
    """Generate load through workflow submissions"""

    def __init__(self, config: LoadTestConfig):
        self.config = config
        self.tokens = {}
        self.submitted_workflows = []
        self.completed_workflows = []
        self.continuous_submission = False
        self.submission_interval = 30  # Submit new workflows every 30 seconds
        self.shared_workspace_id = None  # Reuse same workspace for all workflows

    def setup_authentication(self):
        """Setup JWT authentication tokens"""
        if self.config.skip_token_gen:
            try:
                with open("tokens.json", "r") as f:
                    self.tokens = json.load(f)
                logging.info("Loaded existing JWT tokens from tokens.json")
                return True
            except FileNotFoundError:
                logging.warning("tokens.json not found, generating new tokens...")

        logging.info("🔑 Generating JWT tokens for load testing...")

        try:
            # Use existing token generation if available
            if "generate_jwt_tokens" in globals():
                token_data = generate_jwt_tokens(
                    environment="cloud", api_url=self.config.api_url
                )
                if token_data:
                    self.tokens = token_data
                    logging.info("Successfully generated JWT tokens")
                    return True

            # Fallback to manual token generation
            logging.warning("Using fallback token generation method")
            return self._generate_fallback_tokens()

        except Exception as e:
            logging.error(f"Failed to generate tokens: {e}")
            return False

    def _generate_fallback_tokens(self) -> bool:
        """Fallback method for token generation"""
        # This would implement a basic token generation
        # For now, return False to indicate authentication setup failed
        logging.error("Fallback token generation not implemented")
        return False

    def create_workspace(self, user_token: str) -> Optional[int]:
        """Create a new workspace for load testing"""
        try:
            headers = {
                "Authorization": f"Bearer {user_token}",
                "Content-Type": "application/json",
            }

            workspace_name = f"load_test_workspace_{int(time.time())}"
            response = requests.post(
                f"{self.config.api_url}/workspace",
                json={"name": workspace_name},
                headers=headers,
                timeout=30,
            )

            if response.status_code == 200:
                workspace = response.json()
                workspace_id = workspace.get("id")
                logging.info(f"Created workspace: {workspace_id} ({workspace_name})")
                return workspace_id
            else:
                logging.error(
                    f"Workspace creation failed: {response.status_code} - {response.text}"
                )

        except Exception as e:
            logging.error(f"Error creating workspace: {e}")

        return None

    def import_sample_data(self, workspace_id: int, user_token: str) -> bool:
        """Import tutorial sample data into workspace"""
        try:
            headers = {
                "Authorization": f"Bearer {user_token}",
            }

            response = requests.get(
                f"{self.config.api_url}/workflow/sample_data/{workspace_id}/tutorial",
                headers=headers,
                timeout=120,  # Longer timeout for data import
            )

            if response.status_code == 200:
                logging.info(f"Sample data imported for workspace {workspace_id}")
                return True
            else:
                logging.error(
                    f"Sample data import failed: {response.status_code} - {response.text}"
                )

        except Exception as e:
            logging.error(f"Error importing sample data: {e}")

        return False

    def load_tutorial_workflow(self) -> Dict:
        """Load Tutorial 1 workflow structure from sample data"""
        # Path relative to scripts directory
        tutorial_workflow_path = os.path.join(
            os.path.dirname(__file__),
            "../../sample_data/tutorial/output/tutorial1/workflow.yaml"
        )
        tutorial_workflow_path = os.path.abspath(tutorial_workflow_path)

        try:
            import yaml
            with open(tutorial_workflow_path, 'r') as f:
                workflow_data = yaml.safe_load(f)

            # Add required fields if missing
            if 'name' not in workflow_data:
                workflow_data['name'] = 'tutorial1'
            if 'snakemakeParam' not in workflow_data:
                workflow_data['snakemakeParam'] = {}
            if 'nwbParam' not in workflow_data:
                workflow_data['nwbParam'] = {}
            if 'forceRunList' not in workflow_data:
                workflow_data['forceRunList'] = []

            logging.info(f"Loaded tutorial workflow from {tutorial_workflow_path}")
            return workflow_data
        except Exception as e:
            logging.error(f"Failed to load tutorial workflow: {e}")
            raise RuntimeError(f"Cannot proceed without tutorial workflow: {e}")

    def submit_workflow(self, workspace_id: int, workflow_data: Dict, user_token: str) -> Optional[str]:
        """Submit a workflow run to a workspace"""
        try:
            headers = {
                "Authorization": f"Bearer {user_token}",
                "Content-Type": "application/json",
            }

            response = requests.post(
                f"{self.config.api_url}/run/{workspace_id}",
                json=workflow_data,
                headers=headers,
                timeout=60,
            )

            if response.status_code == 200:
                unique_id = response.text.strip('"')  # FastAPI returns string in quotes
                if unique_id:
                    self.submitted_workflows.append(
                        {
                            "unique_id": unique_id,
                            "workspace_id": workspace_id,
                            "name": workflow_data.get("name", "tutorial1"),
                            "submitted_at": datetime.now(),
                        }
                    )
                    return unique_id
            else:
                logging.error(
                    f"Workflow submission failed: "
                    f"{response.status_code} - {response.text}"
                )

        except Exception as e:
            logging.error(f"Error submitting workflow: {e}")

        return None

    def generate_load(self):
        """Generate load through concurrent workflow submissions"""
        if not self.tokens:
            logging.error("No authentication tokens available")
            return False

        # Use free user token only
        user_token = self.tokens.get("free_token")
        if not user_token:
            logging.error("Free user token not found in token data")
            return False

        logging.info("Using free user token for all load testing")

        # Create a single shared workspace if not already created
        if not self.shared_workspace_id:
            logging.info("Creating shared workspace for all workflows...")
            self.shared_workspace_id = self.create_workspace(user_token)
            if not self.shared_workspace_id:
                logging.error("Failed to create shared workspace")
                return False

            logging.info(f"Created shared workspace: {self.shared_workspace_id}")

            # Import sample data once
            if not self.import_sample_data(self.shared_workspace_id, user_token):
                logging.error("Failed to import sample data")
                return False

            logging.info("Sample data imported successfully")

        # Load Tutorial 1 workflow structure
        tutorial_workflow = self.load_tutorial_workflow()

        logging.info(
            f"Starting load generation with "
            f"{self.config.concurrent_workflows} concurrent workflows..."
        )

        # Keep submitting workflows until we reach 3 instances
        target_instances = 3
        max_workflows = 100  # Safety limit
        submitted_count = 0
        workflow_counter = 0

        while workflow_counter < max_workflows:
            # Check current instance count
            asg_metrics = self.get_asg_metrics()
            current_instances = asg_metrics.get("in_service", 0)

            if current_instances >= target_instances:
                logging.info(
                    f"✅ Target of {target_instances} instances reached! "
                    f"(Current: {current_instances})"
                )
                break

            # Submit batch of workflows
            batch_size = min(
                self.config.concurrent_workflows,
                max_workflows - workflow_counter
            )

            logging.info(
                f"Submitting batch of {batch_size} workflows "
                f"(instances: {current_instances}/{target_instances})..."
            )

            with concurrent.futures.ThreadPoolExecutor(
                max_workers=batch_size
            ) as executor:
                futures = [
                    executor.submit(
                        self._submit_workflow_to_shared_workspace,
                        workflow_counter + i,
                        tutorial_workflow,
                        user_token
                    )
                    for i in range(batch_size)
                ]

                # Wait for all submissions to complete
                batch_submitted = sum(
                    1 for future in concurrent.futures.as_completed(futures)
                    if future.result()
                )
                submitted_count += batch_submitted
                workflow_counter += batch_size

            logging.info(
                f"Batch complete: {batch_submitted}/{batch_size} submitted "
                f"(total: {submitted_count}/{workflow_counter})"
            )

            # Brief pause before checking again
            time.sleep(5)

        logging.info(
            f"Load generation complete: {submitted_count}/{workflow_counter} "
            f"workflows submitted"
        )
        return submitted_count > 0

    def _submit_workflow_to_shared_workspace(
        self, job_index: int, workflow_template: Dict, user_token: str
    ) -> bool:
        """Submit a workflow to the shared workspace with retry logic"""
        max_retries = 3
        retry_delay = 10  # seconds

        for attempt in range(max_retries):
            try:
                workflow_data = workflow_template.copy()
                workflow_data["name"] = f"load_test_run_{job_index}_{int(time.time())}"

                unique_id = self.submit_workflow(
                    self.shared_workspace_id, workflow_data, user_token
                )
                if unique_id:
                    if attempt > 0:
                        logging.info(
                            f"Job {job_index}: Successfully submitted workflow "
                            f"{unique_id} (attempt {attempt + 1})"
                        )
                    else:
                        logging.info(
                            f"Job {job_index}: Successfully submitted workflow "
                            f"{unique_id}"
                        )
                    return True
                else:
                    if attempt < max_retries - 1:
                        logging.warning(
                            f"Job {job_index}: Failed to submit workflow, "
                            f"retrying in {retry_delay}s... "
                            f"(attempt {attempt + 1}/{max_retries})"
                        )
                        time.sleep(retry_delay)
                    else:
                        logging.error(
                            f"Job {job_index}: Failed to submit workflow after "
                            f"{max_retries} attempts"
                        )
                        return False

            except Exception as e:
                if attempt < max_retries - 1:
                    logging.warning(
                        f"Job {job_index}: Error in workflow submission: {e}, "
                        f"retrying in {retry_delay}s... "
                        f"(attempt {attempt + 1}/{max_retries})"
                    )
                    time.sleep(retry_delay)
                else:
                    logging.error(
                        f"Job {job_index}: Error in workflow submission after "
                        f"{max_retries} attempts: {e}"
                    )
                    return False

        return False

    def _submit_single_workflow_job(self, job_index: int, workflow_template: Dict, user_token: str) -> bool:
        """Create workspace, import data, and submit workflow for a single job (legacy method)"""
        try:
            # Step 1: Create workspace
            workspace_id = self.create_workspace(user_token)
            if not workspace_id:
                logging.error(f"Job {job_index}: Failed to create workspace")
                return False

            # Step 2: Import sample data
            if not self.import_sample_data(workspace_id, user_token):
                logging.error(f"Job {job_index}: Failed to import sample data")
                return False

            # Step 3: Submit workflow
            workflow_data = workflow_template.copy()
            workflow_data["name"] = f"load_test_run_{job_index}_{int(time.time())}"

            unique_id = self.submit_workflow(workspace_id, workflow_data, user_token)
            if unique_id:
                logging.info(
                    f"Job {job_index}: Successfully submitted workflow {unique_id} "
                    f"to workspace {workspace_id}"
                )
                return True
            else:
                logging.error(f"Job {job_index}: Failed to submit workflow")
                return False

        except Exception as e:
            logging.error(f"Job {job_index}: Error in workflow submission: {e}")
            return False

    def generate_continuous_load(self, duration_seconds: int):
        """Continuously submit workflows to maintain load"""
        if not self.tokens:
            logging.error("No authentication tokens available")
            return

        user_token = self.tokens.get("free_token")
        if not user_token:
            logging.error("Free user token not found")
            return

        if not self.shared_workspace_id:
            logging.error("Shared workspace not created. Run generate_load() first.")
            return

        tutorial_workflow = self.load_tutorial_workflow()
        self.continuous_submission = True

        logging.info(
            f"Starting continuous workflow submission for {duration_seconds}s"
        )
        logging.info(
            f"Will submit {self.config.concurrent_workflows} workflows every "
            f"{self.submission_interval}s to workspace {self.shared_workspace_id}"
        )

        start_time = time.time()
        submission_round = 0

        while self.continuous_submission and (time.time() - start_time) < duration_seconds:
            submission_round += 1
            logging.info(
                f"Submission round {submission_round}: "
                f"Submitting {self.config.concurrent_workflows} workflows"
            )

            # Submit a batch of workflows to the shared workspace
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(10, self.config.concurrent_workflows)
            ) as executor:
                futures = []
                base_index = submission_round * self.config.concurrent_workflows

                for i in range(self.config.concurrent_workflows):
                    future = executor.submit(
                        self._submit_workflow_to_shared_workspace,
                        base_index + i,
                        tutorial_workflow,
                        user_token
                    )
                    futures.append(future)
                    time.sleep(0.3)  # Small stagger

                # Wait for batch to complete
                submitted = sum(1 for f in concurrent.futures.as_completed(futures) if f.result())
                logging.info(
                    f"Round {submission_round}: {submitted}/"
                    f"{self.config.concurrent_workflows} workflows submitted"
                )

            # Wait before next batch
            if self.continuous_submission:
                time.sleep(self.submission_interval)

        logging.info(
            f"Continuous submission stopped after {submission_round} rounds"
        )

    def stop_continuous_load(self):
        """Stop continuous workflow submission"""
        self.continuous_submission = False


class LoadTestAnalyzer:
    """Analyze load test results and autoscaling behavior"""

    def __init__(
        self,
        config: LoadTestConfig,
        monitor: CloudWatchMonitor,
        generator: WorkflowLoadGenerator,
    ):
        self.config = config
        self.monitor = monitor
        self.generator = generator

    def analyze_scaling_behavior(self) -> Dict:
        """Analyze autoscaling behavior during the test"""
        if not self.monitor.metrics_data:
            return {"error": "No metrics data available for analysis"}

        analysis = {
            "test_duration": self.config.duration,
            "total_metrics_points": len(self.monitor.metrics_data),
            "workflows_submitted": len(self.generator.submitted_workflows),
            "scaling_events": [],
            "threshold_breaches": {"cpu": [], "memory": []},
            "peak_utilization": {"cpu": 0, "memory": 0},
            "scaling_responsiveness": {},
            "final_capacity": {},
        }

        # Analyze metrics timeline
        for i, metric in enumerate(self.monitor.metrics_data):
            timestamp = metric["timestamp"]
            ecs = metric.get("ecs", {})
            asg = metric.get("asg", {})

            cpu_util = ecs.get("cpu_utilization", 0)
            memory_util = ecs.get("memory_utilization", 0)

            # Track peak utilization
            analysis["peak_utilization"]["cpu"] = max(
                analysis["peak_utilization"]["cpu"], cpu_util
            )
            analysis["peak_utilization"]["memory"] = max(
                analysis["peak_utilization"]["memory"], memory_util
            )

            # Detect threshold breaches
            if cpu_util > self.config.target_cpu_threshold:
                analysis["threshold_breaches"]["cpu"].append(
                    {
                        "timestamp": timestamp,
                        "value": cpu_util,
                        "capacity": asg.get("desired_capacity", 0),
                    }
                )

            if memory_util > self.config.target_memory_threshold:
                analysis["threshold_breaches"]["memory"].append(
                    {
                        "timestamp": timestamp,
                        "value": memory_util,
                        "capacity": asg.get("desired_capacity", 0),
                    }
                )

            # Detect capacity changes (scaling events)
            if i > 0:
                prev_capacity = (
                    self.monitor.metrics_data[i - 1]
                    .get("asg", {})
                    .get("desired_capacity", 0)
                )
                current_capacity = asg.get("desired_capacity", 0)

                if current_capacity != prev_capacity:
                    analysis["scaling_events"].append(
                        {
                            "timestamp": timestamp,
                            "from_capacity": prev_capacity,
                            "to_capacity": current_capacity,
                            "direction": "scale_up"
                            if current_capacity > prev_capacity
                            else "scale_down",
                            "trigger_cpu": cpu_util,
                            "trigger_memory": memory_util,
                        }
                    )

        # Calculate scaling responsiveness
        if analysis["threshold_breaches"]["cpu"] and analysis["scaling_events"]:
            first_cpu_breach = datetime.fromisoformat(
                analysis["threshold_breaches"]["cpu"][0]["timestamp"]
            )
            first_scale_event = None

            for event in analysis["scaling_events"]:
                if event["direction"] == "scale_up":
                    first_scale_event = datetime.fromisoformat(event["timestamp"])
                    break

            if first_scale_event:
                response_time = (first_scale_event - first_cpu_breach).total_seconds()
                analysis["scaling_responsiveness"][
                    "cpu_response_time_seconds"
                ] = response_time

        # Final state
        if self.monitor.metrics_data:
            final_metrics = self.monitor.metrics_data[-1]
            analysis["final_capacity"] = final_metrics.get("asg", {})

        return analysis

    def generate_report(self, analysis: Dict) -> str:
        """Generate a comprehensive test report"""
        report = []
        report.append("=" * 80)
        report.append(" OPTINIST AUTOSCALING LOAD TEST REPORT")
        report.append("=" * 80)
        report.append("")

        # Test configuration
        report.append("TEST CONFIGURATION:")
        report.append(f"   API URL: {self.config.api_url}")
        report.append(f"   Duration: {self.config.duration} seconds")
        report.append(f"   Concurrent Workflows: {self.config.concurrent_workflows}")
        if self.config.cpu_only:
            test_type = "CPU only"
        elif self.config.memory_only:
            test_type = "Memory only"
        else:
            test_type = "Mixed load"
        report.append(f"Test Type: {test_type}")
        report.append(f"   CPU Threshold: {self.config.target_cpu_threshold}%")
        report.append(f"   Memory Threshold: {self.config.target_memory_threshold}%")
        report.append("")

        # Workflow submission results
        report.append(" WORKFLOW SUBMISSION:")
        report.append(f"   Workflows Submitted: {analysis['workflows_submitted']}")
        submitted = analysis["workflows_submitted"]
        submission_rate = submitted / self.config.concurrent_workflows * 100
        report.append(f"   Submission Success Rate: " f"{submission_rate:.1f}%")
        report.append("")

        # Peak utilization
        report.append(" PEAK UTILIZATION:")
        report.append(f"   Peak CPU: {analysis['peak_utilization']['cpu']:.2f}%")
        report.append(f"   Peak Memory: {analysis['peak_utilization']['memory']:.2f}%")
        report.append("")

        # Threshold breaches
        report.append(" THRESHOLD BREACHES:")
        cpu_breaches = len(analysis["threshold_breaches"]["cpu"])
        memory_breaches = len(analysis["threshold_breaches"]["memory"])
        report.append(f"   CPU Threshold Breaches: {cpu_breaches}")
        report.append(f"   Memory Threshold Breaches: {memory_breaches}")

        if cpu_breaches > 0:
            max_cpu = max(b["value"] for b in analysis["threshold_breaches"]["cpu"])
            report.append(f"   Max CPU During Breach: {max_cpu:.2f}%")

        if memory_breaches > 0:
            max_memory = max(
                b["value"] for b in analysis["threshold_breaches"]["memory"]
            )
            report.append(f"   Max Memory During Breach: {max_memory:.2f}%")
        report.append("")

        # Scaling events
        report.append("SCALING EVENTS:")
        scaling_events = analysis["scaling_events"]
        report.append(f"   Total Scaling Events: {len(scaling_events)}")

        scale_ups = [e for e in scaling_events if e["direction"] == "scale_up"]
        scale_downs = [e for e in scaling_events if e["direction"] == "scale_down"]

        report.append(f"   Scale-up Events: {len(scale_ups)}")
        report.append(f"   Scale-down Events: {len(scale_downs)}")

        for event in scaling_events:
            direction_indicator = "UP" if event["direction"] == "scale_up" else "DOWN"
            report.append(
                f"{direction_indicator}: {event['from_capacity']} → "
                f"{event['to_capacity']} instances "
                f"(CPU: {event['trigger_cpu']:.1f}%, "
                f"Memory: {event['trigger_memory']:.1f}%)"
            )
        report.append("")

        # Responsiveness analysis
        report.append("SCALING RESPONSIVENESS:")
        responsiveness = analysis["scaling_responsiveness"]
        if "cpu_response_time_seconds" in responsiveness:
            response_time = responsiveness["cpu_response_time_seconds"]
            report.append(f"   CPU Threshold → Scale-up: {response_time:.1f} seconds")

            if response_time <= 300:  # Expected CloudWatch alarm evaluation period
                report.append("   Scaling response time within expected range (≤300s)")
            else:
                report.append(
                    "    Scaling response time exceeded expected range (>300s)"
                )
        else:
            report.append("    No scaling response detected")
        report.append("")

        # Final state
        report.append("🏁 FINAL STATE:")
        final_capacity = analysis["final_capacity"]
        if final_capacity:
            report.append(
                f"Final Desired Capacity: "
                f"{final_capacity.get('desired_capacity', 'Unknown')}"
            )
            report.append(
                f"Final In-Service Instances: "
                f"{final_capacity.get('in_service', 'Unknown')}"
            )
            report.append(f"   Pending Instances: {final_capacity.get('pending', 0)}")
            report.append(
                f"Terminating Instances: {final_capacity.get('terminating', 0)}"
            )
        report.append("")

        # Recommendations
        report.append(" RECOMMENDATIONS:")

        if cpu_breaches == 0 and memory_breaches == 0:
            report.append(
                "    No thresholds breached - consider increasing load "
                "or decreasing thresholds"
            )

        if len(scale_ups) == 0 and (cpu_breaches > 0 or memory_breaches > 0):
            report.append(
                "    Thresholds breached but no scaling occurred - "
                "check CloudWatch alarms"
            )

        if len(scale_ups) > 0 and "cpu_response_time_seconds" in responsiveness:
            if responsiveness["cpu_response_time_seconds"] > 600:
                report.append(
                    "    Slow scaling response - consider optimizing "
                    "alarm evaluation periods"
                )

        if (
            analysis["peak_utilization"]["cpu"] < 50
            and analysis["peak_utilization"]["memory"] < 50
        ):
            report.append(
                "    Low resource utilization - consider more " "intensive workloads"
            )

        report.append("")
        report.append("=" * 80)

        return "\n".join(report)


def main():
    """Main load test execution"""
    parser = argparse.ArgumentParser(description="OptiNiSt Autoscaling Load Test")

    # Infrastructure configuration
    parser.add_argument(
        "--terraform-dir",
        type=str,
        default="../config/terraform",
        help="Path to Terraform directory (default: ../config/terraform)",
    )
    parser.add_argument(
        "--api-url",
        type=str,
        help="API URL (auto-detected from Terraform if not provided)",
    )

    # Test configuration
    parser.add_argument(
        "--duration",
        type=int,
        default=1800,
        help="Test duration in seconds (default: 1800 = 30 minutes)",
    )
    parser.add_argument(
        "--concurrent-workflows",
        type=int,
        default=30,
        help="Number of concurrent workflows to submit (default: 30)",
    )

    # Load test types
    parser.add_argument(
        "--cpu-only", action="store_true", help="Run CPU stress test only"
    )
    parser.add_argument(
        "--memory-only", action="store_true", help="Run memory stress test only"
    )

    # Autoscaling thresholds
    parser.add_argument(
        "--target-cpu",
        type=float,
        default=60.0,
        help="Target CPU threshold for scaling (default: 60.0)",
    )
    parser.add_argument(
        "--target-memory",
        type=float,
        default=80.0,
        help="Target memory threshold for scaling (default: 80.0)",
    )
    parser.add_argument(
        "--cooldown",
        type=int,
        default=300,
        help="Expected cooldown period in seconds (default: 300)",
    )

    # Monitoring configuration
    parser.add_argument(
        "--monitoring-interval",
        type=int,
        default=30,
        help="Metrics monitoring interval in seconds (default: 30)",
    )
    parser.add_argument(
        "--aws-region",
        type=str,
        default="ap-northeast-1",
        help="AWS region (default: ap-northeast-1)",
    )
    parser.add_argument(
        "--asg-name",
        type=str,
        help="Auto Scaling Group name (auto-detected from Terraform if not provided)",
    )
    parser.add_argument(
        "--cluster-name",
        type=str,
        help="ECS cluster name (auto-detected from Terraform if not provided)",
    )
    parser.add_argument(
        "--service-name",
        type=str,
        help="ECS service name (auto-detected from Terraform if not provided)",
    )

    # Output configuration
    parser.add_argument(
        "--output-file",
        type=str,
        help="Output file for detailed results (default: auto-generated)",
    )
    parser.add_argument(
        "--skip-token-gen",
        action="store_true",
        help="Skip token generation and use existing tokens.json",
    )

    args = parser.parse_args()

    # Setup logging
    log_level = logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    # Create configuration
    config = LoadTestConfig(args)

    # Auto-generate output file if not specified
    if not config.output_file:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        test_type = (
            "cpu" if config.cpu_only else "memory" if config.memory_only else "mixed"
        )
        config.output_file = f"load_test_{test_type}_{timestamp}.json"

    logging.info(" Starting OptiNiSt Autoscaling Load Test")
    logging.info(
        f"Configuration: {config.duration}s duration, "
        f"{config.concurrent_workflows} workflows"
    )
    logging.info(f"API URL: {config.api_url}")
    logging.info(f"ASG: {config.asg_name}")
    logging.info(f"ECS Cluster: {config.cluster_name}")
    logging.info(f"ECS Service: {config.service_name}")

    # Initialize components
    monitor = CloudWatchMonitor(config)
    generator = WorkflowLoadGenerator(config)
    analyzer = LoadTestAnalyzer(config, monitor, generator)

    try:
        # Setup authentication
        if not generator.setup_authentication():
            logging.error(
                " Failed to setup authentication - cannot proceed with load test"
            )
            sys.exit(1)

        # Start metrics monitoring in background
        monitor_thread = threading.Thread(target=monitor.monitor_metrics, daemon=True)
        monitor_thread.start()

        # Wait a moment for initial metrics
        time.sleep(5)

        # Submit all workflows at once
        logging.info(
            f"Submitting {config.concurrent_workflows} workflows to run simultaneously..."
        )
        load_success = generator.generate_load()
        if not load_success:
            logging.error(" Failed to generate load - test incomplete")

        # Continue monitoring for the specified duration
        logging.info(
            f"Monitoring autoscaling behavior for {config.duration} seconds..."
        )
        logging.info(
            f"Workflows are running... Watch for CPU/Memory to trigger autoscaling"
        )
        time.sleep(config.duration)

        # Stop monitoring
        monitor.stop_monitoring()

        # Analyze results
        logging.info(" Analyzing test results...")
        analysis = analyzer.analyze_scaling_behavior()

        # Generate and display report
        report = analyzer.generate_report(analysis)
        print("\n" + report)

        # Save detailed results
        detailed_results = {
            "config": {
                "api_url": config.api_url,
                "duration": config.duration,
                "concurrent_workflows": config.concurrent_workflows,
                "asg_name": config.asg_name,
                "cluster_name": config.cluster_name,
                "service_name": config.service_name,
                "test_type": "cpu"
                if config.cpu_only
                else "memory"
                if config.memory_only
                else "mixed",
            },
            "analysis": analysis,
            "raw_metrics": monitor.metrics_data,
            "submitted_workflows": generator.submitted_workflows,
            "report": report,
        }

        with open(config.output_file, "w") as f:
            json.dump(detailed_results, f, indent=2, default=str)

        logging.info(f"Detailed results saved to: {config.output_file}")

        # Test success criteria
        success_criteria = []
        if analysis["workflows_submitted"] >= config.concurrent_workflows * 0.8:
            success_criteria.append("Workflow submission success")
        else:
            success_criteria.append(" Workflow submission failed")

        if (
            analysis["peak_utilization"]["cpu"] > config.target_cpu_threshold
            or analysis["peak_utilization"]["memory"] > config.target_memory_threshold
        ):
            success_criteria.append("Resource thresholds reached")
        else:
            success_criteria.append(" Resource thresholds not reached")

        if len(analysis["scaling_events"]) > 0:
            success_criteria.append("Autoscaling events detected")
        else:
            success_criteria.append(" No autoscaling events detected")

        logging.info("🏆 Test Success Criteria:")
        for criteria in success_criteria:
            logging.info(f"   {criteria}")

        # Exit with appropriate code
        failed_criteria = [c for c in success_criteria if c.startswith("")]
        if failed_criteria:
            logging.warning(
                " Some test criteria failed - review configuration and try again"
            )
            sys.exit(1)
        else:
            logging.info(" Load test completed successfully!")
            sys.exit(0)

    except KeyboardInterrupt:
        logging.info(" Load test interrupted by user")
        monitor.stop_monitoring()
        sys.exit(130)
    except Exception as e:
        logging.error(f" Load test failed: {e}")
        monitor.stop_monitoring()
        sys.exit(1)


if __name__ == "__main__":
    main()
