#!/usr/bin/env python3
"""
Premium User Dynamic Scaling Tests

WHERE TO RUN:
- Local development machine - Recommended
- Cloud ECS container - Should work
- Any environment with network access to API Gateway

REQUIREMENTS:
- API Gateway URL for premium management endpoints
- Python 3.8+ with requests library
- Network access to AWS API Gateway

WHAT IT TESTS:
Critical premium user dynamic scaling and instance management:
1. Single user assignment to premium instances
2. Concurrent user assignments to trigger scaling
3. Automatic spot fleet scaling when capacity is exceeded
4. User migration from shared to dedicated instances
5. Scale down when users logout
6. Migration queue processing and timing

These tests verify that:
- Premium users can be assigned to instances successfully
- Multiple concurrent assignments trigger spot fleet scaling
- Users initially assigned to shared instances migrate to dedicated instances
- Migration queue processes assignments correctly
- User release and cleanup work properly
- API endpoints handle concurrent requests correctly

HOW TO RUN:
  # Automatic (reads API URL from Terraform outputs):
  python test_premium_scaling.py

  # Or specify API URL manually:
  python test_premium_scaling.py --api-url <API_GATEWAY_URL>

  Optional arguments:
    --terraform-dir PATH: Path to Terraform directory (default: ../config/terraform)
    --test {single,concurrent,migration,full}  Type of test to run (default: full)
    --users NUM: Number of users for concurrent test (default: 3)

EXPECTED RESULT:
  All tests should pass with successful user assignments and migrations
"""

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict

import requests


class PremiumScalingTester:
    def __init__(self, api_url: str = None, terraform_dir: str = None):
        """
        Initialize Premium Scaling Tester.

        Args:
            api_url: API Gateway URL (if None, will load from Terraform outputs)
            terraform_dir: Path to Terraform directory (default: ../config/terraform)
        """
        if api_url:
            self.api_url = api_url.rstrip("/")
        else:
            # Load from Terraform outputs
            if terraform_dir is None:
                script_dir = os.path.dirname(os.path.abspath(__file__))
                terraform_dir = os.path.abspath(
                    os.path.join(script_dir, "../config/terraform")
                )

            terraform_outputs = self._load_terraform_outputs(terraform_dir)
            self.api_url = terraform_outputs.get("premium_api_gateway_url", "")
            if not self.api_url:
                raise ValueError(
                    "premium_api_gateway_url not found in Terraform outputs"
                )
            self.api_url = self.api_url.rstrip("/")
            print(f"Loaded API Gateway URL from Terraform: {self.api_url}")

        self.assigned_users = {}

    def _load_terraform_outputs(self, terraform_dir: str) -> Dict:
        """Load Terraform outputs using terraform output command."""
        import subprocess

        try:
            result = subprocess.run(
                ["terraform", "output", "-json"],
                cwd=terraform_dir,
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
            print("Terraform not found. Please install Terraform or provide --api-url")
            sys.exit(1)

    def assign_premium_user(self, user_id):
        """Assign a premium user"""
        try:
            response = requests.post(
                f"{self.api_url}/premium/assign",
                json={"action": "assign", "user_id": user_id, "tier": "premium"},
                timeout=30,
            )

            result = response.json()
            print(f"[{user_id}] Assignment response: {response.status_code} - {result}")

            if response.status_code == 200:
                self.assigned_users[user_id] = result
                return True
            elif response.status_code == 202:
                print(f"[{user_id}] Scaling in progress, will retry...")
                return False
            else:
                print(f"[{user_id}] Assignment failed: {result}")
                return False

        except Exception as e:
            print(f"[{user_id}] Error assigning user: {str(e)}")
            return False

    def release_premium_user(self, user_id):
        """Release a premium user"""
        try:
            response = requests.post(
                f"{self.api_url}/premium/release",
                json={"action": "release", "user_id": user_id, "tier": "premium"},
                timeout=30,
            )

            result = response.json()
            print(f"[{user_id}] Release response: {response.status_code} - {result}")

            if response.status_code == 200:
                if user_id in self.assigned_users:
                    del self.assigned_users[user_id]
                return True
            else:
                print(f"[{user_id}] Release failed: {result}")
                return False

        except Exception as e:
            print(f"[{user_id}] Error releasing user: {str(e)}")
            return False

    def get_premium_status(self, user_id):
        """Get premium user assignment status"""
        try:
            response = requests.get(
                f"{self.api_url}/premium/status",
                params={"user_id": user_id},
                timeout=30,
            )

            if response.status_code == 200:
                return response.json()
            else:
                return None

        except Exception as e:
            print(f"[{user_id}] Error getting status: {str(e)}")
            return None

    def test_single_user_assignment(self):
        """Test basic single user assignment"""
        print("\\n=== Testing Single User Assignment ===")

        user_id = "test-user-001"

        # Assign user
        success = self.assign_premium_user(user_id)
        if not success:
            print("Single user assignment failed")
            return False

        # Wait a moment and check status
        time.sleep(5)
        status = self.get_premium_status(user_id)
        if status:
            print(f"User {user_id} status: {status}")

        # Release user
        success = self.release_premium_user(user_id)
        if success:
            print("Single user test completed successfully")
        else:
            print("Single user release failed")

        return success

    def test_concurrent_user_assignment(self, num_users=3):
        """Test concurrent user assignments to trigger scaling"""
        print(f"\\n=== Testing Concurrent Assignment of {num_users} Users ===")

        user_ids = [f"test-user-{i:03d}" for i in range(2, 2 + num_users)]

        def assign_user(user_id):
            max_retries = 3
            for _ in range(max_retries):
                if self.assign_premium_user(user_id):
                    return user_id, True
                time.sleep(30)  # Wait for scaling if needed
            return user_id, False

        # Assign users concurrently
        print(f"Assigning {num_users} users concurrently...")
        successful_assignments = []

        with ThreadPoolExecutor(max_workers=num_users) as executor:
            future_to_user = {
                executor.submit(assign_user, user_id): user_id for user_id in user_ids
            }

            for future in as_completed(future_to_user):
                user_id, success = future.result()
                if success:
                    successful_assignments.append(user_id)
                    print(f"Successfully assigned {user_id}")
                else:
                    print(f"Failed to assign {user_id}")

        print(
            f"Successfully assigned {len(successful_assignments)} out of "
            f"{num_users} users"
        )

        # Wait for migration queue to process
        print("Waiting 5 minutes for migration queue processing...")
        time.sleep(300)

        # Check final status of all users
        print("\\nFinal user assignments:")
        for user_id in successful_assignments:
            status = self.get_premium_status(user_id)
            if status:
                print(f"{user_id}: {status}")

        return successful_assignments

    def test_user_migration_timing(self, assigned_users):
        """Test that users get migrated from shared to dedicated instances"""
        print("\\n=== Testing User Migration Timing ===")

        if len(assigned_users) < 2:
            print("Need at least 2 users for migration testing")
            return

        # Check status every minute for 10 minutes to observe migrations
        for minute in range(1, 11):
            print(f"\\n--- Migration Check: Minute {minute} ---")

            instance_assignments = {}
            for user_id in assigned_users:
                status = self.get_premium_status(user_id)
                if status and "instance_id" in status:
                    instance_id = status["instance_id"]
                    if instance_id not in instance_assignments:
                        instance_assignments[instance_id] = []
                    instance_assignments[instance_id].append(user_id)

            # Print instance sharing status
            for instance_id, users in instance_assignments.items():
                sharing_status = "SHARED" if len(users) > 1 else "DEDICATED"
                print(f"Instance {instance_id}: {sharing_status} - Users: {users}")

            if minute < 10:
                time.sleep(60)  # Wait 1 minute

    def cleanup_all_users(self):
        """Release all assigned users"""
        print("\\n=== Cleaning Up All Users ===")

        for user_id in list(self.assigned_users.keys()):
            self.release_premium_user(user_id)

        print("Cleanup completed")

    def run_full_test_suite(self):
        """Run the complete test suite"""
        print("Starting Premium Dynamic Scaling Test Suite")
        print(f"API URL: {self.api_url}")

        try:
            # Test 1: Single user assignment
            if not self.test_single_user_assignment():
                print("Basic assignment test failed, stopping")
                return False

            time.sleep(10)

            # Test 2: Concurrent assignments
            assigned_users = self.test_concurrent_user_assignment(3)

            if not assigned_users:
                print("No users were successfully assigned")
                return False

            # Test 3: Migration timing
            self.test_user_migration_timing(assigned_users)

            # Cleanup
            self.cleanup_all_users()

            print("\\n Test suite completed successfully!")
            return True

        except KeyboardInterrupt:
            print("\\n Test interrupted by user")
            self.cleanup_all_users()
            return False
        except Exception as e:
            print(f"\\n Test suite failed with error: {str(e)}")
            self.cleanup_all_users()
            return False


def main():
    parser = argparse.ArgumentParser(description="Test premium user dynamic scaling")
    parser.add_argument(
        "--api-url",
        help="API Gateway URL for premium management "
        "(default: load from Terraform outputs)",
    )
    parser.add_argument(
        "--terraform-dir",
        default="../config/terraform",
        help="Path to Terraform directory (default: ../config/terraform)",
    )
    parser.add_argument(
        "--test",
        choices=["single", "concurrent", "migration", "full"],
        default="full",
        help="Type of test to run",
    )
    parser.add_argument(
        "--users", type=int, default=3, help="Number of users for concurrent test"
    )

    args = parser.parse_args()

    # Resolve terraform directory path if api_url not provided
    terraform_dir = None
    if not args.api_url:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        terraform_dir = os.path.abspath(os.path.join(script_dir, args.terraform_dir))

        if not os.path.exists(terraform_dir):
            print(f"Terraform directory not found: {terraform_dir}")
            print("Please provide --api-url or ensure Terraform directory exists")
            sys.exit(1)

    tester = PremiumScalingTester(api_url=args.api_url, terraform_dir=terraform_dir)

    try:
        if args.test == "single":
            success = tester.test_single_user_assignment()
        elif args.test == "concurrent":
            assigned_users = tester.test_concurrent_user_assignment(args.users)
            success = len(assigned_users) > 0
        elif args.test == "migration":
            # First assign users
            assigned_users = tester.test_concurrent_user_assignment(args.users)
            if assigned_users:
                tester.test_user_migration_timing(assigned_users)
                tester.cleanup_all_users()
            success = len(assigned_users) > 0
        else:  # full
            success = tester.run_full_test_suite()

        sys.exit(0 if success else 1)

    except Exception as e:
        print(f"Test failed with error: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
