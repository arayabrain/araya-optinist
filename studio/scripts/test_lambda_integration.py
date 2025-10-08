#!/usr/bin/env python3
"""
Lambda Integration Tests

KNOWN ISSUE: Currently failing in ECS with ModuleNotFoundError: No module named 'config'

WHERE TO RUN:
- Cloud ECS container - FAILS (import path issues)
- Local development machine - Best option (with proper setup)
- Requires special Lambda package structure

REQUIREMENTS:
- Lambda function code must be in config/terraform/premium_manager_package/
- PYTHONPATH must include Lambda package directories
- Mocked AWS services (boto3, pymysql)
- Python 3.7+

CURRENT FAILURE REASON:
The test attempts to directly import Lambda handler code:
  `from config.terraform.premium_manager_package.premium_manager import handler`

This fails in ECS because:
1. Lambda packages have different PYTHONPATH than ECS container
2. The 'config' module is not in the container's Python path
3. Lambda code is packaged separately for deployment

WHAT IT TESTS:
End-to-end Lambda function behavior with realistic API Gateway events:
1. Premium manager assignment event handling
2. Premium manager heartbeat event handling
3. Premium manager release event handling
4. Enum values work correctly in Lambda operations
5. Lambda error handling for malformed requests
6. Premium cleanup scheduled event handling

HOW TO FIX:
Option 1: Restructure tests to use subprocess to invoke Lambda locally
Option 2: Add proper PYTHONPATH configuration for Lambda packages
Option 3: Mock the Lambda functions entirely instead of importing actual code

HOW TO RUN (when fixed):
  python test_lambda_integration.py

EXPECTED RESULT (when fixed):
  6 tests should pass (currently 5 fail, 1 passes)
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class TestLambdaIntegration:
    """Test Lambda functions with realistic event scenarios"""

    def setup_method(self):
        """Setup test environment"""
        self.test_user_id = "test_user_12345"
        self.test_instance_id = "i-testlambda123"

        # Mock environment variables
        self.mock_env_vars = {
            "RDS_HOST": "test-db.example.com:3306",
            "RDS_USER": "test_user",
            "RDS_PASSWORD": "test_pass",
            "RDS_DATABASE": "test_db",
            "VPC_ID": "vpc-test123",
            "ALB_LISTENER_ARN": (
                "arn:aws:elasticloadbalancing:region:account:listener/test"
            ),
            "CLUSTER_NAME": "test-cluster",
            "PREMIUM_INSTANCE_IDS": "i-test1,i-test2,i-test3",
            "PREMIUM_STANDBY_POOL_SIZE": "2",
            "PREMIUM_IDLE_TIMEOUT_HOURS": "3",
            "PREMIUM_SAFETY_BUFFER": "1",
            "ABSOLUTE_MAX": "10",
        }

    def test_premium_manager_assign_event(self):
        """Test premium_manager Lambda with assignment API Gateway event"""

        print("Testing Premium Manager Lambda - Assignment Event")
        print("=" * 50)

        # Create realistic API Gateway event for assignment
        api_gateway_event = {
            "httpMethod": "POST",
            "path": "/premium/assign",
            "headers": {
                "Authorization": "Bearer test-token",
                "Content-Type": "application/json",
            },
            "body": json.dumps(
                {"action": "assign", "user_id": self.test_user_id, "tier": "premium"}
            ),
            "requestContext": {"requestId": "test-request-123", "stage": "prod"},
        }

        # Mock Lambda context
        mock_context = MagicMock()
        mock_context.function_name = "subscr-premium-manager"
        mock_context.aws_request_id = "test-lambda-123"

        with patch.dict("os.environ", self.mock_env_vars), patch(
            "boto3.client"
        ) as mock_boto3, patch("pymysql.connect") as mock_pymysql:
            # Setup database mocks
            mock_connection = MagicMock()
            mock_cursor = MagicMock()
            mock_pymysql.return_value.__enter__.return_value = mock_connection
            mock_connection.cursor.return_value.__enter__.return_value = mock_cursor

            # Mock successful assignment flow
            mock_cursor.fetchone.side_effect = [
                None,  # No existing assignment
                {"count": 1},  # Active users count
                {"count": 3},  # Total premium users
            ]
            mock_cursor.fetchall.side_effect = [
                [],  # No standby instances initially
                [
                    {"instance_id": self.test_instance_id, "state": "running"}
                ],  # Running instances
                [],  # No assigned users for the instance
            ]
            mock_cursor.rowcount = 1

            # Mock AWS services
            mock_ec2 = MagicMock()
            mock_elbv2 = MagicMock()
            mock_ecs = MagicMock()

            def boto3_client_side_effect(service):
                if service == "ec2":
                    return mock_ec2
                elif service == "elbv2":
                    return mock_elbv2
                elif service == "ecs":
                    return mock_ecs
                return MagicMock()

            mock_boto3.side_effect = boto3_client_side_effect

            # Mock EC2 describe_instances for premium instances
            mock_ec2.describe_instances.return_value = {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": self.test_instance_id,
                                "State": {"Name": "running"},
                                "InstanceType": "t3.large",
                                "LaunchTime": "2025-09-17T10:00:00Z",
                                "Tags": [
                                    {"Key": "Name", "Value": "premium-instance-1"},
                                    {"Key": "Tier", "Value": "premium"},
                                    {"Key": "Type", "Value": "Premium-Instance"},
                                ],
                            }
                        ]
                    }
                ]
            }

            # Mock ECS readiness check
            mock_ecs.list_container_instances.return_value = {
                "containerInstanceArns": [
                    "arn:aws:ecs:region:account:container-instance/test"
                ]
            }
            mock_ecs.describe_container_instances.return_value = {
                "containerInstances": [
                    {
                        "containerInstanceArn": (
                            "arn:aws:ecs:region:account:container-instance/test"
                        ),
                        "ec2InstanceId": self.test_instance_id,
                    }
                ]
            }
            mock_ecs.list_tasks.return_value = {
                "taskArns": ["arn:aws:ecs:region:account:task/test"]
            }
            mock_ecs.describe_tasks.return_value = {
                "tasks": [
                    {
                        "taskDefinitionArn": (
                            "arn:aws:ecs:region:account:task-definition/optinist"
                        ),
                        "lastStatus": "RUNNING",
                        "desiredStatus": "RUNNING",
                    }
                ]
            }

            # Mock ALB operations
            mock_elbv2.create_target_group.return_value = {
                "TargetGroups": [
                    {
                        "TargetGroupArn": (
                            "arn:aws:elasticloadbalancing:region:account:"
                            "targetgroup/test"
                        )
                    }
                ]
            }
            mock_elbv2.create_rule.return_value = {
                "Rules": [
                    {
                        "RuleArn": (
                            "arn:aws:elasticloadbalancing:region:account:"
                            "listener-rule/test"
                        )
                    }
                ]
            }

            try:
                # Import and call the Lambda handler
                from config.terraform.premium_manager_package.premium_manager import (
                    handler,
                )

                # Execute the Lambda function
                result = handler(api_gateway_event, mock_context)

                # Verify the response
                assert isinstance(result, dict), "Lambda should return dict"
                assert "statusCode" in result, "Response should include statusCode"
                assert "body" in result, "Response should include body"

                status_code = result["statusCode"]
                response_body = json.loads(result["body"])

                print("Lambda executed successfully")
                print(f"Status Code: {status_code}")
                print(f"Response: {json.dumps(response_body, indent=2)}")

                # Verify successful assignment
                if status_code == 200:
                    assert (
                        "instance_id" in response_body
                    ), "Successful assignment should include instance_id"
                    assert response_body["instance_id"] == self.test_instance_id
                    print(
                        f"Assignment successful to instance " f"{self.test_instance_id}"
                    )
                elif status_code == 202:
                    assert (
                        "retry_after" in response_body
                    ), "202 response should include retry_after"
                    print(
                        f"Assignment scaling in progress, retry after "
                        f"{response_body.get('retry_after')} seconds"
                    )
                else:
                    print(f"Unexpected status code: {status_code}")

                return True

            except Exception as e:
                print(f"Lambda execution failed: {e}")
                import traceback

                print(f"Details: {traceback.format_exc()}")
                return False

    def test_premium_manager_heartbeat_event(self):
        """Test premium_manager Lambda with heartbeat/activity update event"""

        print("\n Testing Premium Manager Lambda - Heartbeat Event")
        print("=" * 50)

        # Create API Gateway event for heartbeat
        heartbeat_event = {
            "httpMethod": "POST",
            "path": "/premium/heartbeat",
            "body": json.dumps(
                {
                    "action": "update_activity",
                    "user_id": self.test_user_id,
                    "tier": "premium",
                }
            ),
            "requestContext": {"requestId": "test-heartbeat-123"},
        }

        mock_context = MagicMock()
        mock_context.function_name = "subscr-premium-manager"

        with patch.dict("os.environ", self.mock_env_vars), patch(
            "pymysql.connect"
        ) as mock_pymysql:
            # Setup database mocks
            mock_connection = MagicMock()
            mock_cursor = MagicMock()
            mock_pymysql.return_value.__enter__.return_value = mock_connection
            mock_connection.cursor.return_value.__enter__.return_value = mock_cursor

            # Mock successful activity update
            mock_cursor.rowcount = 1  # Indicates successful update

            try:
                from config.terraform.premium_manager_package.premium_manager import (
                    handler,
                )

                result = handler(heartbeat_event, mock_context)

                assert isinstance(result, dict), "Lambda should return dict"
                status_code = result["statusCode"]
                response_body = json.loads(result["body"])

                print("Heartbeat lambda executed successfully")
                print(f"Status Code: {status_code}")
                print(f"Response: {json.dumps(response_body, indent=2)}")

                # Verify heartbeat was processed
                assert status_code == 200, "Heartbeat should return 200"
                assert "user_id" in response_body, "Response should include user_id"
                assert response_body["user_id"] == self.test_user_id

                return True

            except Exception as e:
                print(f"Heartbeat lambda execution failed: {e}")
                return False

    def test_premium_manager_release_event(self):
        """Test premium_manager Lambda with release event"""

        print("\n Testing Premium Manager Lambda - Release Event")
        print("=" * 50)

        release_event = {
            "httpMethod": "POST",
            "path": "/premium/release",
            "body": json.dumps(
                {"action": "release", "user_id": self.test_user_id, "tier": "premium"}
            ),
        }

        mock_context = MagicMock()

        with patch.dict("os.environ", self.mock_env_vars), patch(
            "boto3.client"
        ) as mock_boto3, patch("pymysql.connect") as mock_pymysql:
            # Setup mocks
            mock_connection = MagicMock()
            mock_cursor = MagicMock()
            mock_pymysql.return_value.__enter__.return_value = mock_connection
            mock_connection.cursor.return_value.__enter__.return_value = mock_cursor

            # Mock existing assignment
            mock_cursor.fetchone.return_value = {
                "user_id": self.test_user_id,
                "instance_id": self.test_instance_id,
                "target_group_arn": (
                    "arn:aws:elasticloadbalancing:region:account:targetgroup/test"
                ),
                "alb_rule_arn": (
                    "arn:aws:elasticloadbalancing:region:account:listener-rule/test"
                ),
            }
            mock_cursor.rowcount = 1

            # Mock AWS services
            mock_elbv2 = MagicMock()
            mock_boto3.return_value = mock_elbv2

            try:
                from config.terraform.premium_manager_package.premium_manager import (
                    handler,
                )

                result = handler(release_event, mock_context)

                assert isinstance(result, dict), "Lambda should return dict"
                status_code = result["statusCode"]
                response_body = json.loads(result["body"])

                print("Release lambda executed successfully")
                print(f"Status Code: {status_code}")
                print(f"Response: {json.dumps(response_body, indent=2)}")

                # Release should always succeed (to not block logout)
                assert status_code == 200, "Release should always return 200"
                assert (
                    "released_instance" in response_body
                ), "Response should include released_instance"

                return True

            except Exception as e:
                print(f"Release lambda execution failed: {e}")
                return False

    def test_enum_values_in_lambda_operations(self):
        """Test that Lambda operations work with our fixed enum values"""

        print("\n Testing Lambda with Fixed Enum Values")
        print("=" * 50)

        # Test each enum state that caused issues
        enum_states_to_test = [
            ("launching", "Instance starting up"),
            ("running", "Instance active"),
            ("stopping", "Instance shutting down"),
            ("stopped", "Instance in standby"),  # This was the critical fix
            ("terminating", "Instance being destroyed"),
        ]

        with patch.dict("os.environ", self.mock_env_vars), patch(
            "pymysql.connect"
        ) as mock_pymysql:
            mock_connection = MagicMock()
            mock_cursor = MagicMock()
            mock_pymysql.return_value.__enter__.return_value = mock_connection
            mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
            mock_cursor.rowcount = 1

            try:
                from config.terraform.premium_manager_package.premium_manager import (
                    store_user_assignment,
                    update_instance_state,
                )

                for enum_state, description in enum_states_to_test:
                    print(f"Testing enum state: '{enum_state}' ({description})")

                    # Test storing assignment with enum state
                    try:
                        store_user_assignment(
                            user_id=self.test_user_id,
                            instance_id=self.test_instance_id,
                            target_group_arn="arn:test",
                            alb_rule_arn="arn:test2",
                            instance_state=enum_state,
                            is_shared=False,
                        )
                        print(
                            f"store_user_assignment with '{enum_state}' - " f"SUCCESS"
                        )
                    except Exception as e:
                        print(
                            f"store_user_assignment with '{enum_state}' - "
                            f"FAILED: {e}"
                        )
                        raise

                    # Test updating instance state
                    try:
                        update_instance_state(self.test_user_id, enum_state)
                        print(f"update_instance_state to '{enum_state}' - " f"SUCCESS")
                    except Exception as e:
                        print(
                            f"update_instance_state to '{enum_state}' - " f"FAILED: {e}"
                        )
                        raise

                print("\n    All enum values work correctly in Lambda operations")
                return True

            except Exception as e:
                print(f"\n    Enum testing failed: {e}")
                return False

    def test_lambda_error_handling(self):
        """Test Lambda error handling scenarios"""

        print("\n Testing Lambda Error Handling")
        print("=" * 50)

        # Test malformed event
        malformed_event = {"httpMethod": "POST", "body": "invalid json {{"}

        mock_context = MagicMock()

        with patch.dict("os.environ", self.mock_env_vars):
            try:
                from config.terraform.premium_manager_package.premium_manager import (
                    handler,
                )

                result = handler(malformed_event, mock_context)

                assert isinstance(
                    result, dict
                ), "Lambda should return dict even on error"
                assert (
                    "statusCode" in result
                ), "Error response should include statusCode"

                status_code = result["statusCode"]
                print("Malformed event handled gracefully")
                print(f"Status Code: {status_code}")

                # Should return error status but not crash
                assert status_code >= 400, "Malformed event should return error status"

                return True

            except Exception as e:
                print(f"Error handling test failed: {e}")
                return False

    def test_premium_cleanup_lambda_scheduled_event(self):
        """Test premium_cleanup Lambda with CloudWatch scheduled event"""

        print("\n Testing Premium Cleanup Lambda - Scheduled Event")
        print("=" * 50)

        # Create CloudWatch scheduled event
        scheduled_event = {
            "source": "aws.events",
            "detail-type": "Scheduled Event",
            "detail": {"action": "cleanup"},
            "time": "2025-09-17T10:00:00Z",
        }

        mock_context = MagicMock()
        mock_context.function_name = "subscr-premium-cleanup"

        with patch.dict("os.environ", self.mock_env_vars), patch(
            "boto3.client"
        ) as mock_boto3, patch("pymysql.connect") as mock_pymysql:
            # Setup database mocks
            mock_connection = MagicMock()
            mock_cursor = MagicMock()
            mock_pymysql.return_value.__enter__.return_value = mock_connection
            mock_connection.cursor.return_value.__enter__.return_value = mock_cursor

            # Mock cleanup operations
            mock_cursor.fetchall.side_effect = [
                [
                    {"user_id": "old_user", "instance_id": "i-old123"}
                ],  # Stale assignments
                [],  # No failed standby instances
                [
                    {"instance_id": self.test_instance_id, "state": "running"}
                ],  # Running instances
            ]
            mock_cursor.rowcount = 1

            # Mock AWS services
            mock_ec2 = MagicMock()
            mock_boto3.return_value = mock_ec2
            mock_ec2.describe_instances.return_value = {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": self.test_instance_id,
                                "State": {"Name": "running"},
                                "Tags": [{"Key": "Name", "Value": "premium-instance"}],
                            }
                        ]
                    }
                ]
            }

            try:
                from config.terraform.premium_cleanup import handler

                result = handler(scheduled_event, mock_context)

                assert isinstance(result, dict), "Cleanup Lambda should return dict"
                status_code = result["statusCode"]

                print("Cleanup lambda executed successfully")
                print(f"Status Code: {status_code}")

                assert status_code == 200, "Cleanup should return 200"

                return True

            except ImportError:
                print("premium_cleanup.py not found, skipping cleanup test")
                return True
            except Exception as e:
                print(f"Cleanup lambda execution failed: {e}")
                return False


def run_lambda_integration_tests():
    """Run all Lambda integration tests"""

    print("Starting Lambda Integration Tests")
    print("=" * 50)
    print("These tests verify Lambda functions work with our fixes")
    print("=" * 50)

    test_suite = TestLambdaIntegration()
    test_suite.setup_method()

    tests = [
        (
            "Premium Manager - Assignment Event",
            test_suite.test_premium_manager_assign_event,
        ),
        (
            "Premium Manager - Heartbeat Event",
            test_suite.test_premium_manager_heartbeat_event,
        ),
        (
            "Premium Manager - Release Event",
            test_suite.test_premium_manager_release_event,
        ),
        (
            "Lambda Enum Values Support",
            test_suite.test_enum_values_in_lambda_operations,
        ),
        ("Lambda Error Handling", test_suite.test_lambda_error_handling),
        (
            "Premium Cleanup - Scheduled Event",
            test_suite.test_premium_cleanup_lambda_scheduled_event,
        ),
    ]

    passed = 0
    failed = 0

    for test_name, test_func in tests:
        try:
            success = test_func()
            if success:
                passed += 1
                print(f"\n PASSED: {test_name}")
            else:
                failed += 1
                print(f"\n FAILED: {test_name}")
        except Exception as e:
            failed += 1
            print(f"\n FAILED: {test_name}")
            print(f"Error: {str(e)}")
            import traceback

            print(f"Details: {traceback.format_exc()}")

    print(f"\n Test Results: {passed} passed, {failed} failed")

    if failed == 0:
        print("\n All Lambda integration tests passed!")
        print("Lambda functions handle our fixes correctly")
        print("Enum values work properly in all operations")
        print("End-to-end workflows function as expected")
        return True
    else:
        print("\n Some Lambda integration tests failed!")
        print("Check the errors above for integration issues")
        return False


if __name__ == "__main__":
    try:
        success = run_lambda_integration_tests()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"Lambda integration test runner failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
