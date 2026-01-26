#!/usr/bin/env python3
"""
Background Service Verification Tests

WHERE TO RUN:
- ECS container with IAM role - Recommended (uses instance credentials)
- Local machine with AWS credentials configured

REQUIREMENTS:
- AWS credentials configured (boto3 access)
- Python 3.11 with boto3
- Access to ECS cluster and CloudWatch logs

WHAT IT TESTS:
Verifies the dedicated background service architecture:
1. Background ECS service is running with exactly 1 task
2. Background service logs show job execution (scheduler active)
3. API service logs do NOT show job execution (scheduler disabled)
4. CloudWatch alarms are configured

These tests verify that:
- Background jobs run in a dedicated service (not in API workers)
- Only one scheduler instance is running (no duplicates)
- API services don't run background jobs

The tests check for ongoing job execution logs rather than one-time startup
messages, so they can be run at any time after deployment.

HOW TO RUN:
  python test_background_service.py [--region REGION] [--cluster CLUSTER]

EXPECTED RESULT:
  All tests should pass showing background service is properly configured

EXPECTED RUNTIME:
  ~1-2 minutes (mostly CloudWatch log queries)

PERFORMANCE IMPACT:
  NONE - Read-only operations (ECS describe, CloudWatch logs)
"""

import argparse
import sys
from datetime import datetime, timedelta

import boto3
from botocore.exceptions import ClientError

# Default configuration
DEFAULT_REGION = "ap-northeast-1"
DEFAULT_CLUSTER = "subscr-optinist-cloud-cluster"
BACKGROUND_SERVICE = "subscr-background-optinist-cloud-service"
API_SERVICE = "subscr-optinist-cloud-service"
BACKGROUND_LOG_GROUP = "/ecs/subscr-background-optinist-cloud-taskdef"
API_LOG_GROUP = "/ecs/subscr-optinist-cloud-taskdef"


class BackgroundServiceTester:
    def __init__(self, region: str, cluster: str):
        self.region = region
        self.cluster = cluster
        self.ecs = boto3.client("ecs", region_name=region)
        self.logs = boto3.client("logs", region_name=region)
        self.cloudwatch = boto3.client("cloudwatch", region_name=region)
        self.results = {}

    def run_all_tests(self) -> bool:
        """Run all verification tests."""
        print("=" * 60)
        print("Background Service Verification Tests")
        print("=" * 60)
        print(f"Region: {self.region}")
        print(f"Cluster: {self.cluster}")
        print("=" * 60)
        print()

        tests = [
            ("background_service_running", self.test_background_service_running),
            ("background_task_count", self.test_background_task_count),
            ("background_jobs_running", self.test_background_jobs_running),
            ("api_scheduler_disabled", self.test_api_scheduler_disabled),
            ("cloudwatch_alarms", self.test_cloudwatch_alarms),
        ]

        all_passed = True
        for test_name, test_func in tests:
            print(f"Running: {test_name}...")
            try:
                result = test_func()
                self.results[test_name] = result
                status = "PASS" if result else "FAIL"
                print(f"Result: {status}")
                if not result:
                    all_passed = False
            except Exception as e:
                print(f"Result: ERROR - {e}")
                self.results[test_name] = False
                all_passed = False
            print()

        self.print_summary()
        return all_passed

    def test_background_service_running(self) -> bool:
        """Verify background ECS service exists and is active."""
        try:
            response = self.ecs.describe_services(
                cluster=self.cluster, services=[BACKGROUND_SERVICE]
            )
            if not response["services"]:
                print(f"Service {BACKGROUND_SERVICE} not found")
                return False

            service = response["services"][0]
            status = service["status"]
            print(f"Service status: {status}")

            if status != "ACTIVE":
                print(f"Expected ACTIVE, got {status}")
                return False

            return True
        except ClientError as e:
            print(f"Error: {e}")
            return False

    def test_background_task_count(self) -> bool:
        """Verify exactly 1 background task is running."""
        try:
            response = self.ecs.describe_services(
                cluster=self.cluster, services=[BACKGROUND_SERVICE]
            )
            if not response["services"]:
                return False

            service = response["services"][0]
            running = service["runningCount"]
            desired = service["desiredCount"]

            print(f"Running tasks: {running}")
            print(f"Desired tasks: {desired}")

            if desired != 1:
                print(f"Warning: desired_count should be 1, got {desired}")

            if running != 1:
                print(f"Expected 1 running task, got {running}")
                return False

            return True
        except ClientError as e:
            print(f"Error: {e}")
            return False

    def test_background_jobs_running(self) -> bool:
        """Verify background service is executing scheduled jobs.

        Checks for job execution logs which appear periodically (every few minutes).
        This test can be run at any time - it doesn't depend on startup timing.
        """
        # Job patterns that appear when scheduler is running
        job_patterns = [
            "PublishedExperimentSyncJob",
            "DataCleanupJob",
            "StorageReconciliationJob",
        ]

        print("Checking for job execution logs in background service...")
        for pattern in job_patterns:
            found = self._search_logs(BACKGROUND_LOG_GROUP, pattern)
            if found:
                print(f"Found job execution: {pattern}")
                return True

        print("No job execution logs found. Jobs may not have run yet.")
        print("Jobs run every 5-60 minutes depending on schedule.")
        return False

    def test_api_scheduler_disabled(self) -> bool:
        """Verify API service is NOT executing scheduled jobs.

        The API service should have DISABLE_BACKGROUND_SCHEDULER=1, so no job
        execution logs should appear. Only the background service runs jobs.
        """
        # Job patterns that should NOT appear in API logs
        job_patterns = [
            "PublishedExperimentSyncJob",
            "DataCleanupJob",
            "StorageReconciliationJob",
        ]

        print("Checking that API service is NOT running jobs...")
        for pattern in job_patterns:
            found = self._search_logs(API_LOG_GROUP, pattern)
            if found:
                print(f"ERROR: Found job execution in API service: {pattern}")
                print("API service should have scheduler disabled!")
                return False

        print("Confirmed: No job execution in API service (as expected)")
        return True

    def _search_logs(self, log_group: str, search_pattern: str) -> bool:
        """Search CloudWatch logs for a pattern in recent logs.

        Args:
            log_group: CloudWatch log group name
            search_pattern: Pattern to search for

        Returns:
            True if pattern found, False otherwise
        """
        try:
            # Query recent logs (last 2 hours to catch periodic job runs)
            end_time = int(datetime.now().timestamp() * 1000)
            start_time = int((datetime.now() - timedelta(hours=2)).timestamp() * 1000)

            response = self.logs.filter_log_events(
                logGroupName=log_group,
                startTime=start_time,
                endTime=end_time,
                filterPattern=search_pattern,
                limit=5,
            )

            return len(response.get("events", [])) > 0
        except ClientError as e:
            print(f"Error querying logs: {e}")
            return False

    def test_cloudwatch_alarms(self) -> bool:
        """Verify CloudWatch alarms are configured for background service."""
        expected_alarms = [
            "subscr-background-task-stopped",
            "subscr-background-cpu-high",
            "subscr-background-memory-high",
        ]

        try:
            response = self.cloudwatch.describe_alarms(AlarmNames=expected_alarms)
            found_alarms = [a["AlarmName"] for a in response["MetricAlarms"]]

            print(f"Expected alarms: {expected_alarms}")
            print(f"Found alarms: {found_alarms}")

            missing = set(expected_alarms) - set(found_alarms)
            if missing:
                print(f"Missing alarms: {list(missing)}")
                return False

            return True
        except ClientError as e:
            print(f"Error: {e}")
            return False

    def print_summary(self):
        """Print test summary."""
        print("=" * 60)
        print("Summary")
        print("=" * 60)

        passed = sum(1 for v in self.results.values() if v)
        total = len(self.results)

        for test_name, result in self.results.items():
            status = "PASS" if result else "FAIL"
            print(f"{test_name}: {status}")

        print()
        print(f"Results: {passed}/{total} tests passed")

        if passed == total:
            print("\nBackground service is properly configured!")
        else:
            print("\nSome tests failed - check the output above for details.")


def main():
    parser = argparse.ArgumentParser(description="Verify background service deployment")
    parser.add_argument(
        "--region",
        default=DEFAULT_REGION,
        help=f"AWS region (default: {DEFAULT_REGION})",
    )
    parser.add_argument(
        "--cluster",
        default=DEFAULT_CLUSTER,
        help=f"ECS cluster name (default: {DEFAULT_CLUSTER})",
    )
    args = parser.parse_args()

    tester = BackgroundServiceTester(args.region, args.cluster)
    success = tester.run_all_tests()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
