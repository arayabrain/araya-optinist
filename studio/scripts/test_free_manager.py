#!/usr/bin/env python3
"""
Test script for Free Manager Lambda functionality.

This script tests the Free Manager system that handles:
1. Activity tracking for free tier users
2. Proactive scaling based on active user count
3. User rebalancing to newly launched instances (multi-instance algorithm)
4. Workflow protection during migration (atomic SQL protection)

UPDATED 2025-11-18:
- Lambda now waits up to 10 minutes internally for new instances to launch
- Lambda performs multi-instance rebalancing (distributes across ALL instances)
- Lambda verifies distribution is balanced after rebalancing
- Test adapted to handle longer Lambda execution time (~8-10 min during scale-up)

Similar to test_premium_instance_provisioning.py pattern.

Usage:
    python test_free_manager.py [--terraform-dir PATH] [--region REGION]

Environment Variables:
    AWS_REGION: AWS region (default: us-east-1)
    TERRAFORM_DIR: Path to terraform directory (default: ../config/terraform)

Expected Runtime:
    - Normal run (no scaling): ~2-3 minutes
    - Scale-up scenario: ~10-12 minutes (Lambda waits for instances internally)
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import boto3
from botocore.exceptions import ClientError


class FreeManagerTester:
    """Test harness for Free Manager Lambda system."""

    def __init__(self, terraform_dir: str, aws_region: str):
        self.terraform_dir = terraform_dir
        self.aws_region = aws_region

        # AWS clients
        self.lambda_client = boto3.client("lambda", region_name=aws_region)
        self.ecs_client = boto3.client("ecs", region_name=aws_region)
        self.cloudwatch_client = boto3.client("cloudwatch", region_name=aws_region)
        self.ec2_client = boto3.client("ec2", region_name=aws_region)

        # Load Terraform outputs
        self.terraform_outputs = self._load_terraform_outputs()

        # Configuration from Terraform
        self.free_manager_lambda = self.terraform_outputs.get(
            "free_manager_lambda_name", "subscr-free-manager"
        )
        self.free_cleanup_lambda = self.terraform_outputs.get(
            "free_cleanup_lambda_name", "subscr-free-cleanup"
        )
        self.cluster_name = self.terraform_outputs.get("ecs_cluster_name", "")
        self.free_service_name = self.terraform_outputs.get(
            "ecs_service_name_free", "subscr-optinist-cloud-service"
        )

        print("Initialized FreeManagerTester")
        print(f"Free Manager Lambda: {self.free_manager_lambda}")
        print(f"Free Cleanup Lambda: {self.free_cleanup_lambda}")
        print(f"ECS Cluster: {self.cluster_name}")
        print(f"Free Service: {self.free_service_name}")

    def _load_terraform_outputs(self) -> Dict:
        """Load Terraform outputs using terraform output command."""
        import subprocess

        try:
            result = subprocess.run(
                ["terraform", "output", "-json"],
                cwd=self.terraform_dir,
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
            print("Terraform not found. Please install Terraform.")
            sys.exit(1)

    def _invoke_cleanup_lambda(self, action: str, **kwargs) -> Optional[Dict]:
        """
        Invoke free cleanup Lambda with specified action.

        Args:
            action: Action to perform (cleanup_test_users, simulate_user_activity, etc.)
            **kwargs: Additional parameters for the action

        Returns:
            Lambda response payload or None if failed
        """
        event = {"action": action, **kwargs}

        try:
            response = self.lambda_client.invoke(
                FunctionName=self.free_cleanup_lambda,
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
            print(f"Failed to decode JSON from Lambda response: {e}")
            print(f"Raw payload: {payload}")
            return None

    # =========================================================================
    # STEP 0: Cleanup Functions
    # =========================================================================

    def cleanup_test_data(self):
        """Remove all test data from database via Lambda."""
        print("\n" + "=" * 80)
        print("STEP 0: Cleanup test data")
        print("=" * 80)

        result = self._invoke_cleanup_lambda("cleanup_all_test_users")

        if result and result.get("success"):
            print(f"Deleted {result.get('sessions_deleted', 0)} test user sessions")
        else:
            print(f"Cleanup failed: {result}")

    def reset_ecs_service(self):
        """Reset ECS service to desired count of 1."""
        print("\nResetting ECS service to desired count = 1...")

        try:
            response = self.ecs_client.update_service(
                cluster=self.cluster_name,
                service=self.free_service_name,
                desiredCount=1,
            )
            print(f"DEBUG: ECS update_service response: {response}")
            print("Updated service desired count to 1")

            # Wait for service to stabilize
            print("Waiting for service to stabilize (this may take 1-2 minutes)...")
            waiter = self.ecs_client.get_waiter("services_stable")
            waiter.wait(
                cluster=self.cluster_name,
                services=[self.free_service_name],
                WaiterConfig={"Delay": 15, "MaxAttempts": 20},
            )
            print("Service stabilized at desired count = 1")

        except ClientError as e:
            print(f"Failed to reset ECS service: {e}")
            raise

    # =========================================================================
    # STEP 1: Simulate User Activity
    # =========================================================================

    def simulate_user_activity(
        self, user_id: str, instance_id: str, minutes_ago: int = 0
    ) -> bool:
        """
        Insert/update user activity via cleanup Lambda.

        Args:
            user_id: User ID (string format)
            instance_id: ECS instance ID
            minutes_ago: How many minutes ago the activity occurred (0 = now)

        Returns:
            True on success, False on failure
        """
        result = self._invoke_cleanup_lambda(
            "simulate_user_activity",
            user_id=user_id,
            instance_id=instance_id,
            minutes_ago=minutes_ago,
        )

        return result is not None and result.get("success", False)

    def simulate_workflow_running(self, user_id: str, workflow_count: int = 1) -> bool:
        """
        Set active_workflow_count for a user via cleanup Lambda.

        Args:
            user_id: User ID (string format)
            workflow_count: Number of active workflows (default: 1)

        Returns:
            True on success, False on failure
        """
        result = self._invoke_cleanup_lambda(
            "simulate_workflow", user_id=user_id, workflow_count=workflow_count
        )

        return result is not None and result.get("success", False)

    def get_running_instance_ids(self) -> List[str]:
        """Get list of currently running ECS instance IDs."""
        print("DEBUG: get_running_instance_ids() called")
        try:
            # List all tasks in the service
            print(f"DEBUG: Listing tasks in service: {self.free_service_name}")
            response = self.ecs_client.list_tasks(
                cluster=self.cluster_name,
                serviceName=self.free_service_name,
                desiredStatus="RUNNING",
            )

            task_arns = response.get("taskArns", [])
            print(f"DEBUG: Found {len(task_arns)} RUNNING tasks")

            if not task_arns:
                print("DEBUG: No running tasks found")
                # Check service status
                print("DEBUG: Checking ECS service status...")
                svc_response = self.ecs_client.describe_services(
                    cluster=self.cluster_name, services=[self.free_service_name]
                )
                if svc_response["services"]:
                    svc = svc_response["services"][0]
                    print(
                        f"DEBUG: Service desired={svc['desiredCount']}, "
                        f"running={svc['runningCount']}, "
                        f"pending={svc['pendingCount']}"
                    )
                return []

            # Describe tasks to get container instance ARNs
            print(f"DEBUG: Describing {len(task_arns)} tasks...")
            tasks_response = self.ecs_client.describe_tasks(
                cluster=self.cluster_name, tasks=task_arns
            )

            print("DEBUG: Task details:")
            for task in tasks_response["tasks"]:
                task_id = task["taskArn"].split("/")[-1][:12]
                status = task["lastStatus"]
                container_inst = task.get("containerInstanceArn", "N/A")
                inst_id = (
                    container_inst.split("/")[-1][:12]
                    if container_inst != "N/A"
                    else "N/A"
                )
                print(f"DEBUG:   Task {task_id}: {status}, instance: {inst_id}")

            container_instance_arns = [
                task["containerInstanceArn"]
                for task in tasks_response["tasks"]
                if "containerInstanceArn" in task
            ]

            # Get unique container instances
            unique_container_arns = list(set(container_instance_arns))
            print(f"DEBUG: Unique container instances: {len(unique_container_arns)}")

            if not unique_container_arns:
                print("DEBUG: No container instances with running tasks")
                return []

            # Describe container instances to get EC2 instance IDs
            print("DEBUG: Describing container instances...")
            instances_response = self.ecs_client.describe_container_instances(
                cluster=self.cluster_name, containerInstances=unique_container_arns
            )

            print("DEBUG: Container instance details:")
            instance_ids = []
            for inst in instances_response["containerInstances"]:
                ec2_id = inst["ec2InstanceId"]
                status = inst["status"]
                agent = inst["agentConnected"]
                tasks = inst["runningTasksCount"]
                print(f"DEBUG: {ec2_id}: status={status}, agent={agent}, tasks={tasks}")
                instance_ids.append(ec2_id)

            print(f"DEBUG: Returning {len(instance_ids)} instance IDs: {instance_ids}")
            return instance_ids

        except ClientError as e:
            print(f"ERROR: Failed to get instance IDs: {e}")
            return []

    def setup_test_users(self, num_users: int = 6) -> List[str]:
        """
        Create test users with activity on first available instance.

        Args:
            num_users: Number of test users to create (default: 6, above threshold of 5)

        Returns:
            List of user IDs created
        """
        print("\n" + "=" * 80)
        print(f"STEP 1: Simulate {num_users} active users")
        print("=" * 80)

        # Get current instance ID
        instance_ids = self.get_running_instance_ids()
        if not instance_ids:
            print(
                "No running instances found. Using placeholder instance ID for testing."
            )
            instance_id = "i-test001"
        else:
            instance_id = instance_ids[0]
            print(f"Found running instance: {instance_id}")

        # Create users
        user_ids = [f"test_user_{i}" for i in range(1, num_users + 1)]

        print(f"\nCreating {num_users} test users on instance {instance_id}...")
        for user_id in user_ids:
            success = self.simulate_user_activity(user_id, instance_id, minutes_ago=0)
            if not success:
                print(f"Failed to simulate activity for {user_id}")
                sys.exit(1)

        print(f"Created {num_users} active users")

        # Add workflows to 2 users (to test protection)
        workflow_users = user_ids[:2]
        print(f"\nSimulating workflows for users: {workflow_users}")
        for user_id in workflow_users:
            success = self.simulate_workflow_running(user_id, workflow_count=1)
            if not success:
                print(f"Failed to set workflow for {user_id}")

        print(f"Set active_workflow_count=1 for {len(workflow_users)} users")

        # Verify database state
        self.verify_database_state()

        return user_ids

    def verify_database_state(self):
        """Print current database state via cleanup Lambda."""
        result = self._invoke_cleanup_lambda("get_user_distribution")

        if result and result.get("success"):
            users = result.get("users", [])
            distribution = result.get("distribution", [])

            print(f"\nCurrent database state ({len(users)} users):")
            print(
                f"{'User ID':<15} {'Instance ID':<20} "
                f"{'Workflows':<10} {'Last Activity'}"
            )
            print(f"{'-'*15} {'-'*20} {'-'*10} {'-'*19}")

            for user in users:
                print(
                    f"{user['user_id']:<15} "
                    f"{user['instance_id']:<20} "
                    f"{user['active_workflows']:<10} "
                    f"{user['last_activity'][:19] if user['last_activity'] else 'N/A'}"
                )

            print("\nDistribution summary:")
            for dist in distribution:
                print(
                    f"Instance {dist['instance_id']}: "
                    f"{dist['user_count']} users, "
                    f"{dist['total_workflows']} workflows"
                )

    # =========================================================================
    # STEP 2: Invoke Free Manager Lambda
    # =========================================================================

    def invoke_free_manager_lambda(self) -> Optional[Dict]:
        """
        Invoke Free Manager Lambda with CloudWatch Event simulation.

        NOTE: Lambda may take up to 10 minutes to complete if scaling is triggered.
        The Lambda now waits internally for new instances to launch before rebalancing.

        Returns:
            Lambda response payload or None if failed
        """
        print("\n" + "=" * 80)
        print("STEP 2: Invoke Free Manager Lambda")
        print("=" * 80)

        # Simulate CloudWatch Event payload
        event = {
            "source": "aws.events",
            "detail-type": "Scheduled Event",
            "detail": {"action": "monitor"},
        }

        print(f"Invoking Lambda: {self.free_manager_lambda}")
        print(f"Event payload: {json.dumps(event, indent=2)}")
        print("NOTE: Lambda may take 8-10 minutes if scaling up (waits for instances)")
        print("    This is expected behavior - Lambda handles retry internally.")

        try:
            start_time = time.time()

            response = self.lambda_client.invoke(
                FunctionName=self.free_manager_lambda,
                InvocationType="RequestResponse",
                Payload=json.dumps(event),
            )

            elapsed = time.time() - start_time
            payload = json.loads(response["Payload"].read())
            status_code = response["StatusCode"]

            print(
                f"\nLambda completed in {elapsed:.1f} "
                f"seconds ({elapsed/60:.1f} minutes)"
            )
            print(f"Status Code: {status_code}")
            print(f"Response: {json.dumps(payload, indent=2)}")

            if status_code != 200:
                print(f"Lambda returned non-200 status: {status_code}")
                return None

            # Parse response body
            if "body" in payload:
                body = json.loads(payload["body"])
                print("\nLambda Result:")
                print(f"Active users: {body.get('active_user_count', 'N/A')}")
                print(f"Scaling action: {body.get('scaling_action', 'N/A')}")
                print(f"Rebalanced users: {body.get('rebalanced_users', 'N/A')}")

                # New fields from updated Lambda
                if "rebalancing_successful" in body:
                    print(
                        f"Rebalancing successful: {body.get('rebalancing_successful')}"
                    )
                if "rebalancing_attempts" in body:
                    print(f"Rebalancing attempts: {body.get('rebalancing_attempts')}")

            return payload

        except ClientError as e:
            print(f"Failed to invoke Lambda: {e}")
            return None

    # =========================================================================
    # STEP 3: Verify ECS Service Scaling
    # =========================================================================

    def verify_ecs_scaling(self, expected_min_count: int = 2) -> bool:
        """
        Verify that ECS service has scaled up.

        Args:
            expected_min_count: Minimum expected desired count

        Returns:
            True if scaling occurred, False otherwise
        """
        print("\n" + "=" * 80)
        print("STEP 3: Verify ECS service scaling")
        print("=" * 80)

        try:
            response = self.ecs_client.describe_services(
                cluster=self.cluster_name, services=[self.free_service_name]
            )

            if not response["services"]:
                print(f"Service not found: {self.free_service_name}")
                return False

            service = response["services"][0]
            desired_count = service["desiredCount"]
            running_count = service["runningCount"]

            print(f"Service: {self.free_service_name}")
            print(f"Desired count: {desired_count}")
            print(f"Running count: {running_count}")

            if desired_count >= expected_min_count:
                print(
                    f"Service scaled up (desired={desired_count} "
                    f">= {expected_min_count})"
                )
                return True
            else:
                print(
                    f"Service did not scale (desired={desired_count} "
                    f"< {expected_min_count})"
                )
                return False

        except ClientError as e:
            print(f"Failed to verify ECS scaling: {e}")
            return False

    def wait_for_new_instances(self, timeout: int = 300) -> bool:
        """
        Wait for new ECS instances to become available.

        Args:
            timeout: Maximum wait time in seconds (default: 5 minutes)

        Returns:
            True if new instances are running, False if timeout
        """
        print(f"\nVerifying new instances are available (timeout: {timeout}s)...")
        print("NOTE: Lambda should have already waited for instances internally.")

        start_time = time.time()
        iteration = 0
        while time.time() - start_time < timeout:
            iteration += 1
            print(f"\n[Check {iteration}] Verifying instances...")
            instance_ids = self.get_running_instance_ids()

            if len(instance_ids) >= 2:
                print(
                    f"SUCCESS: Found {len(instance_ids)} "
                    f"running instances: {instance_ids}"
                )
                elapsed = int(time.time() - start_time)
                print(f"Verification completed in {elapsed} seconds")
                return True

            print(f"Currently {len(instance_ids)} instances: {instance_ids}")
            print(f"Elapsed: {int(time.time() - start_time)}s / {timeout}s")

            # Only wait if we haven't found instances yet
            if time.time() - start_time + 10 < timeout:
                print("Waiting 10s before next check...")
                time.sleep(10)
            else:
                break

        print(f"\nTIMEOUT: New instances not found after {timeout}s")
        print("This may indicate the Lambda's internal wait timed out or failed.")
        return False

    # =========================================================================
    # STEP 4: Verify User Distribution
    # =========================================================================

    def verify_user_distribution(self) -> bool:
        """
        Verify that users have been distributed across instances.

        Returns:
            True if distribution successful, False otherwise
        """
        print("\n" + "=" * 80)
        print("STEP 4: Verify user distribution")
        print("=" * 80)

        result = self._invoke_cleanup_lambda("get_user_distribution")

        if not result or not result.get("success"):
            print("Failed to get user distribution")
            return False

        distribution = result.get("distribution", [])
        users = result.get("users", [])

        print("\nUser distribution by instance:")
        print(f"{'Instance ID':<20} {'Users':<10} {'Workflows'}")
        print(f"{'-'*20} {'-'*10} {'-'*10}")

        for dist in distribution:
            instance_id = dist["instance_id"]
            user_count = dist["user_count"]
            workflows = dist["total_workflows"]

            print(f"{instance_id:<20} {user_count:<10} {workflows}")

        print("\nSummary:")
        print(f"Total instances: {len(distribution)}")
        print(f"Total users: {len(users)}")

        # Check if distribution occurred
        if len(distribution) >= 2:
            print(f"Users distributed across {len(distribution)} instances")
            return True
        else:
            print("Users still on single instance")
            return False

    # =========================================================================
    # STEP 5: Verify Workflow Protection
    # =========================================================================

    def verify_workflow_protection(self) -> bool:
        """
        Verify that users with active workflows were NOT migrated.

        Returns:
            True if workflow protection works, False otherwise
        """
        print("\n" + "=" * 80)
        print("STEP 5: Verify workflow protection")
        print("=" * 80)

        result = self._invoke_cleanup_lambda("get_user_distribution")

        if not result or not result.get("success"):
            print("Failed to get user distribution")
            return False

        users = result.get("users", [])
        workflow_users = [u for u in users if u.get("active_workflows", 0) > 0]

        print(f"\nUsers with active workflows ({len(workflow_users)}):")
        print(f"{'User ID':<15} {'Instance ID':<20} {'Workflows':<10} {'Migrations'}")
        print(f"{'-'*15} {'-'*20} {'-'*10} {'-'*10}")

        all_protected = True
        for user in workflow_users:
            migration_count = user.get("migration_count", 0)
            print(
                f"{user['user_id']:<15} "
                f"{user['instance_id']:<20} "
                f"{user['active_workflows']:<10} "
                f"{migration_count}"
            )

            if migration_count > 0:
                print(
                    f"WARNING: User {user['user_id']} has workflows but was migrated!"
                )
                all_protected = False

        if workflow_users:
            if all_protected:
                print(
                    f"\nSUCCESS: All {len(workflow_users)} users with "
                    f"workflows were protected (migration_count=0)"
                )
            else:
                print(
                    "\nFAILURE: Some users with workflows were migrated "
                    "despite protection"
                )
        else:
            print("\nNo users with workflows found")

        return all_protected

    # =========================================================================
    # STEP 6: Verify CloudWatch Metrics
    # =========================================================================

    def verify_cloudwatch_metrics(self) -> bool:
        """
        Verify that CloudWatch metrics were published.

        Returns:
            True if metrics found, False otherwise
        """
        print("\n" + "=" * 80)
        print("STEP 6: Verify CloudWatch metrics")
        print("=" * 80)

        try:
            # Query for active user count metric
            end_time = datetime.now()
            start_time = end_time - timedelta(minutes=10)

            response = self.cloudwatch_client.get_metric_statistics(
                Namespace="OptiNiSt/FreeUsers",
                MetricName="ActiveLogins",
                StartTime=start_time,
                EndTime=end_time,
                Period=300,  # 5 minutes
                Statistics=["Average", "Maximum"],
            )

            datapoints = response.get("Datapoints", [])

            print("Metric: OptiNiSt/FreeUsers/ActiveLogins")
            print(f"Time range: {start_time} to {end_time}")
            print(f"Datapoints found: {len(datapoints)}")

            if datapoints:
                # Sort by timestamp
                datapoints.sort(key=lambda x: x["Timestamp"], reverse=True)

                print("\n  Latest datapoints:")
                for dp in datapoints[:3]:  # Show last 3
                    print(
                        f"{dp['Timestamp']}: "
                        f"avg={dp.get('Average', 'N/A'):.1f}, "
                        f"max={dp.get('Maximum', 'N/A'):.1f}"
                    )

                print("\nCloudWatch metrics published successfully")
                return True
            else:
                print(
                    "\nNo CloudWatch metrics found (may take a few minutes to appear)"
                )
                return False

        except ClientError as e:
            print(f"Failed to query CloudWatch metrics: {e}")
            return False

    def test_json_serialization(self) -> bool:
        """
        Verify that Lambda response with Decimal types is correctly serialized.
        """
        print("\n" + "=" * 80)
        print("STEP 1.5: Verify JSON serialization of Decimal types")
        print("=" * 80)

        # This action is known to return Decimal types from the database
        # (e.g., SUM(active_workflow_count))
        # A successful invocation and parsing implies the fix is working.
        result = self._invoke_cleanup_lambda("get_user_distribution")

        if result and result.get("success"):
            print("Successfully invoked get_user_distribution and parsed response.")
            print("This indicates that the Decimal to JSON serialization is working.")
            return True
        else:
            print("Failed to invoke get_user_distribution or parse its response.")
            print(f"Result: {result}")
            return False

    # =========================================================================
    # STEP 7: Final Cleanup
    # =========================================================================

    def final_cleanup(self, results: Dict[str, bool]):
        """
        Perform final cleanup after tests complete.

        - Always: Delete test users
        - Only if all tests passed: Scale down ECS service to 1
        - If any test failed: Leave ECS at 2 for debugging

        Args:
            results: Dictionary of test results
        """
        print("\n" + "=" * 80)
        print("STEP 7: Final cleanup")
        print("=" * 80)

        all_passed = all(results.values())

        # Always clean up test users
        print("\nCleaning up test users...")
        cleanup_result = self._invoke_cleanup_lambda("cleanup_all_test_users")

        if cleanup_result and cleanup_result.get("success"):
            print(
                f"Deleted {cleanup_result.get('sessions_deleted', 0)} "
                f"test user sessions"
            )
        else:
            print(f"Failed to clean up test users: {cleanup_result}")

        # Conditionally scale down ECS service
        if all_passed:
            print("\nAll tests passed - scaling down ECS service to 1...")
            try:
                self.ecs_client.update_service(
                    cluster=self.cluster_name,
                    service=self.free_service_name,
                    desiredCount=1,
                )
                print("ECS service scaled down to desiredCount=1")
            except ClientError as e:
                print(f"Failed to scale down ECS service: {e}")
        else:
            print(
                "\nSome tests failed - leaving ECS service at current "
                "state for debugging"
            )
            print("(ECS service likely at desiredCount=2)")

        print("=" * 80)

    # =========================================================================
    # Main Test Flow
    # =========================================================================

    def run_full_test(self):
        """Run complete end-to-end test."""
        print("\n" + "=" * 80)
        print("FREE MANAGER LAMBDA - END-TO-END TEST")
        print("=" * 80)
        print(f"Terraform Dir: {self.terraform_dir}")
        print(f"AWS Region: {self.aws_region}")
        print(f"Timestamp: {datetime.now()}")

        results = {
            "cleanup": False,
            "user_setup": False,
            "json_serialization": False,
            "lambda_invocation": False,
            "ecs_scaling": False,
            "user_distribution": False,
            "workflow_protection": False,
            "cloudwatch_metrics": False,
        }

        try:
            # STEP 0: Cleanup
            self.cleanup_test_data()
            self.reset_ecs_service()
            results["cleanup"] = True

            # STEP 1: Setup test users (6 users, above threshold of 5)
            self.setup_test_users(num_users=6)
            results["user_setup"] = True

            # STEP 1.5: Verify JSON serialization
            serialization_success = self.test_json_serialization()
            results["json_serialization"] = serialization_success
            if not serialization_success:
                # If this fails, other calls might also fail parsing
                print(
                    "JSON serialization test failed. "
                    "Subsequent tests may be unreliable."
                )

            # STEP 2: Invoke Lambda
            lambda_response = self.invoke_free_manager_lambda()
            results["lambda_invocation"] = lambda_response is not None

            # STEP 3: Verify ECS scaling
            scaling_success = self.verify_ecs_scaling(expected_min_count=2)
            results["ecs_scaling"] = scaling_success

            # STEP 4: Verify user distribution
            if scaling_success:
                print(
                    "\nNOTE: With updated Lambda, rebalancing already completed "
                    "during first invocation."
                )
                print("Verifying user distribution now...")

                # Give a moment for database to settle
                time.sleep(5)

                distribution_success = self.verify_user_distribution()
                results["user_distribution"] = distribution_success

            # STEP 5: Verify workflow protection
            protection_success = self.verify_workflow_protection()
            results["workflow_protection"] = protection_success

            # STEP 6: Verify CloudWatch metrics
            metrics_success = self.verify_cloudwatch_metrics()
            results["cloudwatch_metrics"] = metrics_success

            # STEP 7: Final cleanup
            self.final_cleanup(results)

        except Exception as e:
            print(f"\nTest failed with exception: {e}")
            import traceback

            traceback.print_exc()

            # Still try to clean up test users even on failure
            try:
                print("\nAttempting cleanup after failure...")
                self.cleanup_test_data()
            except Exception as cleanup_error:
                print(f"Cleanup after failure also failed: {cleanup_error}")

        # Print summary
        self.print_test_summary(results)

        return results

    def print_test_summary(self, results: Dict[str, bool]):
        """Print test results summary."""
        print("\n" + "=" * 80)
        print("TEST RESULTS SUMMARY")
        print("=" * 80)

        total_tests = len(results)
        passed_tests = sum(1 for v in results.values() if v)

        for test_name, passed in results.items():
            status = "PASS" if passed else "FAIL"
            print(f"{status:<10} {test_name}")

        print(f"\n{'=' * 80}")
        print(f"Total: {passed_tests}/{total_tests} tests passed")

        if passed_tests == total_tests:
            print("ALL TESTS PASSED")
        else:
            print(f"{total_tests - passed_tests} TESTS FAILED")

        print("=" * 80)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Test Free Manager Lambda functionality"
    )
    parser.add_argument(
        "--terraform-dir",
        default="../config/terraform",
        help="Path to Terraform directory (default: ../config/terraform)",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "ap-northeast-1"),
        help="AWS region (default: $AWS_REGION or ap-northeast-1)",
    )

    args = parser.parse_args()

    # Resolve terraform directory path
    script_dir = os.path.dirname(os.path.abspath(__file__))
    terraform_dir = os.path.abspath(os.path.join(script_dir, args.terraform_dir))

    if not os.path.exists(terraform_dir):
        print(f"Terraform directory not found: {terraform_dir}")
        sys.exit(1)

    # Run tests
    tester = FreeManagerTester(terraform_dir, args.region)
    results = tester.run_full_test()

    # Exit with error code if any tests failed
    if not all(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
