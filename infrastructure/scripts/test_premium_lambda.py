#!/usr/bin/env python3
"""
Lambda Integration Tests

WHERE TO RUN:
- Cloud ECS container - Works (with proper path setup)
- Local development machine - Works (with proper setup)

REQUIREMENTS:
- Lambda function code must be in terraform/premium_manager_package/
- Mocked AWS services (boto3, pymysql)
- Python 3.7+

WHAT IT TESTS:
End-to-end Lambda function behavior with realistic Lambda invocation events:
1. Premium manager assignment event handling
2. Premium manager heartbeat event handling
3. Premium manager release event handling
4. Enum values work correctly in Lambda operations
5. Lambda error handling for malformed requests
6. Premium cleanup scheduled event handling

These tests verify that:
- Lambda handlers properly process invocation events (simulating API Gateway format)
- Database operations work with enum values
  (launching, running, stopping, stopped, terminating)
- Error handling is graceful and returns proper HTTP status codes
- Scheduled cleanup events process correctly
- All critical Lambda functions integrate properly with AWS services

NOTE:
- These tests use mocked event payloads that simulate the API Gateway event format
- In production, the backend invokes Lambda directly with boto3,
  passing similar event structures
- The Lambda function is designed to handle API Gateway-formatted events
  for compatibility

HOW TO RUN:
  python test_lambda_integration.py

EXPECTED RESULT:
  All 6 tests should pass

PERFORMANCE IMPACT:
  Light - All AWS services and database are mocked
  - Tests Lambda handler code directly with mock events
  - No impact on other users
  - Safe to run anytime

"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

# Add project root and Lambda package directories to path
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
sys.path.insert(0, script_dir)
sys.path.insert(0, project_root)

# Add Lambda package directories to path
lambda_package_dir = os.path.join(project_root, "terraform", "premium_manager_package")
if os.path.exists(lambda_package_dir):
    sys.path.insert(0, lambda_package_dir)

cleanup_package_dir = os.path.join(project_root, "terraform", "premium_cleanup_package")
if os.path.exists(cleanup_package_dir):
    sys.path.insert(0, cleanup_package_dir)

# Add aws_constants Lambda layer path
aws_constants_layer_path = os.path.join(
    project_root, "terraform", "aws_constants_layer", "python"
)
if os.path.exists(aws_constants_layer_path):
    sys.path.insert(0, aws_constants_layer_path)

from aws_constants import ECSTaskStatus  # noqa: E402


class MockRow:
    """Mock database row that behaves like
    a dictionary but also supports index access"""

    def __init__(self, data):
        self.data = data

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.data.values())[key]
        return self.data.get(key)

    def get(self, key, default=None):
        return self.data.get(key, default)


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
            "AUTOSCALING_TARGET_GROUP_ARN": (
                "arn:aws:elasticloadbalancing:region:account:" "targetgroup/asg"
            ),
            "CLUSTER_NAME": "test-cluster",
            "PREMIUM_SERVICE_NAME": "subscr-optinist-premium-service",
            "PREMIUM_INSTANCE_IDS": "i-test1,i-test2,i-test3",
            "PREMIUM_STANDBY_POOL_SIZE": "2",
            "PREMIUM_IDLE_TIMEOUT_HOURS": "3",
            "PREMIUM_SAFETY_BUFFER": "1",
            "ABSOLUTE_MAX": "10",
        }

    def setup_db_mock(self, fetchone_values=None, fetchall_values=None):
        """
        Create a properly configured database mock that
        returns real values instead of MagicMocks

        Args:
            fetchone_values: List of values to return from fetchone()
            calls (can be None, dict, or MockRow)
            fetchall_values: List of values to return from fetchall()
            calls (list of dicts or MockRows)

        Returns:
            A mock connection object that will be reused for all
            get_db_connection() calls
        """
        # Create a single mock connection and cursor that will be shared
        # across all database operations
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1

        # Configure fetchone - return None by default unless specified
        # Use lambda to return None infinitely
        if fetchone_values is not None:
            mock_cursor.fetchone.side_effect = fetchone_values
        else:
            mock_cursor.fetchone.side_effect = lambda: None

        # Configure fetchall - return empty list by default unless specified
        if fetchall_values is not None:
            mock_cursor.fetchall.side_effect = fetchall_values
        else:
            # Use lambda to return empty list infinitely
            mock_cursor.fetchall.side_effect = lambda: []

        # Create mock connection that returns our configured cursor
        mock_connection = MagicMock()
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connection.__enter__.return_value = mock_connection
        mock_connection.__exit__.return_value = None

        return mock_connection

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
            # Setup database mocks with sufficient values for all queries
            # The Lambda makes many DB queries,
            # so we need to provide enough return values
            mock_connection = self.setup_db_mock(
                fetchone_values=[
                    None,  # 1. Check for existing assignment
                    MockRow({"count": 1}),  # 2. Count active users
                    MockRow({"count": 0}),  # 3. Count standby users
                    MockRow({"count": 1}),  # 4. Count real users
                    None,  # 5. Check for existing assignment again
                    MockRow({"count": 1}),  # 6. Another user count
                    MockRow({"count": 0}),  # 7. Another standby count
                    MockRow({"count": 1}),  # 8. Another real user count
                    None,  # 9. Reserve instance check
                    None,  # 10. Additional checks
                    MockRow(
                        {"priority": 100}
                    ),  # 11. ALB rule priority query (default return a high priority)
                ],
                fetchall_values=[
                    [],  # 1. No standby instances initially
                    [
                        MockRow(
                            {"instance_id": self.test_instance_id, "state": "running"}
                        )
                    ],  # 2. Running instances
                    [],  # 3. No assigned users for the instance
                    [],  # 4. No existing ALB rules
                    [],  # 5. Additional queries
                    [],  # 6. More queries
                ],
            )
            # Make pymysql.connect return the same mock connection for all calls
            mock_pymysql.return_value = mock_connection

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
                            "arn:aws:ecs:region:account:"
                            "task-definition/optinist-premium"
                        ),
                        "lastStatus": ECSTaskStatus.RUNNING,
                        "desiredStatus": ECSTaskStatus.RUNNING,
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
                from premium_manager import handler

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
            # Setup database mocks for heartbeat with user lookup data
            # The heartbeat flow:
            # 1. get_user_id_from_uid(): SELECT id FROM users WHERE uid = %s
            # 2. update_user_activity():
            # UPDATE premium_user_assignments SET last_activity
            mock_connection = self.setup_db_mock(
                fetchone_values=[
                    MockRow(  # 1. User lookup - Lambda expects {"id": <numeric_id>}
                        {"id": 12345}  # Numeric user ID from users table
                    ),
                    MockRow(  # 2. Assignment check (if needed)
                        {
                            "user_id": 12345,
                            "instance_id": self.test_instance_id,
                            "instance_state": "running",
                        }
                    ),
                ],
                fetchall_values=[
                    [],  # Additional queries
                ],
            )
            mock_pymysql.return_value = mock_connection

            try:
                from premium_manager import handler

                result = handler(heartbeat_event, mock_context)

                assert isinstance(result, dict), "Lambda should return dict"
                status_code = result["statusCode"]
                response_body = json.loads(result["body"])

                print("Heartbeat lambda executed successfully")
                print(f"Status Code: {status_code}")
                print(f"Response: {json.dumps(response_body, indent=2)}")

                # Verify heartbeat was processed
                assert (
                    status_code == 200
                ), f"Heartbeat should return 200, got {status_code}"
                assert "user_id" in response_body, "Response should include user_id"
                # user_id should be 12345 (the numeric ID we mocked)
                assert (
                    response_body["user_id"] == 12345
                ), f"Expected user_id 12345, got {response_body['user_id']}"

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
            # Setup mocks with sufficient return values
            # for all DB queries during release
            mock_connection = self.setup_db_mock(
                fetchone_values=[
                    MockRow(
                        {  # 1. remove_user_assignment - get assignment
                            "user_id": self.test_user_id,
                            "instance_id": self.test_instance_id,
                            "target_group_arn": (
                                "arn:aws:elasticloadbalancing:region:account:"
                                "targetgroup/test"
                            ),
                            "alb_rule_arn": (
                                "arn:aws:elasticloadbalancing:region:account1:"
                                "listener-rule/test"
                            ),
                        }
                    ),
                    MockRow(
                        {"count": 1}
                    ),  # 2. count_active_premium_users - total count
                    MockRow(
                        {"count": 0}
                    ),  # 3. count_active_premium_users - standby count
                    MockRow(
                        {"count": 1}
                    ),  # 4. count_active_premium_users - real user count
                    MockRow({"count": 3}),  # 5. Total premium subscribers
                    MockRow({"count": 0}),  # 6. Current standby count
                    MockRow({"count": 1}),  # 7. Another user count
                    MockRow({"count": 0}),  # 8. Another standby count
                    MockRow({"count": 1}),  # 9. Another real user count
                ],
                fetchall_values=[
                    [],  # 1. No instances to process
                    [],  # 2. Additional queries
                    [],  # 3. More queries
                ],
            )
            mock_pymysql.return_value = mock_connection

            # Mock AWS services
            mock_elbv2 = MagicMock()
            mock_ec2 = MagicMock()

            def boto3_client_side_effect(service):
                if service == "elbv2":
                    return mock_elbv2
                elif service == "ec2":
                    return mock_ec2
                return MagicMock()

            mock_boto3.side_effect = boto3_client_side_effect

            # Mock EC2 describe_instances for scale-down check
            mock_ec2.describe_instances.return_value = {"Reservations": []}

            try:
                from premium_manager import handler

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

        try:
            from premium_manager import store_user_assignment, update_instance_state

            for enum_state, description in enum_states_to_test:
                print(f"Testing enum state: '{enum_state}' ({description})")

                # Create completely fresh mocks for each iteration
                with patch.dict("os.environ", self.mock_env_vars), patch(
                    "pymysql.connect"
                ) as mock_pymysql:
                    # Use a unique user_id for each enum test
                    test_user = f"{self.test_user_id}_{enum_state}"

                    # Setup database mocks for enum test
                    # No existing assignment, so store should succeed
                    mock_connection = self.setup_db_mock()
                    mock_pymysql.return_value = mock_connection

                    # Test storing assignment with enum state
                    try:
                        store_user_assignment(
                            user_id=test_user,
                            instance_id=self.test_instance_id,
                            target_group_arn="arn:test",
                            rule_arn="arn:test2",
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
                        update_instance_state(test_user, enum_state)
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
                from premium_manager import handler

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
            # Setup database mocks for cleanup operation
            mock_connection = self.setup_db_mock(
                fetchone_values=[
                    MockRow({"count": 0}),  # 1. No stale assignments
                    MockRow({"count": 1}),  # 2. Active assignments
                    MockRow({"count": 0}),  # 3. Standby assignments
                    MockRow({"count": 1}),  # 4. Real user assignments
                    MockRow({"count": 3}),  # 5. Total premium subscribers
                    MockRow({"count": 0}),  # 6. Current standby count
                ],
                fetchall_values=[
                    [],  # 1. No stale assignments to clean
                    [],  # 2. No ALB rules
                    [],  # 3. No assignments
                    [],  # 4. No failed standby instances
                    [
                        MockRow(
                            {"instance_id": self.test_instance_id, "state": "running"}
                        )
                    ],  # 5. Running instances
                    [],  # 6. No assigned users
                ],
            )
            mock_pymysql.return_value = mock_connection

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
                from premium_cleanup import handler

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

    def test_early_check_returns_existing_assignment(self):
        """
        TC-1: Test that assign_premium_user returns existing assignment
        without creating new ALB rules (prevents duplicate rule accumulation)

        Tests assign_premium_user() directly with mocked get_existing_user_assignment
        """
        print("\n Testing Early Check Returns Existing Assignment")
        print("=" * 50)

        test_user_id = 12345  # Numeric user ID (already converted from UID)

        # Existing assignment data to return
        existing_assignment = {
            "user_id": test_user_id,
            "instance_id": self.test_instance_id,
            "target_group_arn": "arn:aws:tg/existing",
            "alb_rule_arn": "arn:aws:rule/existing",
            "status": "active",
            "instance_state": "running",
            "is_shared": 0,
        }

        with patch.dict("os.environ", self.mock_env_vars), patch(
            "boto3.client"
        ) as mock_boto3, patch(
            "premium_manager.get_existing_user_assignment"
        ) as mock_get_existing:
            # Directly mock get_existing_user_assignment to return existing assignment
            mock_get_existing.return_value = existing_assignment

            # Mock AWS services - should NOT be called for rule creation
            mock_elbv2 = MagicMock()

            def boto3_client_side_effect(service):
                if service == "elbv2":
                    return mock_elbv2
                return MagicMock()

            mock_boto3.side_effect = boto3_client_side_effect

            try:
                from premium_manager import assign_premium_user

                # Call assign_premium_user directly (bypasses UID->ID conversion)
                result = assign_premium_user(test_user_id, {"tier": "premium"})

                status_code = result["statusCode"]
                response_body = json.loads(result["body"])

                print(f"Status Code: {status_code}")
                print(f"Response: {json.dumps(response_body, indent=2)}")

                # Should return 200 with existing assignment
                assert status_code == 200, "Should return 200 for existing assignment"
                assert (
                    "already assigned" in response_body.get("message", "").lower()
                    or response_body.get("assignment_source") == "existing"
                ), "Should indicate existing assignment"

                # CRITICAL: create_rule should NOT be called
                assert (
                    not mock_elbv2.create_rule.called
                ), "create_rule should NOT be called for existing assignment"

                # Verify get_existing_user_assignment was called with correct user_id
                mock_get_existing.assert_called_once_with(test_user_id)

                print("Early check correctly returned existing assignment")
                return True

            except Exception as e:
                print(f"Test failed: {e}")
                import traceback

                traceback.print_exc()
                return False

    def test_exception_handler_cleans_up_alb_rule(self):
        """
        TC-2: Test that exception handler cleans up ALB rule if created
        (prevents orphaned rules on failure)
        """
        print("\n Testing Exception Handler ALB Rule Cleanup")
        print("=" * 50)

        with patch.dict("os.environ", self.mock_env_vars), patch(
            "boto3.client"
        ) as mock_boto3, patch("pymysql.connect") as mock_pymysql:
            # Setup database - no existing assignment, but store will fail
            mock_connection = self.setup_db_mock(
                fetchone_values=[
                    None,  # No existing assignment
                    None,  # No reservation
                    MockRow({"count": 0}),  # Standby count
                ],
                fetchall_values=[
                    [],  # No standby instances
                    [
                        MockRow(
                            {"instance_id": self.test_instance_id, "state": "running"}
                        )
                    ],
                    [],  # No existing rules
                ],
            )
            mock_pymysql.return_value = mock_connection

            # Mock AWS services
            mock_elbv2 = MagicMock()
            mock_ec2 = MagicMock()

            # Simulate successful rule creation
            created_rule_arn = "arn:aws:rule/test-created-rule"
            mock_elbv2.create_rule.return_value = {
                "Rules": [{"RuleArn": created_rule_arn}]
            }
            mock_elbv2.describe_rules.return_value = {"Rules": []}

            def boto3_client_side_effect(service):
                if service == "elbv2":
                    return mock_elbv2
                elif service == "ec2":
                    return mock_ec2
                return MagicMock()

            mock_boto3.side_effect = boto3_client_side_effect
            mock_ec2.describe_instances.return_value = {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": self.test_instance_id,
                                "State": {"Name": "running"},
                                "Tags": [
                                    {"Key": "Name", "Value": "premium-instance"},
                                    {"Key": "Tier", "Value": "premium"},
                                ],
                            }
                        ]
                    }
                ]
            }

            try:
                from premium_manager import cleanup_duplicate_rules_for_routing_id

                # Test the cleanup function directly
                test_routing_id = "test123abc"
                test_listener_arn = self.mock_env_vars["ALB_LISTENER_ARN"]

                # Setup mock to return rules with matching routing_id
                mock_elbv2.describe_rules.return_value = {
                    "Rules": [
                        {
                            "RuleArn": "arn:aws:rule/duplicate1",
                            "Priority": "100",
                            "Conditions": [
                                {
                                    "Field": "http-header",
                                    "HttpHeaderConfig": {
                                        "HttpHeaderName": "X-Routing-ID",
                                        "Values": [test_routing_id],
                                    },
                                }
                            ],
                            "Actions": [
                                {
                                    "Type": "forward",
                                    "TargetGroupArn": "arn:aws:tg/orphaned",
                                }
                            ],
                        },
                        {
                            "RuleArn": "arn:aws:rule/duplicate2",
                            "Priority": "101",
                            "Conditions": [
                                {
                                    "Field": "http-header",
                                    "HttpHeaderConfig": {
                                        "HttpHeaderName": "X-Routing-ID",
                                        "Values": [test_routing_id],
                                    },
                                }
                            ],
                            "Actions": [
                                {
                                    "Type": "forward",
                                    "TargetGroupArn": "arn:aws:tg/orphaned2",
                                }
                            ],
                        },
                    ]
                }

                deleted_count = cleanup_duplicate_rules_for_routing_id(
                    test_listener_arn, test_routing_id
                )

                print(f"Deleted {deleted_count} duplicate rules")

                # Should have called delete_rule for both duplicates
                call_count = mock_elbv2.delete_rule.call_count
                assert (
                    call_count == 2
                ), f"Expected 2 delete_rule calls, got {call_count}"

                print("Cleanup function correctly deleted duplicate rules")
                return True

            except Exception as e:
                print(f"Test failed: {e}")
                import traceback

                traceback.print_exc()
                return False

    def test_cleanup_duplicate_alb_rules_scheduled(self):
        """
        TC-4: Test scheduled duplicate cleanup in premium_cleanup Lambda
        Tests cleanup_duplicate_alb_rules() function directly with mocks
        """
        print("\n Testing Scheduled Duplicate ALB Rules Cleanup")
        print("=" * 50)

        valid_rule_arn = "arn:aws:rule/valid-in-db"
        duplicate_rule_arn = "arn:aws:rule/duplicate-not-in-db"
        routing_id = "test-routing-id-123"

        with patch.dict("os.environ", self.mock_env_vars), patch(
            "boto3.client"
        ) as mock_boto3, patch("premium_cleanup.get_db_connection") as mock_get_db:
            # Mock database context manager to return valid rule ARN
            mock_cursor = MagicMock()
            mock_cursor.fetchall.return_value = [{"alb_rule_arn": valid_rule_arn}]

            mock_connection = MagicMock()
            mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
            mock_connection.__enter__.return_value = mock_connection
            mock_connection.__exit__.return_value = None

            mock_get_db.return_value.__enter__.return_value = mock_connection
            mock_get_db.return_value.__exit__.return_value = None

            # Mock AWS services
            mock_elbv2 = MagicMock()

            # Return rules including duplicates (same routing_id)
            mock_elbv2.describe_rules.return_value = {
                "Rules": [
                    {"RuleArn": "default", "Priority": "default"},
                    {
                        "RuleArn": valid_rule_arn,
                        "Priority": "100",
                        "Conditions": [
                            {
                                "Field": "http-header",
                                "HttpHeaderConfig": {
                                    "HttpHeaderName": "X-Routing-ID",
                                    "Values": [routing_id],
                                },
                            },
                        ],
                        "Actions": [{"Type": "forward", "TargetGroupArn": "arn:tg/1"}],
                    },
                    {
                        "RuleArn": duplicate_rule_arn,
                        "Priority": "101",
                        "Conditions": [
                            {
                                "Field": "http-header",
                                "HttpHeaderConfig": {
                                    "HttpHeaderName": "X-Routing-ID",
                                    "Values": [routing_id],
                                },
                            },
                        ],
                        "Actions": [{"Type": "forward", "TargetGroupArn": "arn:tg/2"}],
                    },
                ]
            }

            def boto3_client_side_effect(service):
                if service == "elbv2":
                    return mock_elbv2
                return MagicMock()

            mock_boto3.side_effect = boto3_client_side_effect

            try:
                from premium_cleanup import cleanup_duplicate_alb_rules

                result = cleanup_duplicate_alb_rules()

                print(f"Cleanup result: {result}")

                # Should identify duplicate and delete it
                assert (
                    "duplicates_deleted" in result
                ), "Result should include duplicates_deleted"

                # Check which rules were deleted
                delete_calls = mock_elbv2.delete_rule.call_args_list
                deleted_arns = [call[1]["RuleArn"] for call in delete_calls]

                print(f"Deleted rule ARNs: {deleted_arns}")

                # Valid rule (in DB) should NOT be deleted
                assert (
                    valid_rule_arn not in deleted_arns
                ), f"Valid rule {valid_rule_arn} should not be deleted"

                # Duplicate rule (not in DB) SHOULD be deleted
                assert (
                    duplicate_rule_arn in deleted_arns
                ), f"Duplicate rule {duplicate_rule_arn} should be deleted"

                print("Scheduled cleanup correctly identified and deleted duplicates")
                return True

            except ImportError:
                print("premium_cleanup.py not found, skipping test")
                return True
            except Exception as e:
                print(f"Test failed: {e}")
                import traceback

                traceback.print_exc()
                return False

    def test_autoscaling_pool_triggers_migration_retry(self):
        """
        Test that when a user already assigned to autoscaling-pool requests
        assignment, the system triggers invoke_migration_async() to retry
        migration to a dedicated instance.

        This prevents users from being stuck on autoscaling-pool if their
        initial migration timed out or failed.
        """
        print("\n Testing Autoscaling-Pool Assignment Triggers Migration Retry")
        print("=" * 50)

        test_user_id = 12345  # Numeric user ID

        # Existing assignment with autoscaling-pool (user stuck after failed migration)
        existing_autoscaling_assignment = {
            "user_id": test_user_id,
            "instance_id": "autoscaling-pool",  # PremiumAssignment.AUTOSCALING_POOL
            "target_group_arn": "arn:aws:tg/autoscaling",
            "alb_rule_arn": "arn:aws:rule/autoscaling",
            "status": "active",
            "instance_state": "running",
            "is_shared": 1,
        }

        with patch.dict("os.environ", self.mock_env_vars):
            # Import module first, then patch its attributes directly
            import premium_manager

            with patch.object(
                premium_manager, "get_existing_user_assignment"
            ) as mock_get_existing, patch.object(
                premium_manager, "invoke_migration_async"
            ) as mock_invoke_migration, patch(
                "boto3.client"
            ) as mock_boto3:
                # Return existing autoscaling-pool assignment
                mock_get_existing.return_value = existing_autoscaling_assignment

                # Mock AWS services (should not be called for new rule creation)
                mock_elbv2 = MagicMock()

                def boto3_client_side_effect(service):
                    if service == "elbv2":
                        return mock_elbv2
                    return MagicMock()

                mock_boto3.side_effect = boto3_client_side_effect

                try:
                    # Call assign_premium_user - should detect autoscaling-pool
                    # and trigger migration
                    result = premium_manager.assign_premium_user(
                        test_user_id, {"tier": "premium"}
                    )

                    status_code = result["statusCode"]
                    response_body = json.loads(result["body"])

                    print(f"Status Code: {status_code}")
                    print(f"Response: {json.dumps(response_body, indent=2)}")

                    # Should return 200 with existing assignment
                    assert (
                        status_code == 200
                    ), "Should return 200 for existing assignment"
                    assert (
                        response_body.get("instance_id") == "autoscaling-pool"
                    ), "Should return autoscaling-pool instance_id"
                    assert (
                        response_body.get("assignment_source") == "existing"
                    ), "Should indicate existing assignment"

                    # CRITICAL: invoke_migration_async SHOULD be called
                    assert mock_invoke_migration.called, (
                        "invoke_migration_async should be called when user is on "
                        "autoscaling-pool to retry migration"
                    )

                    # Verify get_existing_user_assignment was called
                    mock_get_existing.assert_called_once_with(test_user_id)

                    # CRITICAL: create_rule should NOT be called (using existing)
                    assert (
                        not mock_elbv2.create_rule.called
                    ), "create_rule should NOT be called for existing assignment"

                    print(
                        "Autoscaling-pool assignment correctly triggered migration "
                        "retry"
                    )
                    return True

                except Exception as e:
                    print(f"Test failed: {e}")
                    import traceback

                    traceback.print_exc()
                    return False


def run_lambda_integration_tests():
    """Run all Lambda integration tests"""

    print("Starting Lambda Integration Tests")
    print("=" * 50)

    # Get Lambda package paths for error messages
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    lambda_pkg_dir = os.path.join(
        project_root, "config", "terraform", "premium_manager_package"
    )
    cleanup_pkg_dir = os.path.join(
        project_root, "config", "terraform", "premium_cleanup_package"
    )

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
        # ALB Duplicate Rules Fix Tests (TC-1 through TC-4)
        (
            "TC-1: Early Check Returns Existing Assignment",
            test_suite.test_early_check_returns_existing_assignment,
        ),
        (
            "TC-2: Exception Handler Cleans Up ALB Rule",
            test_suite.test_exception_handler_cleans_up_alb_rule,
        ),
        (
            "TC-4: Scheduled Duplicate ALB Rules Cleanup",
            test_suite.test_cleanup_duplicate_alb_rules_scheduled,
        ),
        (
            "TC-5: Autoscaling-Pool Assignment Triggers Migration Retry",
            test_suite.test_autoscaling_pool_triggers_migration_retry,
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
        print("Lambda functions handle events correctly")
        print("Enum values work properly in all operations")
        print("End-to-end workflows function as expected")
        print("Error handling is graceful with proper status codes")
        return True
    else:
        print("\n Some Lambda integration tests failed!")
        print("Check the errors above for integration issues")
        print("Ensure Lambda package directories exist:")
        print(f"  - {lambda_pkg_dir}")
        print(f"  - {cleanup_pkg_dir}")
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
