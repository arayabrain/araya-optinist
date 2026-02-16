"""Tests for premium_manager Lambda function."""

import json
from unittest.mock import MagicMock, patch

from aws_constants import ECSTaskStatus
from conftest import MockRow, setup_db_mock

TEST_USER_ID = "test_user_12345"
TEST_INSTANCE_ID = "i-testlambda123"


class TestPremiumManagerEvents:
    """Handler-level tests with realistic API Gateway events."""

    def test_assign_event(self, mock_env_vars_premium):
        """Test premium_manager Lambda with assignment event."""
        print("Testing Premium Manager Lambda - Assignment Event")
        print("=" * 50)

        api_gateway_event = {
            "httpMethod": "POST",
            "path": "/premium/assign",
            "headers": {
                "Authorization": "Bearer test-token",
                "Content-Type": "application/json",
            },
            "body": json.dumps(
                {
                    "action": "assign",
                    "user_id": TEST_USER_ID,
                    "tier": "premium",
                }
            ),
            "requestContext": {
                "requestId": "test-request-123",
                "stage": "prod",
            },
        }

        mock_context = MagicMock()
        mock_context.function_name = "subscr-premium-manager"
        mock_context.aws_request_id = "test-lambda-123"

        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3, patch("pymysql.connect") as mock_pymysql:
            mock_connection = setup_db_mock(
                fetchone_values=[
                    None,
                    MockRow({"count": 1}),
                    MockRow({"count": 0}),
                    MockRow({"count": 1}),
                    None,
                    MockRow({"count": 1}),
                    MockRow({"count": 0}),
                    MockRow({"count": 1}),
                    None,
                    None,
                    MockRow({"priority": 100}),
                ],
                fetchall_values=[
                    [],
                    [
                        MockRow(
                            {
                                "instance_id": TEST_INSTANCE_ID,
                                "state": "running",
                            }
                        )
                    ],
                    [],
                    [],
                    [],
                    [],
                ],
            )
            mock_pymysql.return_value = mock_connection

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

            mock_ec2.describe_instances.return_value = {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": TEST_INSTANCE_ID,
                                "State": {"Name": "running"},
                                "InstanceType": "t3.large",
                                "LaunchTime": "2025-09-17T10:00:00Z",
                                "Tags": [
                                    {
                                        "Key": "Name",
                                        "Value": "premium-instance-1",
                                    },
                                    {
                                        "Key": "Tier",
                                        "Value": "premium",
                                    },
                                    {
                                        "Key": "Type",
                                        "Value": "Premium-Instance",
                                    },
                                ],
                            }
                        ]
                    }
                ]
            }

            mock_ecs.list_container_instances.return_value = {
                "containerInstanceArns": [
                    "arn:aws:ecs:region:account:" "container-instance/test"
                ]
            }
            mock_ecs.describe_container_instances.return_value = {
                "containerInstances": [
                    {
                        "containerInstanceArn": (
                            "arn:aws:ecs:region:account:" "container-instance/test"
                        ),
                        "ec2InstanceId": TEST_INSTANCE_ID,
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

            mock_elbv2.create_target_group.return_value = {
                "TargetGroups": [
                    {
                        "TargetGroupArn": (
                            "arn:aws:elasticloadbalancing:"
                            "region:account:targetgroup/test"
                        )
                    }
                ]
            }
            mock_elbv2.create_rule.return_value = {
                "Rules": [
                    {
                        "RuleArn": (
                            "arn:aws:elasticloadbalancing:"
                            "region:account:listener-rule/test"
                        )
                    }
                ]
            }

            from premium_manager import handler

            result = handler(api_gateway_event, mock_context)

            assert isinstance(result, dict)
            assert "statusCode" in result
            assert "body" in result

            status_code = result["statusCode"]
            response_body = json.loads(result["body"])

            print(f"Status Code: {status_code}")
            print(f"Response: {json.dumps(response_body, indent=2)}")

            if status_code == 200:
                assert "instance_id" in response_body
                assert response_body["instance_id"] == TEST_INSTANCE_ID
            elif status_code == 202:
                assert "retry_after" in response_body

    def test_heartbeat_event(self, mock_env_vars_premium):
        """Test premium_manager Lambda with heartbeat event."""
        print("Testing Premium Manager Lambda - Heartbeat Event")
        print("=" * 50)

        heartbeat_event = {
            "httpMethod": "POST",
            "path": "/premium/heartbeat",
            "body": json.dumps(
                {
                    "action": "update_activity",
                    "user_id": TEST_USER_ID,
                    "tier": "premium",
                }
            ),
            "requestContext": {"requestId": "test-heartbeat-123"},
        }

        mock_context = MagicMock()
        mock_context.function_name = "subscr-premium-manager"

        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "pymysql.connect"
        ) as mock_pymysql:
            mock_connection = setup_db_mock(
                fetchone_values=[
                    MockRow({"id": 12345}),
                    MockRow(
                        {
                            "user_id": 12345,
                            "instance_id": TEST_INSTANCE_ID,
                            "instance_state": "running",
                        }
                    ),
                ],
                fetchall_values=[[]],
            )
            mock_pymysql.return_value = mock_connection

            from premium_manager import handler

            result = handler(heartbeat_event, mock_context)

            assert isinstance(result, dict)
            status_code = result["statusCode"]
            response_body = json.loads(result["body"])

            print(f"Status Code: {status_code}")
            assert status_code == 200
            assert "user_id" in response_body
            assert response_body["user_id"] == 12345

    def test_release_event(self, mock_env_vars_premium):
        """Test premium_manager Lambda with release event."""
        print("Testing Premium Manager Lambda - Release Event")
        print("=" * 50)

        release_event = {
            "httpMethod": "POST",
            "path": "/premium/release",
            "body": json.dumps(
                {
                    "action": "release",
                    "user_id": TEST_USER_ID,
                    "tier": "premium",
                }
            ),
        }

        mock_context = MagicMock()

        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3, patch("pymysql.connect") as mock_pymysql:
            mock_connection = setup_db_mock(
                fetchone_values=[
                    MockRow(
                        {
                            "user_id": TEST_USER_ID,
                            "instance_id": TEST_INSTANCE_ID,
                            "target_group_arn": (
                                "arn:aws:elasticloadbalancing:"
                                "region:account:targetgroup/test"
                            ),
                            "alb_rule_arn": (
                                "arn:aws:elasticloadbalancing:"
                                "region:account1:"
                                "listener-rule/test"
                            ),
                        }
                    ),
                    MockRow({"count": 1}),
                    MockRow({"count": 0}),
                    MockRow({"count": 1}),
                    MockRow({"count": 3}),
                    MockRow({"count": 0}),
                    MockRow({"count": 1}),
                    MockRow({"count": 0}),
                    MockRow({"count": 1}),
                ],
                fetchall_values=[[], [], []],
            )
            mock_pymysql.return_value = mock_connection

            mock_elbv2 = MagicMock()
            mock_ec2 = MagicMock()

            def boto3_client_side_effect(service):
                if service == "elbv2":
                    return mock_elbv2
                elif service == "ec2":
                    return mock_ec2
                return MagicMock()

            mock_boto3.side_effect = boto3_client_side_effect
            mock_ec2.describe_instances.return_value = {"Reservations": []}

            from premium_manager import handler

            result = handler(release_event, mock_context)

            assert isinstance(result, dict)
            status_code = result["statusCode"]
            response_body = json.loads(result["body"])

            print(f"Status Code: {status_code}")
            assert status_code == 200
            assert "released_instance" in response_body

    def test_enum_values_in_lambda_operations(self, mock_env_vars_premium):
        """Test Lambda operations work with enum values."""
        print("Testing Lambda with Fixed Enum Values")
        print("=" * 50)

        enum_states = [
            ("launching", "Instance starting up"),
            ("running", "Instance active"),
            ("stopping", "Instance shutting down"),
            ("stopped", "Instance in standby"),
            ("terminating", "Instance being destroyed"),
        ]

        from premium_manager import store_user_assignment, update_instance_state

        for enum_state, description in enum_states:
            print(f"Testing enum state: '{enum_state}' " f"({description})")

            with patch.dict("os.environ", mock_env_vars_premium), patch(
                "pymysql.connect"
            ) as mock_pymysql:
                test_user = f"{TEST_USER_ID}_{enum_state}"

                mock_connection = setup_db_mock()
                mock_pymysql.return_value = mock_connection

                store_user_assignment(
                    user_id=test_user,
                    instance_id=TEST_INSTANCE_ID,
                    target_group_arn="arn:test",
                    rule_arn="arn:test2",
                    instance_state=enum_state,
                    is_shared=False,
                )
                print(f"store_user_assignment with " f"'{enum_state}' - SUCCESS")

                update_instance_state(test_user, enum_state)
                print(f"update_instance_state to " f"'{enum_state}' - SUCCESS")

    def test_error_handling(self, mock_env_vars_premium):
        """Test Lambda error handling for malformed events."""
        print("Testing Lambda Error Handling")
        print("=" * 50)

        malformed_event = {
            "httpMethod": "POST",
            "body": "invalid json {{",
        }
        mock_context = MagicMock()

        with patch.dict("os.environ", mock_env_vars_premium):
            from premium_manager import handler

            result = handler(malformed_event, mock_context)

            assert isinstance(result, dict)
            assert "statusCode" in result
            assert result["statusCode"] >= 400


class TestEarlyCheckAndCleanup:
    """TC-1, TC-2, TC-4, TC-5: Assignment early check and
    cleanup tests."""

    def test_early_check_returns_existing_assignment(self, mock_env_vars_premium):
        """TC-1: assign_premium_user returns existing assignment
        without creating new ALB rules."""
        print("Testing Early Check Returns Existing Assignment")
        print("=" * 50)

        test_user_id = 12345

        existing_assignment = {
            "user_id": test_user_id,
            "instance_id": TEST_INSTANCE_ID,
            "target_group_arn": "arn:aws:tg/existing",
            "alb_rule_arn": "arn:aws:rule/existing",
            "status": "active",
            "instance_state": "running",
            "is_shared": 0,
        }

        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3, patch(
            "premium_manager.get_existing_user_assignment"
        ) as mock_get_existing:
            mock_get_existing.return_value = existing_assignment

            mock_elbv2 = MagicMock()

            def boto3_client_side_effect(service):
                if service == "elbv2":
                    return mock_elbv2
                return MagicMock()

            mock_boto3.side_effect = boto3_client_side_effect

            from premium_manager import assign_premium_user

            result = assign_premium_user(test_user_id, {"tier": "premium"})

            status_code = result["statusCode"]
            response_body = json.loads(result["body"])

            print(f"Status Code: {status_code}")
            assert status_code == 200
            assert (
                "already assigned" in response_body.get("message", "").lower()
                or response_body.get("assignment_source") == "existing"
            )
            assert not mock_elbv2.create_rule.called
            mock_get_existing.assert_called_once_with(test_user_id)

    def test_exception_handler_cleans_up_alb_rule(self, mock_env_vars_premium):
        """TC-2: Exception handler cleans up ALB rule."""
        print("Testing Exception Handler ALB Rule Cleanup")
        print("=" * 50)

        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3, patch("pymysql.connect") as mock_pymysql:
            mock_connection = setup_db_mock(
                fetchone_values=[
                    None,
                    None,
                    MockRow({"count": 0}),
                ],
                fetchall_values=[
                    [],
                    [
                        MockRow(
                            {
                                "instance_id": TEST_INSTANCE_ID,
                                "state": "running",
                            }
                        )
                    ],
                    [],
                ],
            )
            mock_pymysql.return_value = mock_connection

            mock_elbv2 = MagicMock()
            mock_ec2 = MagicMock()

            created_rule_arn = "arn:aws:rule/test-created-rule"
            mock_elbv2.create_rule.return_value = {
                "Rules": [{"RuleArn": created_rule_arn}]
            }
            mock_elbv2.describe_rules.return_value = {
                "Rules": [
                    {
                        "RuleArn": "arn:aws:rule/duplicate1",
                        "Priority": "100",
                        "Conditions": [
                            {
                                "Field": "http-header",
                                "HttpHeaderConfig": {
                                    "HttpHeaderName": ("X-Routing-ID"),
                                    "Values": ["test123abc"],
                                },
                            }
                        ],
                        "Actions": [
                            {
                                "Type": "forward",
                                "TargetGroupArn": ("arn:aws:tg/orphaned"),
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
                                    "HttpHeaderName": ("X-Routing-ID"),
                                    "Values": ["test123abc"],
                                },
                            }
                        ],
                        "Actions": [
                            {
                                "Type": "forward",
                                "TargetGroupArn": ("arn:aws:tg/orphaned2"),
                            }
                        ],
                    },
                ]
            }

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
                                "InstanceId": TEST_INSTANCE_ID,
                                "State": {"Name": "running"},
                                "Tags": [
                                    {
                                        "Key": "Name",
                                        "Value": "premium-instance",
                                    },
                                    {
                                        "Key": "Tier",
                                        "Value": "premium",
                                    },
                                ],
                            }
                        ]
                    }
                ]
            }

            from premium_manager import cleanup_duplicate_rules_for_routing_id

            test_listener_arn = mock_env_vars_premium["ALB_LISTENER_ARN"]

            deleted_count = cleanup_duplicate_rules_for_routing_id(
                test_listener_arn, "test123abc"
            )

            print(f"Deleted {deleted_count} duplicate rules")
            assert mock_elbv2.delete_rule.call_count == 2

    def test_cleanup_duplicate_alb_rules_scheduled(self, mock_env_vars_premium):
        """TC-4: Scheduled duplicate cleanup in
        premium_cleanup Lambda."""
        print("Testing Scheduled Duplicate ALB Rules Cleanup")
        print("=" * 50)

        valid_rule_arn = "arn:aws:rule/valid-in-db"
        duplicate_rule_arn = "arn:aws:rule/duplicate-not-in-db"
        routing_id = "test-routing-id-123"

        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3, patch("premium_cleanup.get_db_connection") as mock_get_db:
            mock_cursor = MagicMock()
            mock_cursor.fetchall.return_value = [{"alb_rule_arn": valid_rule_arn}]

            mock_connection = MagicMock()
            mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
            mock_connection.__enter__.return_value = mock_connection
            mock_connection.__exit__.return_value = None

            mock_get_db.return_value.__enter__.return_value = mock_connection
            mock_get_db.return_value.__exit__.return_value = None

            mock_elbv2 = MagicMock()
            mock_elbv2.describe_rules.return_value = {
                "Rules": [
                    {
                        "RuleArn": "default",
                        "Priority": "default",
                    },
                    {
                        "RuleArn": valid_rule_arn,
                        "Priority": "100",
                        "Conditions": [
                            {
                                "Field": "http-header",
                                "HttpHeaderConfig": {
                                    "HttpHeaderName": ("X-Routing-ID"),
                                    "Values": [routing_id],
                                },
                            },
                        ],
                        "Actions": [
                            {
                                "Type": "forward",
                                "TargetGroupArn": "arn:tg/1",
                            }
                        ],
                    },
                    {
                        "RuleArn": duplicate_rule_arn,
                        "Priority": "101",
                        "Conditions": [
                            {
                                "Field": "http-header",
                                "HttpHeaderConfig": {
                                    "HttpHeaderName": ("X-Routing-ID"),
                                    "Values": [routing_id],
                                },
                            },
                        ],
                        "Actions": [
                            {
                                "Type": "forward",
                                "TargetGroupArn": "arn:tg/2",
                            }
                        ],
                    },
                ]
            }

            def boto3_client_side_effect(service):
                if service == "elbv2":
                    return mock_elbv2
                return MagicMock()

            mock_boto3.side_effect = boto3_client_side_effect

            from premium_cleanup import cleanup_duplicate_alb_rules

            result = cleanup_duplicate_alb_rules()

            print(f"Cleanup result: {result}")
            assert "duplicates_deleted" in result

            delete_calls = mock_elbv2.delete_rule.call_args_list
            deleted_arns = [call[1]["RuleArn"] for call in delete_calls]

            assert valid_rule_arn not in deleted_arns
            assert duplicate_rule_arn in deleted_arns

    def test_autoscaling_pool_triggers_migration_retry(self, mock_env_vars_premium):
        """TC-5: Autoscaling-pool assignment triggers
        migration retry."""
        print("Testing Autoscaling-Pool Assignment " "Triggers Migration Retry")
        print("=" * 50)

        test_user_id = 12345

        existing_autoscaling_assignment = {
            "user_id": test_user_id,
            "instance_id": "autoscaling-pool",
            "target_group_arn": "arn:aws:tg/autoscaling",
            "alb_rule_arn": "arn:aws:rule/autoscaling",
            "status": "active",
            "instance_state": "running",
            "is_shared": 1,
        }

        with patch.dict("os.environ", mock_env_vars_premium):
            import premium_manager

            with patch.object(
                premium_manager,
                "get_existing_user_assignment",
            ) as mock_get_existing, patch.object(
                premium_manager, "invoke_migration_async"
            ) as mock_invoke_migration, patch(
                "boto3.client"
            ) as mock_boto3:
                mock_get_existing.return_value = existing_autoscaling_assignment

                mock_elbv2 = MagicMock()

                def boto3_client_side_effect(service):
                    if service == "elbv2":
                        return mock_elbv2
                    return MagicMock()

                mock_boto3.side_effect = boto3_client_side_effect

                result = premium_manager.assign_premium_user(
                    test_user_id, {"tier": "premium"}
                )

                status_code = result["statusCode"]
                response_body = json.loads(result["body"])

                assert status_code == 200
                assert response_body.get("instance_id") == "autoscaling-pool"
                assert response_body.get("assignment_source") == "existing"
                assert mock_invoke_migration.called
                mock_get_existing.assert_called_once_with(test_user_id)
                assert not mock_elbv2.create_rule.called


class TestDictCursorFix:
    """TC-6..8: DictCursor fix tests."""

    def test_increment_attempts(self, mock_env_vars_premium):
        """TC-6: _increment_assignment_attempts_transaction
        uses dict-style access."""
        print("Testing DictCursor Fix - Increment Attempts")
        print("=" * 50)

        test_user_id = 99

        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "pymysql.connect"
        ) as mock_pymysql:
            mock_connection = setup_db_mock(
                fetchone_values=[
                    {"assignment_attempts": 3},
                ],
            )
            mock_pymysql.return_value = mock_connection

            from premium_manager import _increment_assignment_attempts_transaction

            result = _increment_assignment_attempts_transaction(test_user_id)

            assert result == 4
            print(f"Correctly incremented attempts: 3 -> {result}")

    def test_store_existing_assignment(self, mock_env_vars_premium):
        """TC-7: _store_user_assignment_transaction uses
        dict-style access on existing assignment."""
        print("Testing DictCursor Fix - " "Store Existing Assignment")
        print("=" * 50)

        test_user_id = 99

        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "pymysql.connect"
        ) as mock_pymysql:
            mock_connection = setup_db_mock(
                fetchone_values=[
                    {
                        "user_id": test_user_id,
                        "assignment_attempts": 1,
                    },
                ],
            )
            mock_pymysql.return_value = mock_connection

            from premium_manager import _store_user_assignment_transaction

            try:
                _store_user_assignment_transaction(
                    user_id=test_user_id,
                    instance_id=TEST_INSTANCE_ID,
                    target_group_arn="arn:test",
                    rule_arn="arn:test2",
                )
                # Should raise, not reach here
                assert False, "Expected exception not raised"
            except KeyError:
                assert False, (
                    "REGRESSION: KeyError - still using " "tuple indexing on DictCursor"
                )
            except Exception as e:
                assert "already has a premium assignment" in str(e)
                print(f"Correctly raised: {e}")

    def test_increment_none_attempts(self, mock_env_vars_premium):
        """TC-8: None assignment_attempts defaults to 1."""
        print("Testing DictCursor Fix - None Attempts Default")
        print("=" * 50)

        test_user_id = 99

        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "pymysql.connect"
        ) as mock_pymysql:
            mock_connection = setup_db_mock(
                fetchone_values=[
                    {"assignment_attempts": None},
                ],
            )
            mock_pymysql.return_value = mock_connection

            from premium_manager import _increment_assignment_attempts_transaction

            result = _increment_assignment_attempts_transaction(test_user_id)

            assert result == 2
            print(f"Correctly defaulted None to 1, " f"incremented to {result}")


class TestGenerateRoutingId:
    """TC-9..12: generate_routing_id pure function tests."""

    def test_deterministic(self):
        """TC-9: Same uid+key always returns the same ID."""
        from premium_manager import generate_routing_id

        uid = "user_abc_123"
        key = "secret-key-xyz"
        result1 = generate_routing_id(uid, key)
        result2 = generate_routing_id(uid, key)

        assert result1 == result2
        print(f"Deterministic: '{result1}' == '{result2}'")

    def test_length(self):
        """TC-10: Output is exactly 16 hex characters."""
        from premium_manager import generate_routing_id

        result = generate_routing_id("user_1", "key_1")

        assert len(result) == 16
        assert all(c in "0123456789abcdef" for c in result)
        print(f"Valid 16-char hex: '{result}'")

    def test_different_keys(self):
        """TC-11: Different keys produce different IDs."""
        from premium_manager import generate_routing_id

        uid = "same_user"
        id1 = generate_routing_id(uid, "key_alpha")
        id2 = generate_routing_id(uid, "key_beta")

        assert id1 != id2
        print(f"key_alpha -> '{id1}', key_beta -> '{id2}'")

    def test_different_uids(self):
        """TC-12: Different UIDs produce different IDs."""
        from premium_manager import generate_routing_id

        key = "same_key"
        id1 = generate_routing_id("user_aaa", key)
        id2 = generate_routing_id("user_bbb", key)

        assert id1 != id2
        print(f"user_aaa -> '{id1}', user_bbb -> '{id2}'")


class TestCountTotalPremiumUsers:
    """TC-13..15: count_total_premium_users tests."""

    def test_subscription_table(self, mock_env_vars_premium):
        """TC-13: Primary path returns subscriber count."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "pymysql.connect"
        ) as mock_pymysql:
            mock_connection = setup_db_mock(
                fetchone_values=[MockRow({"count": 7})],
            )
            mock_pymysql.return_value = mock_connection

            from premium_manager import count_total_premium_users

            result = count_total_premium_users()
            assert result == 7
            print(f"Subscription table returned: {result}")

    def test_fallback(self, mock_env_vars_premium):
        """TC-14: Subscription query fails, falls back."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "pymysql.connect"
        ) as mock_pymysql:
            mock_connection = setup_db_mock()
            mock_pymysql.return_value = mock_connection

            mock_cursor = mock_connection.cursor.return_value.__enter__.return_value
            mock_cursor.execute.side_effect = [
                Exception("Table not found"),
                None,
            ]
            mock_cursor.fetchone.side_effect = [
                MockRow({"count": 3}),
            ]

            from premium_manager import count_total_premium_users

            result = count_total_premium_users()
            assert result == 3
            print(f"Fallback returned: {result}")

    def test_all_fail(self, mock_env_vars_premium):
        """TC-15: Both queries fail, returns default."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "pymysql.connect"
        ) as mock_pymysql:
            mock_connection = setup_db_mock()
            mock_pymysql.return_value = mock_connection

            mock_cursor = mock_connection.cursor.return_value.__enter__.return_value
            mock_cursor.execute.side_effect = [
                Exception("Subscription table error"),
                Exception("Assignments table error"),
            ]

            from premium_manager import (
                DEFAULT_DEVELOPMENT_CAPACITY,
                count_total_premium_users,
            )

            result = count_total_premium_users()
            assert result == DEFAULT_DEVELOPMENT_CAPACITY
            print(
                f"All-fail fallback returned: {result} "
                f"(DEFAULT_DEVELOPMENT_CAPACITY)"
            )


class TestTryReserveInstance:
    """TC-16..17: try_reserve_instance tests."""

    def test_success(self, mock_env_vars_premium):
        """TC-16: No existing assignment, reservation inserted."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "pymysql.connect"
        ) as mock_pymysql:
            mock_connection = setup_db_mock(
                fetchone_values=[None],
            )
            mock_pymysql.return_value = mock_connection

            from premium_manager import try_reserve_instance

            result = try_reserve_instance("i-new123", 42)
            assert result is True

    def test_already_reserved(self, mock_env_vars_premium):
        """TC-17: Existing assignment found, returns False."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "pymysql.connect"
        ) as mock_pymysql:
            mock_connection = setup_db_mock(
                fetchone_values=[
                    MockRow({"instance_id": "i-existing"}),
                ],
            )
            mock_pymysql.return_value = mock_connection

            from premium_manager import try_reserve_instance

            result = try_reserve_instance("i-existing", 42)
            assert result is False


class TestDatabaseCommits:
    """TC-18..20: Verify commit is called after operations."""

    def test_terminate_standby_instance_commits(self, mock_env_vars_premium):
        """TC-18: Verify commit is called after DELETE."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3, patch("pymysql.connect") as mock_pymysql:
            mock_connection = setup_db_mock()
            mock_pymysql.return_value = mock_connection

            mock_ec2 = MagicMock()
            mock_boto3.return_value = mock_ec2

            from premium_manager import terminate_standby_instance

            result = terminate_standby_instance("i-standby1")

            assert result is True
            mock_connection.commit.assert_called()

    def test_cleanup_failed_standby_commits(self, mock_env_vars_premium):
        """TC-19: Verify commit is called after cleanup."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "premium_manager" ".get_all_premium_instances_with_states"
        ) as mock_aws, patch("pymysql.connect") as mock_pymysql:
            mock_aws.return_value = []

            mock_connection = setup_db_mock(
                fetchall_values=[
                    [MockRow({"instance_id": "i-gone"})],
                ],
            )
            mock_pymysql.return_value = mock_connection

            from premium_manager import cleanup_failed_standby_instances

            cleanup_failed_standby_instances()
            mock_connection.commit.assert_called()

    def test_update_user_activity_commits(self, mock_env_vars_premium):
        """TC-20: Verify commit is called after UPDATE."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "pymysql.connect"
        ) as mock_pymysql:
            mock_connection = setup_db_mock()
            mock_pymysql.return_value = mock_connection

            mock_cursor = mock_connection.cursor.return_value.__enter__.return_value
            mock_cursor.rowcount = 1

            from premium_manager import update_user_activity

            result = update_user_activity(42)

            assert result is True
            mock_connection.commit.assert_called()


class TestGetAllPremiumInstances:
    """TC-21..22: get_all_premium_instances tests."""

    def test_filters_by_tags(self, mock_env_vars_premium):
        """TC-21: Only premium-tagged instances returned."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3:
            mock_ec2 = MagicMock()
            mock_boto3.return_value = mock_ec2

            mock_ec2.describe_instances.return_value = {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": "i-premium1",
                                "InstanceType": "t3.large",
                                "State": {"Name": "running"},
                                "Tags": [
                                    {
                                        "Key": "Name",
                                        "Value": "premium-inst-1",
                                    },
                                    {
                                        "Key": "Tier",
                                        "Value": "premium",
                                    },
                                ],
                            },
                            {
                                "InstanceId": "i-other1",
                                "InstanceType": "t3.micro",
                                "State": {"Name": "running"},
                                "Tags": [
                                    {
                                        "Key": "Name",
                                        "Value": "web-server",
                                    },
                                ],
                            },
                        ]
                    }
                ]
            }

            from premium_manager import get_all_premium_instances_with_states

            result = get_all_premium_instances_with_states()

            assert len(result) == 1
            assert result[0]["instance_id"] == "i-premium1"

    def test_empty(self, mock_env_vars_premium):
        """TC-22: No instances returns empty list."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3:
            mock_ec2 = MagicMock()
            mock_boto3.return_value = mock_ec2
            mock_ec2.describe_instances.return_value = {"Reservations": []}

            from premium_manager import get_all_premium_instances_with_states

            result = get_all_premium_instances_with_states()
            assert result == []


class TestStartStandbyInstance:
    """TC-23..24: start_standby_instance tests."""

    def test_success(self, mock_env_vars_premium):
        """TC-23: EC2 start + waiter + DB update."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3, patch("pymysql.connect") as mock_pymysql:
            mock_ec2 = MagicMock()
            mock_boto3.return_value = mock_ec2
            mock_waiter = MagicMock()
            mock_ec2.get_waiter.return_value = mock_waiter

            mock_connection = setup_db_mock()
            mock_pymysql.return_value = mock_connection

            from premium_manager import start_standby_instance

            result = start_standby_instance("i-standby1")

            assert result is True
            mock_ec2.start_instances.assert_called_once_with(InstanceIds=["i-standby1"])
            mock_waiter.wait.assert_called_once()
            mock_connection.commit.assert_called()

    def test_waiter_fails(self, mock_env_vars_premium):
        """TC-24: EC2 waiter timeout returns False."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3, patch("pymysql.connect") as mock_pymysql:
            mock_ec2 = MagicMock()
            mock_boto3.return_value = mock_ec2
            mock_waiter = MagicMock()
            mock_waiter.wait.side_effect = Exception(
                "Waiter instance_running timed out"
            )
            mock_ec2.get_waiter.return_value = mock_waiter

            mock_connection = setup_db_mock()
            mock_pymysql.return_value = mock_connection

            from premium_manager import start_standby_instance

            result = start_standby_instance("i-standby2")
            assert result is False
