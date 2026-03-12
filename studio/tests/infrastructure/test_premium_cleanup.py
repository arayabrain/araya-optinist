"""Tests for premium_cleanup Lambda function."""

from unittest.mock import MagicMock, patch

from aws_constants import ECSTaskStatus, PremiumAssignment, RoutingHeaders
from conftest import MockRow, setup_db_mock

TEST_INSTANCE_ID = "i-testlambda123"


class TestPremiumCleanupHandler:
    """Handler-level tests for premium_cleanup Lambda."""

    def test_scheduled_event(self, mock_env_vars_premium):
        """Test premium_cleanup Lambda with scheduled event."""
        print("Testing Premium Cleanup Lambda - Scheduled Event")
        print("=" * 50)

        scheduled_event = {
            "source": "aws.events",
            "detail-type": "Scheduled Event",
            "detail": {"action": "cleanup"},
            "time": "2025-09-17T10:00:00Z",
        }

        mock_context = MagicMock()
        mock_context.function_name = "subscr-premium-cleanup"

        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3, patch("pymysql.connect") as mock_pymysql:
            mock_connection = setup_db_mock(
                fetchone_values=[
                    MockRow({"count": 0}),
                    MockRow({"count": 1}),
                    MockRow({"count": 0}),
                    MockRow({"count": 1}),
                    MockRow({"count": 3}),
                    MockRow({"count": 0}),
                ],
                fetchall_values=[
                    [],
                    [],
                    [],
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

            mock_ec2 = MagicMock()
            mock_boto3.return_value = mock_ec2
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
                                    }
                                ],
                            }
                        ]
                    }
                ]
            }

            from premium_cleanup import handler

            result = handler(scheduled_event, mock_context)

            assert isinstance(result, dict)
            assert result["statusCode"] == 200


class TestCheckInstanceReadiness:
    """check_instance_readiness tests."""

    def test_success(self, mock_env_vars_premium):
        """Running premium ECS task returns True."""
        test_inst = "i-ready123"
        ci_arn = "arn:aws:ecs:us-east-1:123:container-instance/ci1"
        task_arn = "arn:aws:ecs:us-east-1:123:task/t1"

        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3:
            mock_ecs = MagicMock()

            def boto3_client_side_effect(service):
                if service == "ecs":
                    return mock_ecs
                return MagicMock()

            mock_boto3.side_effect = boto3_client_side_effect

            mock_ecs.list_container_instances.return_value = {
                "containerInstanceArns": [ci_arn]
            }
            mock_ecs.describe_container_instances.return_value = {
                "containerInstances": [
                    {
                        "containerInstanceArn": ci_arn,
                        "ec2InstanceId": test_inst,
                    }
                ]
            }
            mock_ecs.list_tasks.return_value = {"taskArns": [task_arn]}
            mock_ecs.describe_tasks.return_value = {
                "tasks": [
                    {
                        "taskDefinitionArn": (
                            "arn:aws:ecs:us-east-1:123:"
                            "task-definition/optinist-premium"
                        ),
                        "lastStatus": ECSTaskStatus.RUNNING,
                        "desiredStatus": ECSTaskStatus.RUNNING,
                    }
                ]
            }

            from premium_cleanup import check_instance_readiness

            result = check_instance_readiness(test_inst)
            assert result is True

    def test_no_tasks(self, mock_env_vars_premium):
        """No tasks on container returns False."""
        test_inst = "i-notasks123"
        ci_arn = "arn:aws:ecs:us-east-1:123:container-instance/ci2"

        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3:
            mock_ecs = MagicMock()

            def boto3_client_side_effect(service):
                if service == "ecs":
                    return mock_ecs
                return MagicMock()

            mock_boto3.side_effect = boto3_client_side_effect

            mock_ecs.list_container_instances.return_value = {
                "containerInstanceArns": [ci_arn]
            }
            mock_ecs.describe_container_instances.return_value = {
                "containerInstances": [
                    {
                        "containerInstanceArn": ci_arn,
                        "ec2InstanceId": test_inst,
                    }
                ]
            }
            mock_ecs.list_tasks.return_value = {"taskArns": []}

            from premium_cleanup import check_instance_readiness

            result = check_instance_readiness(test_inst)
            assert result is False

    def test_no_container(self, mock_env_vars_premium):
        """No container instance returns False."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3:
            mock_ecs = MagicMock()

            def boto3_client_side_effect(service):
                if service == "ecs":
                    return mock_ecs
                return MagicMock()

            mock_boto3.side_effect = boto3_client_side_effect
            mock_ecs.list_container_instances.return_value = {
                "containerInstanceArns": []
            }

            from premium_cleanup import check_instance_readiness

            result = check_instance_readiness("i-nocontainer")
            assert result is False


class TestCleanupStaleAssignments:
    """cleanup_stale_assignments tests."""

    def test_no_stale_assignments(self, mock_env_vars_premium):
        """No stale assignments returns 0 cleaned."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "pymysql.connect"
        ) as mock_pymysql, patch("boto3.client"):
            mock_connection = setup_db_mock(
                fetchall_values=[[]],
            )
            mock_pymysql.return_value = mock_connection

            from premium_cleanup import cleanup_stale_assignments

            result = cleanup_stale_assignments()
            assert result["cleaned_assignments"] == 0

    def test_deletes_alb_and_db(self, mock_env_vars_premium):
        """Stale assignment triggers ALB + DB cleanup."""
        rule_arn = "arn:aws:rule/stale-rule"
        tg_arn = "arn:aws:tg/stale-tg"

        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3, patch("pymysql.connect") as mock_pymysql:
            mock_connection = setup_db_mock(
                fetchall_values=[
                    [
                        MockRow(
                            {
                                "user_id": 999,
                                "instance_id": "i-stale1",
                                "target_group_arn": tg_arn,
                                "alb_rule_arn": rule_arn,
                                "last_activity": "2025-01-01",
                            }
                        )
                    ],
                ],
            )
            mock_pymysql.return_value = mock_connection

            mock_elbv2 = MagicMock()

            def boto3_client_side_effect(service):
                if service == "elbv2":
                    return mock_elbv2
                return MagicMock()

            mock_boto3.side_effect = boto3_client_side_effect

            from premium_cleanup import cleanup_stale_assignments

            result = cleanup_stale_assignments()

            assert result["cleaned_assignments"] == 1
            mock_elbv2.delete_rule.assert_called_once_with(RuleArn=rule_arn)
            mock_elbv2.delete_target_group.assert_called_once_with(
                TargetGroupArn=tg_arn
            )

    def test_skips_autoscaling_tg(self, mock_env_vars_premium):
        """Autoscaling TG not deleted on stale cleanup."""
        rule_arn = "arn:aws:rule/stale-asg-rule"
        asg_tg_arn = mock_env_vars_premium["AUTOSCALING_TARGET_GROUP_ARN"]

        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3, patch("pymysql.connect") as mock_pymysql:
            mock_connection = setup_db_mock(
                fetchall_values=[
                    [
                        MockRow(
                            {
                                "user_id": 888,
                                "instance_id": "i-asg1",
                                "target_group_arn": asg_tg_arn,
                                "alb_rule_arn": rule_arn,
                                "last_activity": "2025-01-01",
                            }
                        )
                    ],
                ],
            )
            mock_pymysql.return_value = mock_connection

            mock_elbv2 = MagicMock()

            def boto3_client_side_effect(service):
                if service == "elbv2":
                    return mock_elbv2
                return MagicMock()

            mock_boto3.side_effect = boto3_client_side_effect

            from premium_cleanup import cleanup_stale_assignments

            result = cleanup_stale_assignments()

            assert result["cleaned_assignments"] == 1
            mock_elbv2.delete_rule.assert_called_once_with(RuleArn=rule_arn)
            assert not mock_elbv2.delete_target_group.called


class TestCleanupOrphanedAlbResources:
    """cleanup_orphaned_alb_resources tests."""

    def test_no_orphans(self, mock_env_vars_premium):
        """All ALB rules match DB, no orphans."""
        valid_rule_arn = "arn:aws:rule/valid-in-db"

        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3, patch("pymysql.connect") as mock_pymysql:
            mock_elbv2 = MagicMock()

            def boto3_client_side_effect(service):
                if service == "elbv2":
                    return mock_elbv2
                return MagicMock()

            mock_boto3.side_effect = boto3_client_side_effect

            mock_elbv2.describe_rules.return_value = {
                "Rules": [
                    {
                        "RuleArn": valid_rule_arn,
                        "Priority": "100",
                        "Conditions": [
                            {
                                "Field": "http-header",
                                "HttpHeaderConfig": {
                                    "HttpHeaderName": (RoutingHeaders.ROUTING_ID),
                                    "Values": ["rid-123"],
                                },
                            },
                            {
                                "Field": "http-header",
                                "HttpHeaderConfig": {
                                    "HttpHeaderName": (RoutingHeaders.USER_TIER),
                                    "Values": ["premium"],
                                },
                            },
                        ],
                        "Actions": [
                            {
                                "Type": "forward",
                                "TargetGroupArn": ("arn:aws:tg/v1"),
                            }
                        ],
                    }
                ]
            }

            mock_connection = setup_db_mock(
                fetchall_values=[
                    [
                        MockRow(
                            {
                                "alb_rule_arn": valid_rule_arn,
                                "target_group_arn": ("arn:aws:tg/v1"),
                                "user_id": 100,
                            }
                        )
                    ],
                ],
            )
            mock_pymysql.return_value = mock_connection

            from premium_cleanup import cleanup_orphaned_alb_resources

            result = cleanup_orphaned_alb_resources()

            assert result["orphaned_rules_deleted"] == 0
            assert not mock_elbv2.delete_rule.called

    def test_deletes_orphan(self, mock_env_vars_premium):
        """Orphaned ALB rule (not in DB) deleted."""
        orphan_arn = "arn:aws:rule/orphan-no-db"

        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3, patch("pymysql.connect") as mock_pymysql:
            mock_elbv2 = MagicMock()

            def boto3_client_side_effect(service):
                if service == "elbv2":
                    return mock_elbv2
                return MagicMock()

            mock_boto3.side_effect = boto3_client_side_effect

            mock_elbv2.describe_rules.return_value = {
                "Rules": [
                    {
                        "RuleArn": orphan_arn,
                        "Priority": "200",
                        "Conditions": [
                            {
                                "Field": "http-header",
                                "HttpHeaderConfig": {
                                    "HttpHeaderName": (RoutingHeaders.ROUTING_ID),
                                    "Values": ["rid-orphan"],
                                },
                            },
                            {
                                "Field": "http-header",
                                "HttpHeaderConfig": {
                                    "HttpHeaderName": (RoutingHeaders.USER_TIER),
                                    "Values": ["premium"],
                                },
                            },
                        ],
                        "Actions": [
                            {
                                "Type": "forward",
                                "TargetGroupArn": ("arn:aws:tg/orph"),
                            }
                        ],
                    }
                ]
            }

            mock_connection = setup_db_mock(
                fetchall_values=[[]],
            )
            mock_pymysql.return_value = mock_connection

            from premium_cleanup import cleanup_orphaned_alb_resources

            result = cleanup_orphaned_alb_resources()

            assert result["orphaned_rules_deleted"] == 1
            mock_elbv2.delete_rule.assert_called_once_with(RuleArn=orphan_arn)

    def test_skips_default_rule(self, mock_env_vars_premium):
        """Default ALB rule is never deleted."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "boto3.client"
        ) as mock_boto3, patch("pymysql.connect") as mock_pymysql:
            mock_elbv2 = MagicMock()

            def boto3_client_side_effect(service):
                if service == "elbv2":
                    return mock_elbv2
                return MagicMock()

            mock_boto3.side_effect = boto3_client_side_effect

            mock_elbv2.describe_rules.return_value = {
                "Rules": [
                    {
                        "RuleArn": "arn:aws:rule/default",
                        "Priority": "default",
                        "Conditions": [],
                        "Actions": [
                            {
                                "Type": "forward",
                                "TargetGroupArn": ("arn:aws:tg/dflt"),
                            }
                        ],
                    }
                ]
            }

            mock_connection = setup_db_mock(
                fetchall_values=[[]],
            )
            mock_pymysql.return_value = mock_connection

            from premium_cleanup import cleanup_orphaned_alb_resources

            result = cleanup_orphaned_alb_resources()

            assert result["orphaned_rules_deleted"] == 0
            assert not mock_elbv2.delete_rule.called


class TestReconcileInstanceStates:
    """reconcile_instance_states tests."""

    def test_cleans_terminated_instance(self, mock_env_vars_premium):
        """Terminated instance assignment cleaned."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "premium_cleanup" ".get_all_premium_instances_with_states"
        ) as mock_aws, patch("boto3.client") as mock_boto3, patch(
            "pymysql.connect"
        ) as mock_pymysql:
            mock_aws.return_value = []

            mock_elbv2 = MagicMock()

            def boto3_client_side_effect(service):
                if service == "elbv2":
                    return mock_elbv2
                return MagicMock()

            mock_boto3.side_effect = boto3_client_side_effect

            mock_connection = setup_db_mock(
                fetchall_values=[
                    [
                        MockRow(
                            {
                                "id": 1,
                                "user_id": 100,
                                "instance_id": "i-gone",
                                "instance_state": "running",
                                "status": "active",
                                "target_group_arn": "standby",
                                "alb_rule_arn": "standby",
                            }
                        )
                    ],
                ],
            )
            mock_pymysql.return_value = mock_connection

            from premium_cleanup import reconcile_instance_states

            result = reconcile_instance_states()
            assert result["cleanup_count"] == 1

    def test_updates_state_mismatch(self, mock_env_vars_premium):
        """DB state updated when AWS state differs."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "premium_cleanup" ".get_all_premium_instances_with_states"
        ) as mock_aws, patch("boto3.client") as mock_boto3, patch(
            "pymysql.connect"
        ) as mock_pymysql:
            mock_aws.return_value = [
                {
                    "instance_id": "i-1",
                    "instance_type": "t3.large",
                    "state": "stopped",
                    "launch_time": None,
                }
            ]

            mock_elbv2 = MagicMock()

            def boto3_client_side_effect(service):
                if service == "elbv2":
                    return mock_elbv2
                return MagicMock()

            mock_boto3.side_effect = boto3_client_side_effect

            mock_connection = setup_db_mock(
                fetchall_values=[
                    [
                        MockRow(
                            {
                                "id": 2,
                                "user_id": 200,
                                "instance_id": "i-1",
                                "instance_state": "running",
                                "status": "active",
                                "target_group_arn": ("arn:aws:tg/u200"),
                                "alb_rule_arn": ("arn:aws:rule/u200"),
                            }
                        )
                    ],
                ],
            )
            mock_pymysql.return_value = mock_connection

            from premium_cleanup import reconcile_instance_states

            result = reconcile_instance_states()
            assert result["update_count"] == 1

    def test_skips_autoscaling_pool(self, mock_env_vars_premium):
        """autoscaling-pool rows are skipped."""
        with patch.dict("os.environ", mock_env_vars_premium), patch(
            "premium_cleanup" ".get_all_premium_instances_with_states"
        ) as mock_aws, patch("boto3.client") as mock_boto3, patch(
            "pymysql.connect"
        ) as mock_pymysql:
            mock_aws.return_value = []

            mock_elbv2 = MagicMock()

            def boto3_client_side_effect(service):
                if service == "elbv2":
                    return mock_elbv2
                return MagicMock()

            mock_boto3.side_effect = boto3_client_side_effect

            mock_connection = setup_db_mock(
                fetchall_values=[
                    [
                        MockRow(
                            {
                                "id": 3,
                                "user_id": 300,
                                "instance_id": (PremiumAssignment.AUTOSCALING_POOL),
                                "instance_state": "running",
                                "status": "active",
                                "target_group_arn": ("arn:aws:tg/asg"),
                                "alb_rule_arn": ("arn:aws:rule/asg"),
                            }
                        )
                    ],
                ],
            )
            mock_pymysql.return_value = mock_connection

            from premium_cleanup import reconcile_instance_states

            result = reconcile_instance_states()
            assert result["cleanup_count"] == 0
            assert result["update_count"] == 0
