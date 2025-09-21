#!/usr/bin/env python3
"""
OptiNiSt Autoscaling Load Test

RUNTIME ENVIRONMENT:
⚠️ Best run on cloud (requires AWS credentials and infrastructure)
⚠️ Can run locally with --mock flag (limited functionality)
✅ Requires AWS CLI configured or IAM role with appropriate permissions

This script tests autoscaling behavior by generating controlled load to trigger
CPU and memory thresholds, then validates that the Auto Scaling Group responds
correctly according to the configured CloudWatch alarms.

Autoscaling Configuration:
- Scale-up: CPU >60% or Memory >80% for 3 evaluation periods
- Scale-down: CPU <20% and Memory <10% for 3 evaluation periods
- Cooldown: 300 seconds
- Health check grace period: 180 seconds

Usage:
    python load_test.py                           # Full test with default settings
    python load_test.py --cpu-only               # CPU stress test only
    python load_test.py --memory-only            # Memory stress test only
    python load_test.py --environment cloud      # Test cloud environment
    python load_test.py --duration 600           # 10-minute test duration
    python load_test.py --concurrent-workflows 10 # Custom workflow count

Features:
- CPU stress testing via compute-intensive workflows
- Memory stress testing via large data processing
- Real-time CloudWatch metrics monitoring
- Autoscaling behavior validation
- Detailed performance analysis and reporting
"""

import argparse
import concurrent.futures
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import boto3
import requests

# Add the current directory to Python path for imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from get_jwt_tokens import generate_jwt_tokens
except ImportError as e:
    print(f"Warning: Could not import get_jwt_tokens module: {e}")
    print("Token generation functionality may be limited")


class LoadTestConfig:
    """Configuration for load testing parameters"""

    def __init__(self, args):
        self.environment = args.environment
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
        self.asg_name = args.asg_name
        self.cluster_name = args.cluster_name
        self.service_name = args.service_name
        self.output_file = args.output_file
        self.skip_token_gen = args.skip_token_gen

        # Auto-detect API URL for local environment
        if self.environment == "local" and not self.api_url:
            self.api_url = "http://localhost:8000"


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

    def monitor_metrics(self):
        """Continuously monitor metrics during load test"""
        logging.info("🔍 Starting CloudWatch metrics monitoring...")

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

                # Log current status
                if asg_metrics and ecs_metrics:
                    logging.info(
                        f"📊 Metrics - CPU: {ecs_metrics['cpu_utilization']}%, "
                        f"Memory: {ecs_metrics['memory_utilization']}%, "
                        f"Instances: {asg_metrics['in_service']}/"
                        f"{asg_metrics['desired_capacity']}"
                    )

                    # Check for scaling thresholds
                    if (
                        ecs_metrics["cpu_utilization"]
                        > self.config.target_cpu_threshold
                    ):
                        logging.warning(
                            f"🔥 CPU threshold exceeded: "
                            f"{ecs_metrics['cpu_utilization']}% > "
                            f"{self.config.target_cpu_threshold}%"
                        )

                    if (
                        ecs_metrics["memory_utilization"]
                        > self.config.target_memory_threshold
                    ):
                        logging.warning(
                            f"💾 Memory threshold exceeded: "
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
        logging.info("⏹️ Stopped CloudWatch metrics monitoring")


class WorkflowLoadGenerator:
    """Generate load through workflow submissions"""

    def __init__(self, config: LoadTestConfig):
        self.config = config
        self.tokens = {}
        self.submitted_workflows = []
        self.completed_workflows = []

    def setup_authentication(self):
        """Setup JWT authentication tokens"""
        if self.config.skip_token_gen:
            try:
                with open("tokens.json", "r") as f:
                    self.tokens = json.load(f)
                logging.info("✅ Loaded existing JWT tokens from tokens.json")
                return True
            except FileNotFoundError:
                logging.warning("⚠️ tokens.json not found, generating new tokens...")

        logging.info("🔑 Generating JWT tokens for load testing...")

        try:
            # Use existing token generation if available
            if "generate_jwt_tokens" in globals():
                token_data = generate_jwt_tokens(
                    environment=self.config.environment, api_url=self.config.api_url
                )
                if token_data:
                    self.tokens = token_data
                    logging.info("✅ Successfully generated JWT tokens")
                    return True

            # Fallback to manual token generation
            logging.warning("⚠️ Using fallback token generation method")
            return self._generate_fallback_tokens()

        except Exception as e:
            logging.error(f"❌ Failed to generate tokens: {e}")
            return False

    def _generate_fallback_tokens(self) -> bool:
        """Fallback method for token generation"""
        # This would implement a basic token generation
        # For now, return False to indicate authentication setup failed
        logging.error("❌ Fallback token generation not implemented")
        return False

    def create_cpu_intensive_workflow(self) -> Dict:
        """Create a CPU-intensive workflow payload"""
        return {
            "name": f"cpu_stress_test_{int(time.time())}",
            "description": "CPU-intensive workflow for autoscaling load testing",
            "algorithm": "suite2p_cell_extraction",
            "params": {
                "suite2p_file_path": "/tmp/sample_data/sample_mouse2p_image.tiff",
                "suite2p_params": {
                    "neuropil_basis": "dF/F",
                    "neucoeff": 0.7,
                    "allow_overlap": False,
                    # CPU-intensive parameters
                    "max_iterations": 1000,
                    "high_pass": 100,
                    "spatial_hp_reg": 100,
                },
            },
        }

    def create_memory_intensive_workflow(self) -> Dict:
        """Create a memory-intensive workflow payload"""
        return {
            "name": f"memory_stress_test_{int(time.time())}",
            "description": "Memory-intensive workflow for autoscaling load testing",
            "algorithm": "caiman_motion_correction",
            "params": {
                "input_file": "/tmp/sample_data/sample_mouse2p_image.tiff",
                "caiman_params": {
                    # Memory-intensive parameters
                    "max_shifts": (10, 10),
                    "niter_rig": 3,
                    "splits_rig": 28,
                    "num_splits_to_process_rig": None,
                    "strides": (96, 96),
                    "overlaps": (32, 32),
                    "splits_els": 28,
                    "num_splits_to_process_els": [14, None],
                    "upsample_factor_grid": 4,
                    "max_deviation_rigid": 3,
                },
            },
        }

    def submit_workflow(self, workflow_data: Dict, user_token: str) -> Optional[str]:
        """Submit a single workflow"""
        try:
            headers = {
                "Authorization": f"Bearer {user_token}",
                "Content-Type": "application/json",
            }

            response = requests.post(
                f"{self.config.api_url}/workflows/submit",
                json=workflow_data,
                headers=headers,
                timeout=30,
            )

            if response.status_code == 200:
                result = response.json()
                workflow_id = result.get("workflow_id")
                if workflow_id:
                    self.submitted_workflows.append(
                        {
                            "id": workflow_id,
                            "name": workflow_data["name"],
                            "submitted_at": datetime.now(),
                            "type": "cpu"
                            if "cpu_stress" in workflow_data["name"]
                            else "memory",
                        }
                    )
                    return workflow_id
            else:
                logging.error(
                    f"❌ Workflow submission failed: "
                    f"{response.status_code} - {response.text}"
                )

        except Exception as e:
            logging.error(f"❌ Error submitting workflow: {e}")

        return None

    def generate_load(self):
        """Generate load through concurrent workflow submissions"""
        if not self.tokens:
            logging.error("❌ No authentication tokens available")
            return False

        # Get a user token (prefer premium for load testing)
        user_token = None
        if "premium_token" in self.tokens:
            user_token = self.tokens["premium_token"]
        elif "free_token" in self.tokens:
            user_token = self.tokens["free_token"]
        else:
            logging.error("❌ No user tokens found in token data")
            return False

        logging.info(
            f"🚀 Starting load generation with "
            f"{self.config.concurrent_workflows} concurrent workflows..."
        )

        # Determine workflow types based on test configuration
        workflows_to_submit = []

        if self.config.cpu_only:
            workflows_to_submit = ["cpu"] * self.config.concurrent_workflows
        elif self.config.memory_only:
            workflows_to_submit = ["memory"] * self.config.concurrent_workflows
        else:
            # Mixed load - alternate between CPU and memory intensive
            for i in range(self.config.concurrent_workflows):
                workflows_to_submit.append("cpu" if i % 2 == 0 else "memory")

        # Submit workflows concurrently
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(10, self.config.concurrent_workflows)
        ) as executor:
            futures = []

            for i, workflow_type in enumerate(workflows_to_submit):
                if workflow_type == "cpu":
                    workflow_data = self.create_cpu_intensive_workflow()
                else:
                    workflow_data = self.create_memory_intensive_workflow()

                future = executor.submit(
                    self.submit_workflow, workflow_data, user_token
                )
                futures.append(future)

                # Stagger submissions slightly to avoid overwhelming the API
                time.sleep(0.5)

            # Wait for all submissions to complete
            submitted_count = 0
            for future in concurrent.futures.as_completed(futures):
                workflow_id = future.result()
                if workflow_id:
                    submitted_count += 1
                    logging.info(
                        f"✅ Submitted workflow {submitted_count}"
                        f"/{len(workflows_to_submit)}: {workflow_id}"
                    )

        logging.info(
            f"📈 Load generation complete: {submitted_count}"
            f"/{self.config.concurrent_workflows} workflows submitted"
        )
        return submitted_count > 0


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
        report.append("🧪 OPTINIST AUTOSCALING LOAD TEST REPORT")
        report.append("=" * 80)
        report.append("")

        # Test configuration
        report.append("📋 TEST CONFIGURATION:")
        report.append(f"   Environment: {self.config.environment}")
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
        report.append("🚀 WORKFLOW SUBMISSION:")
        report.append(f"   Workflows Submitted: {analysis['workflows_submitted']}")
        submitted = analysis["workflows_submitted"]
        submission_rate = submitted / self.config.concurrent_workflows * 100
        report.append(f"   Submission Success Rate: " f"{submission_rate:.1f}%")
        report.append("")

        # Peak utilization
        report.append("📊 PEAK UTILIZATION:")
        report.append(f"   Peak CPU: {analysis['peak_utilization']['cpu']:.2f}%")
        report.append(f"   Peak Memory: {analysis['peak_utilization']['memory']:.2f}%")
        report.append("")

        # Threshold breaches
        report.append("⚠️ THRESHOLD BREACHES:")
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
        report.append("⚡ SCALING EVENTS:")
        scaling_events = analysis["scaling_events"]
        report.append(f"   Total Scaling Events: {len(scaling_events)}")

        scale_ups = [e for e in scaling_events if e["direction"] == "scale_up"]
        scale_downs = [e for e in scaling_events if e["direction"] == "scale_down"]

        report.append(f"   Scale-up Events: {len(scale_ups)}")
        report.append(f"   Scale-down Events: {len(scale_downs)}")

        for event in scaling_events:
            direction_emoji = "📈" if event["direction"] == "scale_up" else "📉"
            report.append(
                f"{direction_emoji} {event['from_capacity']} → "
                f"{event['to_capacity']} instances "
                f"(CPU: {event['trigger_cpu']:.1f}%, "
                f"Memory: {event['trigger_memory']:.1f}%)"
            )
        report.append("")

        # Responsiveness analysis
        report.append("⏱️ SCALING RESPONSIVENESS:")
        responsiveness = analysis["scaling_responsiveness"]
        if "cpu_response_time_seconds" in responsiveness:
            response_time = responsiveness["cpu_response_time_seconds"]
            report.append(f"   CPU Threshold → Scale-up: {response_time:.1f} seconds")

            if response_time <= 300:  # Expected CloudWatch alarm evaluation period
                report.append(
                    "   ✅ Scaling response time within expected range (≤300s)"
                )
            else:
                report.append(
                    "   ⚠️ Scaling response time exceeded expected range (>300s)"
                )
        else:
            report.append("   ❌ No scaling response detected")
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
        report.append("💡 RECOMMENDATIONS:")

        if cpu_breaches == 0 and memory_breaches == 0:
            report.append(
                "   ⚠️ No thresholds breached - consider increasing load "
                "or decreasing thresholds"
            )

        if len(scale_ups) == 0 and (cpu_breaches > 0 or memory_breaches > 0):
            report.append(
                "   ❌ Thresholds breached but no scaling occurred - "
                "check CloudWatch alarms"
            )

        if len(scale_ups) > 0 and "cpu_response_time_seconds" in responsiveness:
            if responsiveness["cpu_response_time_seconds"] > 600:
                report.append(
                    "   ⚠️ Slow scaling response - consider optimizing "
                    "alarm evaluation periods"
                )

        if (
            analysis["peak_utilization"]["cpu"] < 50
            and analysis["peak_utilization"]["memory"] < 50
        ):
            report.append(
                "   💡 Low resource utilization - consider more " "intensive workloads"
            )

        report.append("")
        report.append("=" * 80)

        return "\n".join(report)


def main():
    """Main load test execution"""
    parser = argparse.ArgumentParser(description="OptiNiSt Autoscaling Load Test")

    # Test configuration
    parser.add_argument(
        "--environment",
        choices=["local", "cloud"],
        default="local",
        help="Test environment (default: local)",
    )
    parser.add_argument(
        "--api-url", type=str, help="API URL (auto-detected for local environment)"
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=1800,
        help="Test duration in seconds (default: 1800 = 30 minutes)",
    )
    parser.add_argument(
        "--concurrent-workflows",
        type=int,
        default=8,
        help="Number of concurrent workflows to submit (default: 8)",
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
        default="subscr-optinist-asg",
        help="Auto Scaling Group name (default: subscr-optinist-asg)",
    )
    parser.add_argument(
        "--cluster-name",
        type=str,
        default="subscr-optinist-cloud-cluster",
        help="ECS cluster name (default: subscr-optinist-cloud-cluster)",
    )
    parser.add_argument(
        "--service-name",
        type=str,
        default="subscr-optinist-cloud-service",
        help="ECS service name (default: subscr-optinist-cloud-service)",
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

    logging.info("🧪 Starting OptiNiSt Autoscaling Load Test")
    logging.info(
        f"📋 Configuration: {config.environment} "
        f"environment, {config.duration}s duration, "
        f"{config.concurrent_workflows} workflows"
    )

    # Initialize components
    monitor = CloudWatchMonitor(config)
    generator = WorkflowLoadGenerator(config)
    analyzer = LoadTestAnalyzer(config, monitor, generator)

    try:
        # Setup authentication
        if not generator.setup_authentication():
            logging.error(
                "❌ Failed to setup authentication - cannot proceed with load test"
            )
            sys.exit(1)

        # Start metrics monitoring in background
        monitor_thread = threading.Thread(target=monitor.monitor_metrics, daemon=True)
        monitor_thread.start()

        # Wait a moment for initial metrics
        time.sleep(5)

        # Generate load
        load_success = generator.generate_load()
        if not load_success:
            logging.error("❌ Failed to generate load - test incomplete")

        # Continue monitoring for the specified duration
        logging.info(
            f"⏱️ Monitoring autoscaling behavior for {config.duration} seconds..."
        )
        time.sleep(config.duration)

        # Stop monitoring
        monitor.stop_monitoring()

        # Analyze results
        logging.info("📊 Analyzing test results...")
        analysis = analyzer.analyze_scaling_behavior()

        # Generate and display report
        report = analyzer.generate_report(analysis)
        print("\n" + report)

        # Save detailed results
        detailed_results = {
            "config": {
                "environment": config.environment,
                "duration": config.duration,
                "concurrent_workflows": config.concurrent_workflows,
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

        logging.info(f"💾 Detailed results saved to: {config.output_file}")

        # Test success criteria
        success_criteria = []
        if analysis["workflows_submitted"] >= config.concurrent_workflows * 0.8:
            success_criteria.append("✅ Workflow submission success")
        else:
            success_criteria.append("❌ Workflow submission failed")

        if (
            analysis["peak_utilization"]["cpu"] > config.target_cpu_threshold
            or analysis["peak_utilization"]["memory"] > config.target_memory_threshold
        ):
            success_criteria.append("✅ Resource thresholds reached")
        else:
            success_criteria.append("⚠️ Resource thresholds not reached")

        if len(analysis["scaling_events"]) > 0:
            success_criteria.append("✅ Autoscaling events detected")
        else:
            success_criteria.append("❌ No autoscaling events detected")

        logging.info("🏆 Test Success Criteria:")
        for criteria in success_criteria:
            logging.info(f"   {criteria}")

        # Exit with appropriate code
        failed_criteria = [c for c in success_criteria if c.startswith("❌")]
        if failed_criteria:
            logging.warning(
                "⚠️ Some test criteria failed - review configuration and try again"
            )
            sys.exit(1)
        else:
            logging.info("🎉 Load test completed successfully!")
            sys.exit(0)

    except KeyboardInterrupt:
        logging.info("⏹️ Load test interrupted by user")
        monitor.stop_monitoring()
        sys.exit(130)
    except Exception as e:
        logging.error(f"❌ Load test failed: {e}")
        monitor.stop_monitoring()
        sys.exit(1)


if __name__ == "__main__":
    main()
