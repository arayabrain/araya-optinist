"""Tests for premium_manager Lambda function."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from aws_constants import ECSTaskStatus, PremiumInstanceConfig
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
        ) as mock_boto3, patch("premium_manager.pymysql.connect") as mock_pymysql:
            mock_connection = setup_db_mock(
                fetchone_values=[
                    MockRow({"id": 123}),
                    # restore_pending_release: no pending_release row
                    None,
                    # get_existing_user_assignment: no existing assignment
                    None,
                    # _count_active_premium_users_transaction: debug count
                    MockRow({"count": 1}),
                    # _count_active_premium_users_transaction: real count
                    MockRow({"count": 0}),
                    # try_reserve_instance: no existing reservation
                    None,
                    # store_user_assignment: no existing user assignment
                    None,
                    # store_user_assignment: free_user_assignments check
                    None,
                ],
                fetchall_values=[
                    # register_orphaned: get_available_standby (1st call)
                    [],
                    # get_available_standby (2nd call in assign flow)
                    [
                        MockRow(
                            {
                                "instance_id": TEST_INSTANCE_ID,
                                "state": "running",
                            }
                        )
                    ],
                    # get_assigned_users_for_instance: debug query
                    [],
                    # get_assigned_users_for_instance: real users
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
                                        "Value": (
                                            PremiumInstanceConfig.get_instance_name()
                                        ),
                                    },
                                    {
                                        "Key": "Tier",
                                        "Value": (
                                            PremiumInstanceConfig.INSTANCE_IDENTIFIER
                                        ),
                                    },
                                    {
                                        "Key": "Type",
                                        "Value": (
                                            PremiumInstanceConfig.INSTANCE_TYPE_TAG
                                        ),
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
        ) as mock_boto3, patch("premium_manager.pymysql.connect") as mock_pymysql:
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

        for i, (enum_state, description) in enumerate(enum_states):
            print(f"Testing enum state: '{enum_state}' " f"({description})")

            with patch.dict("os.environ", mock_env_vars_premium), patch(
                "pymysql.connect"
            ) as mock_pymysql:
                test_user_id = 1000 + i

                mock_connection = setup_db_mock()
                mock_pymysql.return_value = mock_connection

                store_user_assignment(
                    user_id=test_user_id,
                    instance_id=TEST_INSTANCE_ID,
                    target_group_arn="arn:test",
                    rule_arn="arn:test2",
                    instance_state=enum_state,
                    is_shared=False,
                )
                print(f"store_user_assignment with " f"'{enum_state}' - SUCCESS")

                update_instance_state(test_user_id, enum_state)
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
    """Assignment early check and cleanup tests."""

    def test_early_check_returns_existing_assignment(self, mock_env_vars_premium):
        """assign_premium_user returns existing assignment
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
        ) as mock_get_existing, patch(
            "premium_manager.pymysql.connect"
        ) as mock_pymysql:
            mock_get_existing.return_value = existing_assignment
            mock_connection = setup_db_mock(fetchone_values=[None])
            mock_pymysql.return_value = mock_connection

            mock_elbv2 = MagicMock()

            def boto3_client_side_effect(service):
                if service == "elbv2":
                    return mock_elbv2
                return MagicMock()

            mock_boto3.side_effect = boto3_client_side_effect

            from premium_manager import assign_premium_user

            result = assign_premium_user(
                test_user_id, {"tier": "premium"}, "firebase_uid_123"
            )

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
        """Exception handler cleans up ALB rule."""
        print("Testing Exception Handler ALB Rule Cleanup")
        print("=" * 50)

        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3, patch("premium_manager.pymysql.connect") as mock_pymysql:
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
                                        "Value": (
                                            PremiumInstanceConfig.get_instance_name()
                                        ),
                                    },
                                    {
                                        "Key": "Tier",
                                        "Value": (
                                            PremiumInstanceConfig.INSTANCE_IDENTIFIER
                                        ),
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
        """Scheduled duplicate cleanup in
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
        """Autoscaling-pool assignment triggers
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
            ) as mock_boto3, patch(
                "premium_manager.pymysql.connect"
            ) as mock_pymysql:
                mock_get_existing.return_value = existing_autoscaling_assignment
                mock_connection = setup_db_mock(fetchone_values=[None])
                mock_pymysql.return_value = mock_connection

                mock_elbv2 = MagicMock()

                def boto3_client_side_effect(service):
                    if service == "elbv2":
                        return mock_elbv2
                    return MagicMock()

                mock_boto3.side_effect = boto3_client_side_effect

                result = premium_manager.assign_premium_user(
                    test_user_id,
                    {"tier": "premium"},
                    "firebase_uid_456",
                )

                status_code = result["statusCode"]
                response_body = json.loads(result["body"])

                assert status_code == 200
                assert response_body.get("instance_id") == "autoscaling-pool"
                assert response_body.get("assignment_source") == "existing"
                assert mock_invoke_migration.called
                mock_get_existing.assert_called_with(test_user_id)
                assert not mock_elbv2.create_rule.called

    def test_restore_pending_release_returns_alb_fields(self, mock_env_vars_premium):
        """Restored pending_release response includes
        target_group_arn and rule_arn."""
        test_user_id = 12345

        restored_assignment = {
            "user_id": test_user_id,
            "instance_id": TEST_INSTANCE_ID,
            "target_group_arn": "arn:aws:tg/restored",
            "alb_rule_arn": "arn:aws:rule/restored",
            "status": "terminating",
            "instance_state": "running",
            "is_shared": 0,
        }

        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ), patch("premium_manager.restore_pending_release") as mock_restore:
            mock_restore.return_value = restored_assignment

            from premium_manager import assign_premium_user

            result = assign_premium_user(
                test_user_id, {"tier": "premium"}, "firebase_uid_123"
            )

            status_code = result["statusCode"]
            response_body = json.loads(result["body"])

            assert status_code == 200
            assert response_body["assignment_source"] == "restored_from_pending_release"
            assert response_body["target_group_arn"] == "arn:aws:tg/restored"
            assert response_body["rule_arn"] == "arn:aws:rule/restored"
            assert response_body["instance_id"] == TEST_INSTANCE_ID
            mock_restore.assert_called_once_with(test_user_id)

    def test_stopped_instance_restarted_on_assign(self, mock_env_vars_premium):
        """Assign restarts a stopped dedicated instance
        and returns it once ECS is ready."""
        test_user_id = 12345

        existing_assignment = {
            "user_id": test_user_id,
            "instance_id": TEST_INSTANCE_ID,
            "target_group_arn": "arn:aws:tg/existing",
            "alb_rule_arn": "arn:aws:rule/existing",
            "status": "active",
            "instance_state": "stopped",
            "is_shared": 0,
        }

        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3, patch(
            "premium_manager.get_existing_user_assignment"
        ) as mock_get_existing, patch(
            "premium_manager.check_instance_readiness_with_retry"
        ) as mock_readiness, patch(
            "premium_manager.clear_ecs_agent_checkpoint"
        ) as mock_clear_ecs, patch(
            "premium_manager._update_instance_state_to_running"
        ) as mock_update_state, patch(
            "premium_manager.pymysql.connect"
        ) as mock_pymysql:
            mock_get_existing.return_value = existing_assignment
            mock_readiness.return_value = True
            mock_connection = setup_db_mock(fetchone_values=[None])
            mock_pymysql.return_value = mock_connection

            mock_ec2 = MagicMock()
            mock_ec2.describe_instances.return_value = {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": TEST_INSTANCE_ID,
                                "State": {"Name": "stopped"},
                            }
                        ]
                    }
                ]
            }
            mock_ec2.get_waiter.return_value = MagicMock()

            def boto3_client_side_effect(service):
                if service == "ec2":
                    return mock_ec2
                return MagicMock()

            mock_boto3.side_effect = boto3_client_side_effect

            from premium_manager import assign_premium_user

            result = assign_premium_user(
                test_user_id, {"tier": "premium"}, "firebase_uid_123"
            )

            status_code = result["statusCode"]
            response_body = json.loads(result["body"])

            assert status_code == 200
            assert response_body["instance_id"] == TEST_INSTANCE_ID
            assert response_body["assignment_source"] == "restarted_instance"
            mock_ec2.start_instances.assert_called_once_with(
                InstanceIds=[TEST_INSTANCE_ID]
            )
            mock_clear_ecs.assert_called_once_with(TEST_INSTANCE_ID)
            mock_update_state.assert_called_once_with(TEST_INSTANCE_ID)
            mock_readiness.assert_called_once_with(
                TEST_INSTANCE_ID, max_wait_seconds=120, retry_interval=10
            )

    def test_stopping_instance_waits_then_restarts(self, mock_env_vars_premium):
        """Assign waits for a stopping instance to reach stopped
        state before restarting it."""
        test_user_id = 12345

        existing_assignment = {
            "user_id": test_user_id,
            "instance_id": TEST_INSTANCE_ID,
            "target_group_arn": "arn:aws:tg/existing",
            "alb_rule_arn": "arn:aws:rule/existing",
            "status": "active",
            "instance_state": "stopping",
            "is_shared": 0,
        }

        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3, patch(
            "premium_manager.get_existing_user_assignment"
        ) as mock_get_existing, patch(
            "premium_manager.check_instance_readiness_with_retry"
        ) as mock_readiness, patch(
            "premium_manager.clear_ecs_agent_checkpoint"
        ) as mock_clear_ecs, patch(
            "premium_manager._update_instance_state_to_running"
        ), patch(
            "premium_manager.pymysql.connect"
        ) as mock_pymysql:
            mock_get_existing.return_value = existing_assignment
            mock_readiness.return_value = True
            mock_connection = setup_db_mock(fetchone_values=[None])
            mock_pymysql.return_value = mock_connection

            mock_ec2 = MagicMock()
            mock_ec2.describe_instances.return_value = {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": TEST_INSTANCE_ID,
                                "State": {"Name": "stopping"},
                            }
                        ]
                    }
                ]
            }
            mock_stop_waiter = MagicMock()
            mock_run_waiter = MagicMock()

            def get_waiter_side_effect(waiter_name):
                if waiter_name == "instance_stopped":
                    return mock_stop_waiter
                if waiter_name == "instance_running":
                    return mock_run_waiter
                return MagicMock()

            mock_ec2.get_waiter.side_effect = get_waiter_side_effect

            def boto3_client_side_effect(service):
                if service == "ec2":
                    return mock_ec2
                return MagicMock()

            mock_boto3.side_effect = boto3_client_side_effect

            from premium_manager import assign_premium_user

            result = assign_premium_user(
                test_user_id, {"tier": "premium"}, "firebase_uid_123"
            )

            status_code = result["statusCode"]
            response_body = json.loads(result["body"])

            assert status_code == 200
            assert response_body["assignment_source"] == "restarted_instance"
            mock_stop_waiter.wait.assert_called_once()
            mock_ec2.start_instances.assert_called_once_with(
                InstanceIds=[TEST_INSTANCE_ID]
            )
            mock_clear_ecs.assert_called_once_with(TEST_INSTANCE_ID)

    def test_terminated_instance_triggers_fresh_assignment(self, mock_env_vars_premium):
        """Assign removes stale assignment for a terminated
        instance and does not return the stale record."""
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

        patches = {
            "premium_manager.register_orphaned_stopped_instances": None,
            "premium_manager.get_all_premium_instances_with_states": [],
            "premium_manager.count_active_premium_users": 0,
            "premium_manager.get_available_standby_instances": [],
        }
        with patch.dict("os.environ", mock_env_vars_premium):
            import premium_manager

            patchers = []
            for target, rv in patches.items():
                p = patch(target, return_value=rv)
                p.start()
                patchers.append(p)

            try:
                with patch.object(
                    premium_manager, "get_existing_user_assignment"
                ) as mock_get_existing, patch.object(
                    premium_manager, "remove_user_assignment"
                ) as mock_remove, patch(
                    "boto3.client"
                ) as mock_boto3, patch(
                    "pymysql.connect"
                ) as mock_pymysql:
                    mock_get_existing.return_value = existing_assignment
                    mock_pymysql.return_value = setup_db_mock(
                        fetchone_values=[None] * 20,
                        fetchall_values=[[] for _ in range(10)],
                    )

                    mock_ec2 = MagicMock()
                    mock_ec2.describe_instances.return_value = {
                        "Reservations": [
                            {
                                "Instances": [
                                    {
                                        "InstanceId": TEST_INSTANCE_ID,
                                        "State": {"Name": "terminated"},
                                    }
                                ]
                            }
                        ]
                    }

                    mock_elbv2 = MagicMock()
                    mock_elbv2.create_target_group.return_value = {
                        "TargetGroups": [{"TargetGroupArn": "arn:aws:tg/new"}]
                    }
                    mock_elbv2.create_rule.return_value = {
                        "Rules": [{"RuleArn": "arn:aws:rule/new"}]
                    }

                    def boto3_client_side_effect(service):
                        if service == "ec2":
                            return mock_ec2
                        if service == "elbv2":
                            return mock_elbv2
                        return MagicMock()

                    mock_boto3.side_effect = boto3_client_side_effect

                    result = premium_manager.assign_premium_user(
                        test_user_id, {"tier": "premium"}, "firebase_uid_123"
                    )

                    mock_remove.assert_called_once_with(test_user_id)
                    # Should NOT return the stale assignment
                    response_body = json.loads(result["body"])
                    assert response_body.get("assignment_source") != "existing"
            finally:
                for p in patchers:
                    p.stop()

    def test_ec2_not_found_cleans_up_stale_assignment(self, mock_env_vars_premium):
        """Assign removes stale assignment when EC2 instance
        no longer exists (InvalidInstanceID.NotFound)."""
        from botocore.exceptions import ClientError

        test_user_id = 12345

        existing_assignment = {
            "user_id": test_user_id,
            "instance_id": "i-deleted999",
            "target_group_arn": "arn:aws:tg/existing",
            "alb_rule_arn": "arn:aws:rule/existing",
            "status": "active",
            "instance_state": "running",
            "is_shared": 0,
        }

        patches = {
            "premium_manager.register_orphaned_stopped_instances": None,
            "premium_manager.get_all_premium_instances_with_states": [],
            "premium_manager.count_active_premium_users": 0,
            "premium_manager.get_available_standby_instances": [],
        }
        with patch.dict("os.environ", mock_env_vars_premium):
            import premium_manager

            patchers = []
            for target, rv in patches.items():
                p = patch(target, return_value=rv)
                p.start()
                patchers.append(p)

            try:
                with patch.object(
                    premium_manager, "get_existing_user_assignment"
                ) as mock_get_existing, patch.object(
                    premium_manager, "remove_user_assignment"
                ) as mock_remove, patch(
                    "boto3.client"
                ) as mock_boto3, patch(
                    "pymysql.connect"
                ) as mock_pymysql:
                    mock_get_existing.return_value = existing_assignment
                    mock_pymysql.return_value = setup_db_mock(
                        fetchone_values=[None] * 20,
                        fetchall_values=[[] for _ in range(10)],
                    )

                    mock_ec2 = MagicMock()
                    mock_ec2.describe_instances.side_effect = ClientError(
                        {
                            "Error": {
                                "Code": "InvalidInstanceID.NotFound",
                                "Message": "Instance not found",
                            }
                        },
                        "DescribeInstances",
                    )

                    mock_elbv2 = MagicMock()
                    mock_elbv2.create_target_group.return_value = {
                        "TargetGroups": [{"TargetGroupArn": "arn:aws:tg/new"}]
                    }
                    mock_elbv2.create_rule.return_value = {
                        "Rules": [{"RuleArn": "arn:aws:rule/new"}]
                    }

                    def boto3_client_side_effect(service):
                        if service == "ec2":
                            return mock_ec2
                        if service == "elbv2":
                            return mock_elbv2
                        return MagicMock()

                    mock_boto3.side_effect = boto3_client_side_effect

                    result = premium_manager.assign_premium_user(
                        test_user_id, {"tier": "premium"}, "firebase_uid_123"
                    )

                    mock_remove.assert_called_once_with(test_user_id)
                    response_body = json.loads(result["body"])
                    assert response_body.get("assignment_source") != "existing"
            finally:
                for p in patchers:
                    p.stop()


class TestDictCursorFix:
    """DictCursor fix tests."""

    def test_increment_attempts(self, mock_env_vars_premium):
        """_increment_assignment_attempts_transaction
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
        """_store_user_assignment_transaction uses
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
        """None assignment_attempts defaults to 1."""
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
    """generate_routing_id pure function tests."""

    def test_deterministic(self):
        """Same uid+key always returns the same ID."""
        from premium_manager import generate_routing_id

        uid = "user_abc_123"
        key = "secret-key-xyz"
        result1 = generate_routing_id(uid, key)
        result2 = generate_routing_id(uid, key)

        assert result1 == result2
        print(f"Deterministic: '{result1}' == '{result2}'")

    def test_length(self):
        """Output is exactly 16 hex characters."""
        from premium_manager import generate_routing_id

        result = generate_routing_id("user_1", "key_1")

        assert len(result) == 16
        assert all(c in "0123456789abcdef" for c in result)
        print(f"Valid 16-char hex: '{result}'")

    def test_different_keys(self):
        """Different keys produce different IDs."""
        from premium_manager import generate_routing_id

        uid = "same_user"
        id1 = generate_routing_id(uid, "key_alpha")
        id2 = generate_routing_id(uid, "key_beta")

        assert id1 != id2
        print(f"key_alpha -> '{id1}', key_beta -> '{id2}'")

    def test_different_uids(self):
        """Different UIDs produce different IDs."""
        from premium_manager import generate_routing_id

        key = "same_key"
        id1 = generate_routing_id("user_aaa", key)
        id2 = generate_routing_id("user_bbb", key)

        assert id1 != id2
        print(f"user_aaa -> '{id1}', user_bbb -> '{id2}'")


class TestCountTotalPremiumUsers:
    """count_total_premium_users tests."""

    def test_subscription_table(self, mock_env_vars_premium):
        """Primary path returns subscriber count."""
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
        """Subscription query fails, falls back."""
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
        """Both queries fail, returns default."""
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
    """try_reserve_instance tests."""

    def test_success(self, mock_env_vars_premium):
        """No existing assignment, reservation inserted."""
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
        """Existing assignment found, returns False."""
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
    """Verify commit is called after operations."""

    def test_terminate_standby_instance_commits(self, mock_env_vars_premium):
        """Verify commit is called after DELETE."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3, patch("premium_manager.pymysql.connect") as mock_pymysql:
            mock_connection = setup_db_mock()
            mock_pymysql.return_value = mock_connection

            mock_ec2 = MagicMock()
            mock_boto3.return_value = mock_ec2

            from premium_manager import terminate_standby_instance

            result = terminate_standby_instance("i-standby1")

            assert result is True
            mock_connection.commit.assert_called()

    def test_cleanup_failed_standby_commits(self, mock_env_vars_premium):
        """Verify commit is called after cleanup."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "premium_manager" ".get_all_premium_instances_with_states"
        ) as mock_aws, patch("premium_manager.pymysql.connect") as mock_pymysql:
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
        """Verify commit is called after UPDATE."""
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
    """get_all_premium_instances tests."""

    def test_filters_by_tags(self, mock_env_vars_premium):
        """Only premium-tagged instances returned."""
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
                                        "Value": (
                                            PremiumInstanceConfig.get_instance_name()
                                        ),
                                    },
                                    {
                                        "Key": "Tier",
                                        "Value": (
                                            PremiumInstanceConfig.INSTANCE_IDENTIFIER
                                        ),
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
        """No instances returns empty list."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3:
            mock_ec2 = MagicMock()
            mock_boto3.return_value = mock_ec2
            mock_ec2.describe_instances.return_value = {"Reservations": []}

            from premium_manager import get_all_premium_instances_with_states

            result = get_all_premium_instances_with_states()
            assert result == []

    def test_cross_env_instance_skipped(self, mock_env_vars_premium):
        """Instance with different env prefix is excluded."""
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
                                "InstanceId": "i-cross-env",
                                "InstanceType": "t3.large",
                                "State": {"Name": "running"},
                                "Tags": [
                                    {
                                        "Key": "Name",
                                        "Value": "production-premium-running",
                                    },
                                    {
                                        "Key": "Tier",
                                        "Value": (
                                            PremiumInstanceConfig.INSTANCE_IDENTIFIER
                                        ),
                                    },
                                ],
                            },
                        ]
                    }
                ]
            }

            from premium_manager import get_all_premium_instances_with_states

            result = get_all_premium_instances_with_states()
            assert len(result) == 0

    def test_tagless_instance_skipped(self, mock_env_vars_premium):
        """Premium instance without Name tag is excluded."""
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
                                "InstanceId": "i-no-name",
                                "InstanceType": "t3.large",
                                "State": {"Name": "running"},
                                "Tags": [
                                    {
                                        "Key": "Tier",
                                        "Value": (
                                            PremiumInstanceConfig.INSTANCE_IDENTIFIER
                                        ),
                                    },
                                ],
                            },
                        ]
                    }
                ]
            }

            from premium_manager import get_all_premium_instances_with_states

            result = get_all_premium_instances_with_states()
            assert len(result) == 0


class TestStartStandbyInstance:
    """start_standby_instance tests."""

    def test_success(self, mock_env_vars_premium):
        """EC2 start + waiter + checkpoint clear + DB update."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3, patch(
            "premium_manager.pymysql.connect"
        ) as mock_pymysql, patch(
            "premium_manager.clear_ecs_agent_checkpoint"
        ) as mock_clear:
            mock_ec2 = MagicMock()
            mock_boto3.return_value = mock_ec2
            mock_waiter = MagicMock()
            mock_ec2.get_waiter.return_value = mock_waiter
            mock_clear.return_value = True

            mock_connection = setup_db_mock()
            mock_pymysql.return_value = mock_connection

            from premium_manager import start_standby_instance

            result = start_standby_instance("i-standby1")

            assert result is True
            mock_ec2.start_instances.assert_called_once_with(InstanceIds=["i-standby1"])
            mock_waiter.wait.assert_called_once()
            mock_clear.assert_called_once_with("i-standby1")
            mock_connection.commit.assert_called()

    def test_waiter_fails(self, mock_env_vars_premium):
        """EC2 waiter timeout returns False."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3, patch("premium_manager.pymysql.connect") as mock_pymysql:
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


class TestClearEcsAgentCheckpoint:
    """Tests for clear_ecs_agent_checkpoint SSM helper."""

    def test_success(self, mock_env_vars_premium):
        """SSM command succeeds on first poll."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3, patch("premium_manager.time.sleep"):
            mock_ssm = MagicMock()
            mock_boto3.return_value = mock_ssm
            mock_ssm.describe_instance_information.return_value = {
                "InstanceInformationList": [{"PingStatus": "Online"}]
            }
            mock_ssm.send_command.return_value = {"Command": {"CommandId": "cmd-123"}}
            mock_ssm.get_command_invocation.return_value = {
                "Status": "Success",
            }

            from premium_manager import clear_ecs_agent_checkpoint

            result = clear_ecs_agent_checkpoint("i-test1")

            assert result is True
            mock_ssm.send_command.assert_called_once()
            mock_ssm.get_command_invocation.assert_called_once_with(
                CommandId="cmd-123", InstanceId="i-test1"
            )

    def test_command_fails(self, mock_env_vars_premium):
        """SSM command returns Failed status."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3, patch("premium_manager.time.sleep"):
            mock_ssm = MagicMock()
            mock_boto3.return_value = mock_ssm
            mock_ssm.describe_instance_information.return_value = {
                "InstanceInformationList": [{"PingStatus": "Online"}]
            }
            mock_ssm.send_command.return_value = {"Command": {"CommandId": "cmd-456"}}
            mock_ssm.get_command_invocation.return_value = {
                "Status": "Failed",
                "StandardErrorContent": "permission denied",
            }

            from premium_manager import clear_ecs_agent_checkpoint

            result = clear_ecs_agent_checkpoint("i-test2")

            assert result is False

    def test_send_command_client_error(self, mock_env_vars_premium):
        """SSM send_command raises ClientError."""
        from botocore.exceptions import ClientError

        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3, patch("premium_manager.time.sleep"):
            mock_ssm = MagicMock()
            mock_boto3.return_value = mock_ssm
            mock_ssm.describe_instance_information.return_value = {
                "InstanceInformationList": [{"PingStatus": "Online"}]
            }
            mock_ssm.send_command.side_effect = ClientError(
                {"Error": {"Code": "InvalidInstanceId"}},
                "SendCommand",
            )

            from premium_manager import clear_ecs_agent_checkpoint

            result = clear_ecs_agent_checkpoint("i-test3")

            assert result is False

    def test_timeout(self, mock_env_vars_premium):
        """SSM polling exhausts max wait time."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3, patch("premium_manager.time.sleep"):
            mock_ssm = MagicMock()
            mock_boto3.return_value = mock_ssm
            mock_ssm.describe_instance_information.return_value = {
                "InstanceInformationList": [{"PingStatus": "Online"}]
            }
            mock_ssm.send_command.return_value = {"Command": {"CommandId": "cmd-789"}}
            mock_ssm.get_command_invocation.return_value = {
                "Status": "InProgress",
            }

            from premium_manager import clear_ecs_agent_checkpoint

            result = clear_ecs_agent_checkpoint("i-test4")

            assert result is False


class TestDesiredCountReservesBootingStandbys:
    """update_premium_service_desired_count counts ACTIVE ECS container
    instances plus premium EC2s still inside the boot grace period, so
    a standby that is still registering with ECS keeps its service
    slot reserved instead of having its task placement cancelled."""

    @staticmethod
    def _make_mocks(
        mock_boto3,
        *,
        desired,
        running,
        registered=(),
        ec2_instances=(),
    ):
        """Configure boto3 mocks for update_premium_service_desired_count tests.

        registered: iterable of (ci_arn, ec2_id) pairs for ECS container instances.
        ec2_instances: iterable of (instance_id, minutes_since_launch) pairs.
        """
        mock_ecs = MagicMock()
        mock_ec2 = MagicMock()

        def boto3_client_side_effect(service):
            if service == "ecs":
                return mock_ecs
            if service == "ec2":
                return mock_ec2
            return MagicMock()

        mock_boto3.side_effect = boto3_client_side_effect

        mock_ecs.describe_services.return_value = {
            "services": [{"desiredCount": desired, "runningCount": running}]
        }
        mock_ecs.list_container_instances.return_value = {
            "containerInstanceArns": [arn for arn, _ in registered]
        }
        mock_ecs.describe_container_instances.return_value = {
            "containerInstances": [
                {"containerInstanceArn": arn, "ec2InstanceId": ec2_id}
                for arn, ec2_id in registered
            ]
        }
        now = datetime.now(timezone.utc)
        reservations = (
            [
                {
                    "Instances": [
                        {
                            "InstanceId": iid,
                            "LaunchTime": now - timedelta(minutes=mins),
                        }
                        for iid, mins in ec2_instances
                    ]
                }
            ]
            if ec2_instances
            else []
        )
        mock_ec2.describe_instances.return_value = {"Reservations": reservations}

        return mock_ecs, mock_ec2

    def test_updates_from_registered_count_when_no_booting(self, mock_env_vars_premium):
        """One registered CI, no booting EC2 -> desiredCount = 1."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3:
            mock_ecs, _ = self._make_mocks(
                mock_boto3,
                desired=4,
                running=1,
                registered=[("arn:aws:ecs:us-east-1:123:ci/a", "i-registered1")],
                ec2_instances=[("i-registered1", 60)],
            )

            from premium_manager import update_premium_service_desired_count

            update_premium_service_desired_count()

            mock_ecs.update_service.assert_called_once()
            assert mock_ecs.update_service.call_args[1]["desiredCount"] == 1

    def test_no_update_when_counts_match(self, mock_env_vars_premium):
        """desired already matches registered + booting -> no update."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3:
            mock_ecs, _ = self._make_mocks(
                mock_boto3,
                desired=2,
                running=2,
                registered=[
                    ("arn:aws:ecs:us-east-1:123:ci/a", "i-r1"),
                    ("arn:aws:ecs:us-east-1:123:ci/b", "i-r2"),
                ],
            )

            from premium_manager import update_premium_service_desired_count

            update_premium_service_desired_count()

            mock_ecs.update_service.assert_not_called()

    def test_counts_booting_standby_within_grace_period(self, mock_env_vars_premium):
        """Standby EC2 that just started but hasn't joined ECS yet is
        counted toward desiredCount so its task placement isn't
        cancelled before the agent registers."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3:
            mock_ecs, _ = self._make_mocks(
                mock_boto3,
                desired=1,
                running=1,
                registered=[("arn:aws:ecs:us-east-1:123:ci/a", "i-registered1")],
                ec2_instances=[("i-registered1", 60), ("i-booting1", 3)],
            )

            from premium_manager import update_premium_service_desired_count

            update_premium_service_desired_count()

            mock_ecs.update_service.assert_called_once()
            assert mock_ecs.update_service.call_args[1]["desiredCount"] == 2

    def test_ignores_orphan_past_grace_period(self, mock_env_vars_premium):
        """EC2 running past grace period without joining ECS is treated
        as an orphan and not counted (cleanup_orphaned_ec2_instances
        will stop it)."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3:
            mock_ecs, _ = self._make_mocks(
                mock_boto3,
                desired=2,
                running=1,
                registered=[("arn:aws:ecs:us-east-1:123:ci/a", "i-registered1")],
                ec2_instances=[("i-registered1", 60), ("i-orphan1", 30)],
            )

            from premium_manager import update_premium_service_desired_count

            update_premium_service_desired_count()

            mock_ecs.update_service.assert_called_once()
            assert mock_ecs.update_service.call_args[1]["desiredCount"] == 1

    def test_does_not_double_count_registered_instance(self, mock_env_vars_premium):
        """A registered CI whose EC2 is also returned by describe_instances
        is counted once, not twice."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3:
            mock_ecs, _ = self._make_mocks(
                mock_boto3,
                desired=0,
                running=0,
                registered=[("arn:aws:ecs:us-east-1:123:ci/a", "i-registered1")],
                ec2_instances=[("i-registered1", 2)],
            )

            from premium_manager import update_premium_service_desired_count

            update_premium_service_desired_count()

            mock_ecs.update_service.assert_called_once()
            assert mock_ecs.update_service.call_args[1]["desiredCount"] == 1


class TestRoutingIdContract:
    """Lambda and middleware must produce identical
    routing IDs for the same Firebase UID + secret."""

    def test_handler_passes_firebase_uid_to_assign(self, mock_env_vars_premium):
        """handler() must pass the original Firebase UID
        string (not the numeric DB ID) as user_uid to
        assign_premium_user."""
        event = {
            "httpMethod": "POST",
            "path": "/premium/assign",
            "body": json.dumps(
                {
                    "action": "assign",
                    "user_id": "firebase_xyz_999",
                    "tier": "premium",
                }
            ),
            "requestContext": {"requestId": "req-1"},
        }
        mock_context = MagicMock()

        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "pymysql.connect"
        ) as mock_pymysql, patch(
            "premium_manager.assign_premium_user",
            return_value={
                "statusCode": 200,
                "body": "{}",
            },
        ) as mock_assign:
            mock_connection = setup_db_mock(
                fetchone_values=[{"id": 42}],
            )
            mock_pymysql.return_value = mock_connection

            from premium_manager import handler

            handler(event, mock_context)

            mock_assign.assert_called_once()
            _, _, user_uid_arg = mock_assign.call_args[0]
            assert user_uid_arg == "firebase_xyz_999"

    def test_same_routing_id_for_firebase_uid(self):
        """Both implementations produce identical output
        for the same Firebase UID + secret."""
        from premium_manager import generate_routing_id as lambda_gen

        from studio.app.common.core.middleware.secure_routing_middleware import (  # noqa: E501
            generate_routing_id as middleware_gen,
        )

        uid = "firebase_user_abc123"
        secret = "shared-secret-key"

        assert lambda_gen(uid, secret) == middleware_gen(uid, secret)

    def test_numeric_id_differs_from_firebase_uid(self):
        """Numeric DB ID and Firebase UID produce different
        routing IDs -- a routing mismatch root cause."""
        from premium_manager import generate_routing_id

        secret = "test-secret"
        numeric_id = generate_routing_id("123", secret)
        firebase_uid = generate_routing_id("firebaseXYZ", secret)

        assert numeric_id != firebase_uid


class TestScaleDownIfPossible:
    """scale_down_if_possible stops idle instances and registers
    them as standby in the database for later termination."""

    def _make_instance(self, instance_id, state="running"):
        return {"instance_id": instance_id, "state": state}

    def test_stops_idle_and_registers_standby(self, mock_env_vars_premium):
        """Idle instances are stopped, deregistered from ECS,
        and registered as standby in the DB."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3, patch(
            "premium_manager.get_dynamic_max_capacity", return_value=10
        ), patch(
            "premium_manager.count_active_premium_users", return_value=0
        ), patch(
            "premium_manager.count_total_premium_users", return_value=5
        ), patch(
            "premium_manager.get_all_premium_instances_with_states"
        ) as mock_get_instances, patch(
            "premium_manager.get_assigned_users_for_instance"
        ) as mock_get_users, patch(
            "premium_manager.deregister_container_instance_from_ecs"
        ) as mock_deregister, patch(
            "premium_manager.store_user_assignment"
        ) as mock_store, patch(
            "premium_manager.update_premium_service_desired_count"
        ):
            mock_ec2 = MagicMock()
            mock_boto3.return_value = mock_ec2

            # 3 running, 0 users -> 3 idle (>= 2), min_needed = 1
            mock_get_instances.return_value = [
                self._make_instance("i-idle1"),
                self._make_instance("i-idle2"),
                self._make_instance("i-idle3"),
            ]
            mock_get_users.return_value = []

            from premium_manager import scale_down_if_possible

            scale_down_if_possible()

            # Should stop idle instances
            mock_ec2.stop_instances.assert_called_once()
            stopped_ids = mock_ec2.stop_instances.call_args[1]["InstanceIds"]
            assert len(stopped_ids) >= 1

            # Each stopped instance should be deregistered from ECS
            for iid in stopped_ids:
                mock_deregister.assert_any_call(iid)

            # Each stopped instance should be registered as standby
            assert mock_store.call_count == len(stopped_ids)
            for call in mock_store.call_args_list:
                assert call[1]["user_id"] is None
                assert call[1]["instance_state"] == "stopped"
                assert call[1]["is_standby"] is True

    def test_no_scale_down_when_idle_below_threshold(self, mock_env_vars_premium):
        """No scale-down when fewer than 2 idle instances."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3, patch(
            "premium_manager.get_dynamic_max_capacity", return_value=10
        ), patch(
            "premium_manager.count_active_premium_users", return_value=0
        ), patch(
            "premium_manager.count_total_premium_users", return_value=5
        ), patch(
            "premium_manager.get_all_premium_instances_with_states"
        ) as mock_get_instances, patch(
            "premium_manager.get_assigned_users_for_instance"
        ) as mock_get_users, patch(
            "premium_manager.store_user_assignment"
        ) as mock_store:
            mock_ec2 = MagicMock()
            mock_boto3.return_value = mock_ec2

            # 1 running, 0 users -> 1 idle (< 2 threshold)
            mock_get_instances.return_value = [
                self._make_instance("i-only1"),
            ]
            mock_get_users.return_value = []

            from premium_manager import scale_down_if_possible

            scale_down_if_possible()

            mock_ec2.stop_instances.assert_not_called()
            mock_store.assert_not_called()

    def test_standby_registration_failure_does_not_block(self, mock_env_vars_premium):
        """If store_user_assignment fails for one instance, the
        remaining instances are still registered."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3, patch(
            "premium_manager.get_dynamic_max_capacity", return_value=10
        ), patch(
            "premium_manager.count_active_premium_users", return_value=0
        ), patch(
            "premium_manager.count_total_premium_users", return_value=5
        ), patch(
            "premium_manager.get_all_premium_instances_with_states"
        ) as mock_get_instances, patch(
            "premium_manager.get_assigned_users_for_instance"
        ) as mock_get_users, patch(
            "premium_manager.deregister_container_instance_from_ecs"
        ), patch(
            "premium_manager.store_user_assignment"
        ) as mock_store, patch(
            "premium_manager.update_premium_service_desired_count"
        ):
            mock_ec2 = MagicMock()
            mock_boto3.return_value = mock_ec2

            # 4 running, 0 users -> 4 idle
            mock_get_instances.return_value = [
                self._make_instance(f"i-idle{i}") for i in range(4)
            ]
            mock_get_users.return_value = []

            # First call fails, rest succeed
            mock_store.side_effect = [
                Exception("DB error"),
                None,
                None,
            ]

            from premium_manager import scale_down_if_possible

            # Should not raise despite the DB error
            scale_down_if_possible()

            mock_ec2.stop_instances.assert_called_once()
            # All stopped instances attempted registration
            assert mock_store.call_count == len(
                mock_ec2.stop_instances.call_args[1]["InstanceIds"]
            )

    def test_occupied_instances_not_stopped(self, mock_env_vars_premium):
        """Instances with assigned users are never stopped."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3, patch(
            "premium_manager.get_dynamic_max_capacity", return_value=10
        ), patch(
            "premium_manager.count_active_premium_users", return_value=1
        ), patch(
            "premium_manager.count_total_premium_users", return_value=5
        ), patch(
            "premium_manager.get_all_premium_instances_with_states"
        ) as mock_get_instances, patch(
            "premium_manager.get_assigned_users_for_instance"
        ) as mock_get_users, patch(
            "premium_manager.deregister_container_instance_from_ecs"
        ), patch(
            "premium_manager.store_user_assignment"
        ) as mock_store, patch(
            "premium_manager.update_premium_service_desired_count"
        ):
            mock_ec2 = MagicMock()
            mock_boto3.return_value = mock_ec2

            # 4 running: 1 occupied + 3 idle
            mock_get_instances.return_value = [
                self._make_instance("i-occupied"),
                self._make_instance("i-idle1"),
                self._make_instance("i-idle2"),
                self._make_instance("i-idle3"),
            ]

            def users_side_effect(iid):
                if iid == "i-occupied":
                    return [{"user_id": 42}]
                return []

            mock_get_users.side_effect = users_side_effect

            from premium_manager import scale_down_if_possible

            scale_down_if_possible()

            stopped_ids = mock_ec2.stop_instances.call_args[1]["InstanceIds"]
            assert "i-occupied" not in stopped_ids
            # All stopped instances registered as standby
            assert mock_store.call_count == len(stopped_ids)


class TestCleanupOrphanedEC2Instances:
    """cleanup_orphaned_ec2_instances stops unregistered
    instances past the grace period and skips the rest."""

    def test_stops_orphaned_past_grace_period(self, mock_env_vars_premium):
        """EC2 instance running 20 min and NOT in ECS
        container instances gets stopped and registered as standby."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3, patch("premium_manager.store_user_assignment") as mock_store:
            mock_ecs = MagicMock()
            mock_ec2 = MagicMock()

            def boto3_client_side_effect(service):
                if service == "ecs":
                    return mock_ecs
                if service == "ec2":
                    return mock_ec2
                return MagicMock()

            mock_boto3.side_effect = boto3_client_side_effect

            mock_ecs.list_container_instances.return_value = {
                "containerInstanceArns": []
            }

            # 20 minutes ago -- past grace period
            launch_time = datetime.now(timezone.utc) - timedelta(minutes=20)
            mock_ec2.describe_instances.return_value = {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": "i-orphan1",
                                "State": {"Name": "running"},
                                "LaunchTime": launch_time,
                                "Tags": [
                                    {
                                        "Key": "Tier",
                                        "Value": (
                                            PremiumInstanceConfig.INSTANCE_IDENTIFIER
                                        ),
                                    }
                                ],
                            }
                        ]
                    }
                ]
            }

            from premium_manager import cleanup_orphaned_ec2_instances

            cleanup_orphaned_ec2_instances()

            mock_ec2.stop_instances.assert_called_once_with(InstanceIds=["i-orphan1"])
            mock_store.assert_called_once()
            call_kwargs = mock_store.call_args[1]
            assert call_kwargs["user_id"] is None
            assert call_kwargs["instance_id"] == "i-orphan1"
            assert call_kwargs["instance_state"] == "stopped"
            assert call_kwargs["is_standby"] is True

    def test_orphan_standby_registration_failure_does_not_block(
        self, mock_env_vars_premium
    ):
        """If store_user_assignment fails for one orphan, remaining
        orphans are still stopped and registered."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3, patch("premium_manager.store_user_assignment") as mock_store:
            mock_ecs = MagicMock()
            mock_ec2 = MagicMock()

            def boto3_client_side_effect(service):
                if service == "ecs":
                    return mock_ecs
                if service == "ec2":
                    return mock_ec2
                return MagicMock()

            mock_boto3.side_effect = boto3_client_side_effect

            mock_ecs.list_container_instances.return_value = {
                "containerInstanceArns": []
            }

            launch_time = datetime.now(timezone.utc) - timedelta(minutes=20)
            mock_ec2.describe_instances.return_value = {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": f"i-orphan{i}",
                                "State": {"Name": "running"},
                                "LaunchTime": launch_time,
                            }
                            for i in range(2)
                        ]
                    }
                ]
            }

            # First registration fails, second succeeds
            mock_store.side_effect = [Exception("DB error"), None]

            from premium_manager import cleanup_orphaned_ec2_instances

            cleanup_orphaned_ec2_instances()

            # Both instances should be stopped
            assert mock_ec2.stop_instances.call_count == 2
            # Both registrations attempted
            assert mock_store.call_count == 2

    def test_skips_instance_within_grace_period(self, mock_env_vars_premium):
        """EC2 instance running 5 min is within grace period
        and must NOT be stopped."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3:
            mock_ecs = MagicMock()
            mock_ec2 = MagicMock()

            def boto3_client_side_effect(service):
                if service == "ecs":
                    return mock_ecs
                if service == "ec2":
                    return mock_ec2
                return MagicMock()

            mock_boto3.side_effect = boto3_client_side_effect

            mock_ecs.list_container_instances.return_value = {
                "containerInstanceArns": []
            }

            # 5 minutes ago -- within grace period
            launch_time = datetime.now(timezone.utc) - timedelta(minutes=5)
            mock_ec2.describe_instances.return_value = {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": "i-young1",
                                "State": {"Name": "running"},
                                "LaunchTime": launch_time,
                                "Tags": [
                                    {
                                        "Key": "Tier",
                                        "Value": (
                                            PremiumInstanceConfig.INSTANCE_IDENTIFIER
                                        ),
                                    }
                                ],
                            }
                        ]
                    }
                ]
            }

            from premium_manager import cleanup_orphaned_ec2_instances

            cleanup_orphaned_ec2_instances()

            mock_ec2.stop_instances.assert_not_called()

    def test_skips_instance_registered_in_ecs(self, mock_env_vars_premium):
        """EC2 instance registered as ECS container instance
        must NOT be stopped even if old."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3:
            mock_ecs = MagicMock()
            mock_ec2 = MagicMock()

            def boto3_client_side_effect(service):
                if service == "ecs":
                    return mock_ecs
                if service == "ec2":
                    return mock_ec2
                return MagicMock()

            mock_boto3.side_effect = boto3_client_side_effect

            mock_ecs.list_container_instances.return_value = {
                "containerInstanceArns": ["arn:aws:ecs:r:a:ci/registered"]
            }
            mock_ecs.describe_container_instances.return_value = {
                "containerInstances": [
                    {
                        "containerInstanceArn": ("arn:aws:ecs:r:a:ci/registered"),
                        "ec2InstanceId": "i-healthy1",
                    }
                ]
            }

            # 60 minutes ago -- old but healthy
            launch_time = datetime.now(timezone.utc) - timedelta(minutes=60)
            mock_ec2.describe_instances.return_value = {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": "i-healthy1",
                                "State": {"Name": "running"},
                                "LaunchTime": launch_time,
                                "Tags": [
                                    {
                                        "Key": "Tier",
                                        "Value": (
                                            PremiumInstanceConfig.INSTANCE_IDENTIFIER
                                        ),
                                    }
                                ],
                            }
                        ]
                    }
                ]
            }

            from premium_manager import cleanup_orphaned_ec2_instances

            cleanup_orphaned_ec2_instances()

            mock_ec2.stop_instances.assert_not_called()


class TestHandleScheduledMonitoring:
    """handle_scheduled_monitoring acquires the distributed scaling
    lock, runs the monitor steps in order, and skips cleanly when
    another invocation holds the lock."""

    @staticmethod
    def _lock_ctx(acquired: bool):
        mock = MagicMock()
        mock.return_value.__enter__.return_value = acquired
        mock.return_value.__exit__.return_value = False
        return mock

    def test_registers_orphans_before_terminating_aged(self, mock_env_vars_premium):
        """register_orphaned_stopped_instances runs before
        terminate_aged_stopped_instances so ghost instances
        become visible to the terminator."""
        call_order = []

        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "premium_manager.distributed_lock", new=self._lock_ctx(True)
        ), patch("premium_manager.count_active_premium_users", return_value=0), patch(
            "premium_manager.count_total_premium_users", return_value=0
        ), patch(
            "premium_manager.get_all_premium_instances_with_states",
            return_value=[],
        ), patch(
            "premium_manager.get_assigned_users_for_instance",
            return_value=[],
        ), patch(
            "premium_manager.publish_premium_metrics"
        ), patch(
            "premium_manager.scale_down_if_possible"
        ), patch(
            "premium_manager.update_premium_service_desired_count"
        ), patch(
            "premium_manager.cleanup_failed_standby_instances"
        ), patch(
            "premium_manager.register_orphaned_stopped_instances",
            side_effect=lambda: call_order.append("register_orphans"),
        ), patch(
            "premium_manager.terminate_aged_stopped_instances",
            side_effect=lambda: call_order.append("terminate_aged"),
        ), patch(
            "premium_manager.get_standby_count", return_value=0
        ), patch(
            "premium_manager.finalize_expired_pending_releases",
            return_value=[],
        ), patch(
            "premium_manager.cleanup_ghost_ecs_registrations"
        ), patch(
            "premium_manager.cleanup_orphaned_ec2_instances"
        ), patch(
            "premium_manager.fix_incorrect_is_shared_flags",
            return_value={"fixed_count": 0},
        ), patch(
            "premium_manager.process_shared_instance_optimization",
            return_value={"migrations_performed": 0, "shared_instances_found": 0},
        ):
            from premium_manager import handle_scheduled_monitoring

            result = handle_scheduled_monitoring({"source": "test"}, None)
            assert result["statusCode"] == 200

            assert call_order == ["register_orphans", "terminate_aged"]

    def test_skips_when_lock_not_acquired(self, mock_env_vars_premium):
        """When another invocation holds the scaling lock, the handler
        returns the 'skipped' response and does not call any scaling
        step. Regression guard for the pre-fix bug where a stale
        CloudWatch-metric lock kept the handler blackholed for ~15
        minutes after every scaling op."""
        mock_step = MagicMock()

        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "premium_manager.distributed_lock", new=self._lock_ctx(False)
        ), patch(
            "premium_manager.count_active_premium_users", side_effect=mock_step
        ), patch(
            "premium_manager.scale_down_if_possible", side_effect=mock_step
        ), patch(
            "premium_manager.update_premium_service_desired_count",
            side_effect=mock_step,
        ), patch(
            "premium_manager.cleanup_ghost_ecs_registrations", side_effect=mock_step
        ), patch(
            "premium_manager.cleanup_orphaned_ec2_instances", side_effect=mock_step
        ):
            from premium_manager import handle_scheduled_monitoring

            result = handle_scheduled_monitoring({"source": "test"}, None)

            assert result["statusCode"] == 200
            body = json.loads(result["body"])
            assert body["status"] == "skipped"
            assert "already in progress" in body["message"]
            mock_step.assert_not_called()

    def test_acquires_non_blocking_with_correct_lock_name(self, mock_env_vars_premium):
        """The monitor must call distributed_lock with timeout=0 so
        contention skips (not waits) and with PREMIUM_SCALING_LOCK so
        it interlocks with other invocations of this same handler.
        A typo or a blocking default would be a silent regression."""
        lock_mock = self._lock_ctx(False)  # not acquired → fast skip path

        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "premium_manager.distributed_lock", new=lock_mock
        ):
            from premium_manager import (
                PREMIUM_SCALING_LOCK,
                handle_scheduled_monitoring,
            )

            handle_scheduled_monitoring({"source": "test"}, None)

            lock_mock.assert_called_once_with(PREMIUM_SCALING_LOCK, timeout=0)

    def test_releases_lock_on_monitor_exception(self, mock_env_vars_premium):
        """When a step inside the with-block raises, the handler
        returns a structured 500 and the context manager's __exit__ is
        still called (guaranteeing lock release). Regression guard for
        a future refactor that moves try/except outside the with-block
        or re-introduces a separate release call."""
        lock_mock = self._lock_ctx(True)

        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "premium_manager.distributed_lock", new=lock_mock
        ), patch(
            "premium_manager.count_active_premium_users",
            side_effect=RuntimeError("boom"),
        ):
            from premium_manager import handle_scheduled_monitoring

            result = handle_scheduled_monitoring({"source": "test"}, None)

            assert result["statusCode"] == 500
            body = json.loads(result["body"])
            assert body["status"] == "error"
            assert "boom" in body["error"]
            # Context manager exit fires regardless of the exception →
            # GET_LOCK is released by the helper's `finally` on the DB
            # connection.
            lock_mock.return_value.__exit__.assert_called_once()


class TestGetUserUidFromId:
    """Reverse UID lookup from numeric DB ID."""

    def test_returns_firebase_uid(self, mock_env_vars_premium):
        """Returns the Firebase UID for a known user_id."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "pymysql.connect"
        ) as mock_pymysql:
            mock_connection = setup_db_mock(
                fetchone_values=[{"uid": "firebase_abc"}],
            )
            mock_pymysql.return_value = mock_connection

            from premium_manager import get_user_uid_from_id

            result = get_user_uid_from_id(mock_connection, 42)
            assert result == "firebase_abc"

    def test_raises_for_unknown_id(self, mock_env_vars_premium):
        """Raises ValueError when user_id is not found."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "pymysql.connect"
        ) as mock_pymysql:
            mock_connection = setup_db_mock(
                fetchone_values=[None],
            )
            mock_pymysql.return_value = mock_connection

            from premium_manager import get_user_uid_from_id

            with pytest.raises(ValueError, match="not found"):
                get_user_uid_from_id(mock_connection, 9999)


class TestGetPremiumUserStatus:
    """Tests for get_premium_user_status Lambda function."""

    def test_returns_status_for_active_user(self, mock_env_vars_premium):
        """Returns 200 with assignment details for an active user."""
        test_user_id = 12345
        assigned_at = datetime(2026, 3, 27, 2, 3, 26)

        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "pymysql.connect"
        ) as mock_pymysql, patch("boto3.client") as mock_boto3:
            mock_connection = setup_db_mock(
                fetchone_values=[
                    MockRow(
                        {
                            "instance_id": TEST_INSTANCE_ID,
                            "target_group_arn": "arn:aws:tg/test",
                            "alb_rule_arn": "arn:aws:rule/test",
                            "status": "active",
                            "assigned_at": assigned_at,
                            "is_shared": 0,
                        }
                    ),
                ],
            )
            mock_pymysql.return_value = mock_connection
            # Mock EC2 describe_instances to return running instance
            mock_ec2 = MagicMock()
            mock_ec2.describe_instances.return_value = {
                "Reservations": [{"Instances": [{"State": {"Name": "running"}}]}]
            }
            mock_boto3.return_value = mock_ec2

            from premium_manager import get_premium_user_status

            result = get_premium_user_status(test_user_id)

            assert result["statusCode"] == 200
            body = json.loads(result["body"])
            assert body["user_id"] == test_user_id
            assert body["instance_id"] == TEST_INSTANCE_ID
            assert body["status"] == "active"
            assert body["assigned_at"] == assigned_at.isoformat()
            assert body["is_shared"] is False

    def test_returns_404_for_unassigned_user(self, mock_env_vars_premium):
        """Returns 404 when user has no premium assignment."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "pymysql.connect"
        ) as mock_pymysql:
            mock_connection = setup_db_mock(
                fetchone_values=[None],
            )
            mock_pymysql.return_value = mock_connection

            from premium_manager import get_premium_user_status

            result = get_premium_user_status(99999)

            assert result["statusCode"] == 404

    def test_returns_null_assigned_at_when_missing(self, mock_env_vars_premium):
        """Returns assigned_at as null when the column value is None."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "pymysql.connect"
        ) as mock_pymysql, patch("boto3.client") as mock_boto3:
            mock_connection = setup_db_mock(
                fetchone_values=[
                    MockRow(
                        {
                            "instance_id": TEST_INSTANCE_ID,
                            "target_group_arn": "arn:aws:tg/test",
                            "alb_rule_arn": "arn:aws:rule/test",
                            "status": "active",
                            "assigned_at": None,
                            "is_shared": 1,
                        }
                    ),
                ],
            )
            mock_pymysql.return_value = mock_connection
            # Mock EC2 describe_instances to return running instance
            mock_ec2 = MagicMock()
            mock_ec2.describe_instances.return_value = {
                "Reservations": [{"Instances": [{"State": {"Name": "running"}}]}]
            }
            mock_boto3.return_value = mock_ec2

            from premium_manager import get_premium_user_status

            result = get_premium_user_status(12345)

            assert result["statusCode"] == 200
            body = json.loads(result["body"])
            assert body["assigned_at"] is None
            assert body["is_shared"] is True

    def test_pending_release_restored_includes_assigned_at(self, mock_env_vars_premium):
        """When status is pending_release, restore_pending_release is called
        and the response still includes assigned_at without KeyError."""
        test_user_id = 12345
        assigned_at = datetime(2026, 3, 27, 2, 0, 0)

        restored_assignment = {
            "user_id": test_user_id,
            "instance_id": TEST_INSTANCE_ID,
            "target_group_arn": "arn:aws:tg/restored",
            "alb_rule_arn": "arn:aws:rule/restored",
            "status": "terminating",
            "instance_state": "running",
            "is_shared": 0,
            "assigned_at": assigned_at,
        }

        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "pymysql.connect"
        ) as mock_pymysql, patch(
            "premium_manager.restore_pending_release"
        ) as mock_restore:
            mock_connection = setup_db_mock(
                fetchone_values=[
                    # Initial query returns pending_release status
                    MockRow(
                        {
                            "instance_id": TEST_INSTANCE_ID,
                            "target_group_arn": "arn:aws:tg/test",
                            "alb_rule_arn": "arn:aws:rule/test",
                            "status": "terminating",
                            "assigned_at": assigned_at,
                            "is_shared": 0,
                        }
                    ),
                ],
            )
            mock_pymysql.return_value = mock_connection
            mock_restore.return_value = restored_assignment

            from premium_manager import get_premium_user_status

            result = get_premium_user_status(test_user_id)

            assert result["statusCode"] == 200
            body = json.loads(result["body"])
            assert body["assigned_at"] == assigned_at.isoformat()
            assert body["status"] == "active"
            mock_restore.assert_called_once_with(test_user_id)

    def test_pending_release_restore_fails_gracefully(self, mock_env_vars_premium):
        """When restore_pending_release raises, the original assignment
        is still returned."""
        test_user_id = 12345
        assigned_at = datetime(2026, 3, 27, 2, 0, 0)

        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "pymysql.connect"
        ) as mock_pymysql, patch(
            "premium_manager.restore_pending_release"
        ) as mock_restore:
            mock_connection = setup_db_mock(
                fetchone_values=[
                    MockRow(
                        {
                            "instance_id": TEST_INSTANCE_ID,
                            "target_group_arn": "arn:aws:tg/test",
                            "alb_rule_arn": "arn:aws:rule/test",
                            "status": "terminating",
                            "assigned_at": assigned_at,
                            "is_shared": 0,
                        }
                    ),
                ],
            )
            mock_pymysql.return_value = mock_connection
            mock_restore.side_effect = Exception("restore failed")

            from premium_manager import get_premium_user_status

            result = get_premium_user_status(test_user_id)

            assert result["statusCode"] == 200
            body = json.loads(result["body"])
            # Original assignment returned with terminating status
            assert body["status"] == "terminating"
            assert body["assigned_at"] == assigned_at.isoformat()


class TestCleanupGhostECSRegistrations:
    """cleanup_ghost_ecs_registrations must only deregister premium
    instances, and must apply a grace period for agent disconnects
    on running EC2 instances."""

    def _make_clients(self, mock_boto3):
        mock_ecs = MagicMock()
        mock_ec2 = MagicMock()

        def client_factory(service):
            if service == "ecs":
                return mock_ecs
            if service == "ec2":
                return mock_ec2
            return MagicMock()

        mock_boto3.side_effect = client_factory
        return mock_ecs, mock_ec2

    def test_uses_premium_tier_filter(self, mock_env_vars_premium):
        """list_container_instances must include the premium tier filter."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3:
            mock_ecs, _ = self._make_clients(mock_boto3)
            mock_ecs.list_container_instances.return_value = {
                "containerInstanceArns": []
            }

            from premium_manager import cleanup_ghost_ecs_registrations

            cleanup_ghost_ecs_registrations()

            mock_ecs.list_container_instances.assert_called_once_with(
                cluster="test-cluster",
                filter="attribute:tier == premium",
            )

    def test_deregisters_stopped_ec2_immediately(self, mock_env_vars_premium):
        """Instance whose EC2 is stopped gets deregistered with no grace."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3:
            mock_ecs, mock_ec2 = self._make_clients(mock_boto3)
            ci_arn = "arn:aws:ecs:r:a:ci/ghost1"
            mock_ecs.list_container_instances.return_value = {
                "containerInstanceArns": [ci_arn]
            }
            mock_ecs.describe_container_instances.return_value = {
                "containerInstances": [
                    {
                        "containerInstanceArn": ci_arn,
                        "ec2InstanceId": "i-stopped1",
                        "agentConnected": False,
                        "status": "ACTIVE",
                    }
                ]
            }
            mock_ec2.describe_instances.return_value = {
                "Reservations": [
                    {"Instances": [{"State": {"Name": "stopped"}, "Tags": []}]}
                ]
            }

            from premium_manager import cleanup_ghost_ecs_registrations

            cleanup_ghost_ecs_registrations()

            mock_ecs.deregister_container_instance.assert_called_once_with(
                cluster="test-cluster",
                containerInstance=ci_arn,
                force=True,
            )

    def test_tags_running_instance_on_first_disconnect(self, mock_env_vars_premium):
        """Running EC2 with disconnected agent gets tagged but not deregistered."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3:
            mock_ecs, mock_ec2 = self._make_clients(mock_boto3)
            ci_arn = "arn:aws:ecs:r:a:ci/disc1"
            mock_ecs.list_container_instances.return_value = {
                "containerInstanceArns": [ci_arn]
            }
            mock_ecs.describe_container_instances.return_value = {
                "containerInstances": [
                    {
                        "containerInstanceArn": ci_arn,
                        "ec2InstanceId": "i-running1",
                        "agentConnected": False,
                        "status": "ACTIVE",
                    }
                ]
            }
            mock_ec2.describe_instances.return_value = {
                "Reservations": [
                    {"Instances": [{"State": {"Name": "running"}, "Tags": []}]}
                ]
            }

            from premium_manager import cleanup_ghost_ecs_registrations

            cleanup_ghost_ecs_registrations()

            mock_ecs.deregister_container_instance.assert_not_called()
            mock_ec2.create_tags.assert_called_once()
            tag_call = mock_ec2.create_tags.call_args
            assert tag_call.kwargs["Resources"] == ["i-running1"]
            assert tag_call.kwargs["Tags"][0]["Key"] == "optinist:agent-disconnected-at"

    def test_skips_within_grace_period(self, mock_env_vars_premium):
        """Running EC2 tagged recently is within grace period — skip."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3:
            mock_ecs, mock_ec2 = self._make_clients(mock_boto3)
            ci_arn = "arn:aws:ecs:r:a:ci/grace1"
            mock_ecs.list_container_instances.return_value = {
                "containerInstanceArns": [ci_arn]
            }
            mock_ecs.describe_container_instances.return_value = {
                "containerInstances": [
                    {
                        "containerInstanceArn": ci_arn,
                        "ec2InstanceId": "i-grace1",
                        "agentConnected": False,
                        "status": "ACTIVE",
                    }
                ]
            }
            # Tagged 2 minutes ago — within the 5-minute grace
            recent = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
            mock_ec2.describe_instances.return_value = {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "State": {"Name": "running"},
                                "Tags": [
                                    {
                                        "Key": "optinist:agent-disconnected-at",
                                        "Value": recent,
                                    }
                                ],
                            }
                        ]
                    }
                ]
            }

            from premium_manager import cleanup_ghost_ecs_registrations

            cleanup_ghost_ecs_registrations()

            mock_ecs.deregister_container_instance.assert_not_called()

    def test_deregisters_after_grace_period(self, mock_env_vars_premium):
        """Running EC2 tagged over 5 minutes ago gets deregistered."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3:
            mock_ecs, mock_ec2 = self._make_clients(mock_boto3)
            ci_arn = "arn:aws:ecs:r:a:ci/expired1"
            mock_ecs.list_container_instances.return_value = {
                "containerInstanceArns": [ci_arn]
            }
            mock_ecs.describe_container_instances.return_value = {
                "containerInstances": [
                    {
                        "containerInstanceArn": ci_arn,
                        "ec2InstanceId": "i-expired1",
                        "agentConnected": False,
                        "status": "ACTIVE",
                    }
                ]
            }
            # Tagged 10 minutes ago — past the 5-minute grace
            old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
            mock_ec2.describe_instances.return_value = {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "State": {"Name": "running"},
                                "Tags": [
                                    {
                                        "Key": "optinist:agent-disconnected-at",
                                        "Value": old,
                                    }
                                ],
                            }
                        ]
                    }
                ]
            }

            from premium_manager import cleanup_ghost_ecs_registrations

            cleanup_ghost_ecs_registrations()

            mock_ecs.deregister_container_instance.assert_called_once_with(
                cluster="test-cluster",
                containerInstance=ci_arn,
                force=True,
            )

    def test_clears_tag_on_reconnect(self, mock_env_vars_premium):
        """Connected agent triggers delete_tags to clear disconnect tag."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3:
            mock_ecs, mock_ec2 = self._make_clients(mock_boto3)
            ci_arn = "arn:aws:ecs:r:a:ci/healthy1"
            mock_ecs.list_container_instances.return_value = {
                "containerInstanceArns": [ci_arn]
            }
            mock_ecs.describe_container_instances.return_value = {
                "containerInstances": [
                    {
                        "containerInstanceArn": ci_arn,
                        "ec2InstanceId": "i-healthy1",
                        "agentConnected": True,
                        "status": "ACTIVE",
                    }
                ]
            }

            from premium_manager import cleanup_ghost_ecs_registrations

            cleanup_ghost_ecs_registrations()

            mock_ecs.deregister_container_instance.assert_not_called()
            mock_ec2.delete_tags.assert_called_once_with(
                Resources=["i-healthy1"],
                Tags=[{"Key": "optinist:agent-disconnected-at"}],
            )

    def test_mixed_cluster_only_deregisters_ghost(self, mock_env_vars_premium):
        """With a healthy and a stopped instance in the filtered results,
        only the stopped one is deregistered."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3:
            mock_ecs, mock_ec2 = self._make_clients(mock_boto3)
            healthy_arn = "arn:aws:ecs:r:a:ci/healthy"
            ghost_arn = "arn:aws:ecs:r:a:ci/ghost"
            mock_ecs.list_container_instances.return_value = {
                "containerInstanceArns": [healthy_arn, ghost_arn]
            }
            mock_ecs.describe_container_instances.return_value = {
                "containerInstances": [
                    {
                        "containerInstanceArn": healthy_arn,
                        "ec2InstanceId": "i-healthy",
                        "agentConnected": True,
                        "status": "ACTIVE",
                    },
                    {
                        "containerInstanceArn": ghost_arn,
                        "ec2InstanceId": "i-ghost",
                        "agentConnected": False,
                        "status": "ACTIVE",
                    },
                ]
            }
            mock_ec2.describe_instances.return_value = {
                "Reservations": [
                    {"Instances": [{"State": {"Name": "terminated"}, "Tags": []}]}
                ]
            }

            from premium_manager import cleanup_ghost_ecs_registrations

            cleanup_ghost_ecs_registrations()

            # Only the ghost gets deregistered
            mock_ecs.deregister_container_instance.assert_called_once_with(
                cluster="test-cluster",
                containerInstance=ghost_arn,
                force=True,
            )
            # EC2 describe only called for the disconnected instance
            mock_ec2.describe_instances.assert_called_once_with(InstanceIds=["i-ghost"])
            # Healthy instance's tag is cleared
            mock_ec2.delete_tags.assert_any_call(
                Resources=["i-healthy"],
                Tags=[{"Key": "optinist:agent-disconnected-at"}],
            )

    def test_deregisters_disconnected_without_ec2_id(self, mock_env_vars_premium):
        """Container instance with disconnected agent and no ec2InstanceId
        is deregistered immediately."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3:
            mock_ecs, mock_ec2 = self._make_clients(mock_boto3)
            ci_arn = "arn:aws:ecs:r:a:ci/no-ec2"
            mock_ecs.list_container_instances.return_value = {
                "containerInstanceArns": [ci_arn]
            }
            mock_ecs.describe_container_instances.return_value = {
                "containerInstances": [
                    {
                        "containerInstanceArn": ci_arn,
                        "ec2InstanceId": "",
                        "agentConnected": False,
                        "status": "ACTIVE",
                    }
                ]
            }

            from premium_manager import cleanup_ghost_ecs_registrations

            cleanup_ghost_ecs_registrations()

            mock_ecs.deregister_container_instance.assert_called_once_with(
                cluster="test-cluster",
                containerInstance=ci_arn,
                force=True,
            )
            # No EC2 calls should be made
            mock_ec2.describe_instances.assert_not_called()
