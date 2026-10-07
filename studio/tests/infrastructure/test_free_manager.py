"""Tests for free_manager Lambda function."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

# free_manager creates boto3 clients at module level;
# AWS_DEFAULT_REGION must be set before import to avoid NoRegionError
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")


class TestFreeManagerHandler:
    """Handler routing tests."""

    def test_handler_routes_scheduled_event(self, mock_env_vars_free):
        """Scheduled event routes to handle_scheduled_monitoring."""
        event = {
            "source": "aws.events",
            "detail-type": "Scheduled Event",
        }
        mock_context = MagicMock()

        with patch.dict("os.environ", mock_env_vars_free), patch(
            "free_manager.ecs_client"
        ), patch("free_manager.autoscaling_client"), patch(
            "free_manager.cloudwatch_client"
        ), patch(
            "free_manager.ec2_client"
        ), patch(
            "free_manager.handle_scheduled_monitoring"
        ) as mock_scheduled:
            mock_scheduled.return_value = {
                "statusCode": 200,
                "body": json.dumps({"status": "ok"}),
            }

            from free_manager import handler

            handler(event, mock_context)
            mock_scheduled.assert_called_once_with(event, mock_context)

    def test_handler_routes_asg_event(self, mock_env_vars_free):
        """ASG event routes to handle_asg_event."""
        event = {
            "source": "aws.autoscaling",
            "detail-type": "EC2 Instance Launch Successful",
            "detail": {
                "AutoScalingGroupName": "test-free-asg",
            },
        }
        mock_context = MagicMock()

        with patch.dict("os.environ", mock_env_vars_free), patch(
            "free_manager.ecs_client"
        ), patch("free_manager.autoscaling_client"), patch(
            "free_manager.cloudwatch_client"
        ), patch(
            "free_manager.ec2_client"
        ), patch(
            "free_manager.handle_asg_event"
        ) as mock_asg:
            mock_asg.return_value = {
                "statusCode": 200,
                "body": json.dumps({"status": "ok"}),
            }

            from free_manager import handler

            handler(event, mock_context)
            mock_asg.assert_called_once_with(event, mock_context)


class TestHandleAsgEvent:
    """ASG event handler tests."""

    def test_ignores_wrong_asg(self, mock_env_vars_free):
        """ASG name mismatch returns 200 with 'Event ignored'."""
        event = {
            "source": "aws.autoscaling",
            "detail-type": "EC2 Instance Launch Successful",
            "detail": {
                "AutoScalingGroupName": "other-asg",
            },
        }
        mock_context = MagicMock()

        with patch.dict("os.environ", mock_env_vars_free), patch(
            "free_manager.ecs_client"
        ), patch("free_manager.autoscaling_client"), patch(
            "free_manager.cloudwatch_client"
        ), patch(
            "free_manager.ec2_client"
        ):
            from free_manager import handle_asg_event

            result = handle_asg_event(event, mock_context)

            assert result["statusCode"] == 200
            body = json.loads(result["body"])
            assert "ignored" in body["message"].lower()

    def test_syncs_ecs(self, mock_env_vars_free):
        """ASG desired != ECS desired triggers update_service."""
        event = {
            "source": "aws.autoscaling",
            "detail-type": "EC2 Instance Launch Successful",
            "detail": {
                "AutoScalingGroupName": "test-free-asg",
            },
        }
        mock_context = MagicMock()

        with patch.dict("os.environ", mock_env_vars_free), patch(
            "free_manager.ecs_client"
        ) as mock_ecs, patch("free_manager.autoscaling_client") as mock_asg, patch(
            "free_manager.cloudwatch_client"
        ), patch(
            "free_manager.ec2_client"
        ):
            mock_asg.describe_auto_scaling_groups.return_value = {
                "AutoScalingGroups": [{"DesiredCapacity": 3}]
            }
            mock_ecs.describe_services.return_value = {
                "services": [{"desiredCount": 1}]
            }

            from free_manager import handle_asg_event

            result = handle_asg_event(event, mock_context)

            assert result["statusCode"] == 200
            mock_ecs.update_service.assert_called_once_with(
                cluster="test-cluster",
                service="subscr-optinist-cloud-service",
                desiredCount=3,
            )

    def test_already_synced(self, mock_env_vars_free):
        """ASG == ECS desired, no update call."""
        event = {
            "source": "aws.autoscaling",
            "detail-type": "EC2 Instance Launch Successful",
            "detail": {
                "AutoScalingGroupName": "test-free-asg",
            },
        }
        mock_context = MagicMock()

        with patch.dict("os.environ", mock_env_vars_free), patch(
            "free_manager.ecs_client"
        ) as mock_ecs, patch("free_manager.autoscaling_client") as mock_asg, patch(
            "free_manager.cloudwatch_client"
        ), patch(
            "free_manager.ec2_client"
        ):
            mock_asg.describe_auto_scaling_groups.return_value = {
                "AutoScalingGroups": [{"DesiredCapacity": 2}]
            }
            mock_ecs.describe_services.return_value = {
                "services": [{"desiredCount": 2}]
            }

            from free_manager import handle_asg_event

            result = handle_asg_event(event, mock_context)

            assert result["statusCode"] == 200
            body = json.loads(result["body"])
            assert "sync" in body["message"].lower()
            mock_ecs.update_service.assert_not_called()


class TestCalculateDesiredInstances:
    """
    Pure function tests for calculate_desired_instances.

    The target must always land inside the ASG's own bounds. SetDesiredCapacity
    rejects anything outside them, and a rejection aborts the invocation, so
    that cycle's rebalancing and metric publication are skipped too.
    """

    @pytest.mark.parametrize(
        "active_users,asg_min,asg_max,expected",
        [
            # One instance per 5 users, rounded up, between the bounds.
            (0, 1, 10, 1),
            (1, 1, 10, 1),
            (5, 1, 10, 1),
            (6, 1, 10, 2),
            (10, 1, 10, 2),
            (11, 1, 10, 3),
            (50, 1, 10, 10),
            # Floor is the configured minimum, not a literal 1: a user count
            # that alone would ask for fewer instances still cannot go below
            # what the group is configured to keep running.
            (5, 3, 5, 3),
            (5, 5, 5, 5),
            (10, 5, 5, 5),
            (15, 5, 5, 5),
            (6, 3, 5, 3),
            (16, 3, 5, 4),
            # Ceiling is the configured maximum: a user count that alone would
            # ask for more instances is capped rather than requested.
            (16, 1, 3, 3),
            (100, 1, 3, 3),
            # min == max pins the count regardless of load.
            (1, 2, 2, 2),
            (99, 2, 2, 2),
            # A minimum of 0 is valid on an ASG and must not be raised to 1.
            (0, 0, 4, 0),
            (1, 0, 4, 1),
        ],
    )
    def test_target_is_within_asg_bounds(
        self, active_users, asg_min, asg_max, expected
    ):
        """Target matches the formula and never leaves [MinSize, MaxSize]."""
        from free_manager import calculate_desired_instances

        result = calculate_desired_instances(active_users, asg_min, asg_max)

        assert result == expected
        assert asg_min <= result <= asg_max

    def test_get_service_info_returns_the_asg_bounds(self, mock_env_vars_free):
        """get_service_info surfaces MinSize/MaxSize for the caller to clamp to."""
        from free_manager import get_service_info

        asg = {
            "AutoScalingGroups": [
                {
                    "DesiredCapacity": 3,
                    "MinSize": 3,
                    "MaxSize": 5,
                    "Instances": [],
                }
            ]
        }
        service = {
            "services": [{"desiredCount": 3, "runningCount": 3, "pendingCount": 0}]
        }

        with patch.dict("os.environ", mock_env_vars_free), patch(
            "free_manager.autoscaling_client"
        ) as mock_asg, patch("free_manager.ecs_client") as mock_ecs:
            mock_asg.describe_auto_scaling_groups.return_value = asg
            mock_ecs.describe_services.return_value = service
            mock_ecs.list_tasks.return_value = {"taskArns": []}

            info = get_service_info("test-cluster", "test-service")

        assert info["min_size"] == 3
        assert info["max_size"] == 5


class TestScaleAndRebalanceUsesAsgBounds:
    """
    scale_and_rebalance's wiring: the bounds it reads from get_service_info
    must be the ones it asks SetDesiredCapacity for. A regression that kept a
    literal floor in scale_and_rebalance would still satisfy the pure-function
    tests above, so these drive the real entry point.
    """

    @staticmethod
    def _run(mock_env_vars_free, users, desired, minimum, maximum):
        """Drive scale_and_rebalance, returning the autoscaling client mock."""
        from free_manager import scale_and_rebalance

        # The scale-up branch polls for readiness on a wall-clock loop. Report
        # every instance ready and the distribution balanced so it breaks on
        # the first attempt instead of waiting out max_wait_time.
        ready = [f"i-{n}" for n in range(maximum)]

        with patch.dict("os.environ", mock_env_vars_free), patch(
            "free_manager.autoscaling_client"
        ) as mock_asg, patch("free_manager.ecs_client") as mock_ecs, patch(
            "free_manager.get_service_info"
        ) as mock_info, patch(
            "free_manager.is_scaling_in_progress", return_value=False
        ), patch(
            "free_manager.set_scaling_lock"
        ), patch(
            "free_manager.get_available_instance_ids", return_value=ready
        ), patch(
            "free_manager.get_users_per_instance", return_value={}
        ), patch(
            "free_manager.rebalance_idle_users_multi", return_value=[]
        ), patch(
            "free_manager.is_distribution_balanced", return_value=True
        ), patch(
            "time.sleep"
        ):
            mock_info.return_value = {
                "desired_count": desired,
                "running_count": desired,
                "pending_count": 0,
                "min_size": minimum,
                "max_size": maximum,
            }
            mock_ecs.list_tasks.return_value = {"taskArns": ready}
            scale_and_rebalance(active_user_count=users)
            return mock_asg

    def test_floor_is_the_asg_minimum_on_scale_down(self, mock_env_vars_free):
        """users=5 alone wants 1; the minimum of 3 is requested instead."""
        asg = self._run(mock_env_vars_free, users=5, desired=5, minimum=3, maximum=5)

        asg.set_desired_capacity.assert_called_once()
        assert asg.set_desired_capacity.call_args[1]["DesiredCapacity"] == 3

    def test_no_call_when_the_minimum_already_holds_capacity(self, mock_env_vars_free):
        """
        The old-bug shape: users=5, min=max=desired=3. The old floor of 1 made
        this a scale-down to 1 that the group rejects; the clamped target
        equals current desired, so nothing is requested at all.
        """
        asg = self._run(mock_env_vars_free, users=5, desired=3, minimum=3, maximum=3)

        asg.set_desired_capacity.assert_not_called()

    def test_ceiling_is_the_asg_maximum_on_scale_up(self, mock_env_vars_free):
        """users=50 alone wants 10; the maximum of 3 is requested instead."""
        asg = self._run(mock_env_vars_free, users=50, desired=1, minimum=1, maximum=3)

        asg.set_desired_capacity.assert_called_once()
        assert asg.set_desired_capacity.call_args[1]["DesiredCapacity"] == 3

    def test_scale_down_hysteresis_is_retained(self, mock_env_vars_free):
        """A gap of 1 is left alone, as before: 6 users want 2, desired is 3."""
        asg = self._run(mock_env_vars_free, users=6, desired=3, minimum=1, maximum=5)

        asg.set_desired_capacity.assert_not_called()


class TestHandlerFailsTheInvocation:
    """
    The free-manager-errors alarm watches AWS/Lambda Errors, which counts only
    invocations ending in an unhandled exception. A handler that returned an
    error body instead would leave the alarm permanently blind, so the
    re-raise is the alarm's precondition and is pinned here.
    """

    def test_scheduled_failure_raises_out_of_handler(self, mock_env_vars_free):
        """A rejected SetDesiredCapacity reaches the platform as a failure."""
        from free_manager import handler

        with patch.dict("os.environ", mock_env_vars_free), patch(
            "free_manager.ecs_client"
        ), patch("free_manager.autoscaling_client"), patch(
            "free_manager.cloudwatch_client"
        ), patch(
            "free_manager.ec2_client"
        ), patch(
            "free_manager.handle_scheduled_monitoring",
            side_effect=Exception("ValidationError: desired capacity below MinSize"),
        ):
            with pytest.raises(Exception, match="ValidationError"):
                handler({"source": "aws.events"}, MagicMock())

    def test_asg_event_failure_raises_out_of_handler(self, mock_env_vars_free):
        """The ASG-event path fails the invocation too."""
        from free_manager import handler

        with patch.dict("os.environ", mock_env_vars_free), patch(
            "free_manager.ecs_client"
        ), patch("free_manager.autoscaling_client"), patch(
            "free_manager.cloudwatch_client"
        ), patch(
            "free_manager.ec2_client"
        ), patch(
            "free_manager.handle_asg_event", side_effect=Exception("boom")
        ):
            with pytest.raises(Exception, match="boom"):
                handler({"source": "aws.autoscaling"}, MagicMock())

    def test_missing_asg_raises(self, mock_env_vars_free):
        """
        Manual Test 4's induction: ASG_NAME pointing at a missing group must
        reach the metric, not be swallowed into a 200/500 body.
        """
        from free_manager import handler

        with patch.dict("os.environ", mock_env_vars_free), patch(
            "free_manager.ecs_client"
        ) as mock_ecs, patch("free_manager.autoscaling_client") as mock_asg, patch(
            "free_manager.cloudwatch_client"
        ), patch(
            "free_manager.ec2_client"
        ), patch(
            "free_manager.count_active_free_users", return_value=5
        ), patch(
            "free_manager.publish_active_user_metric"
        ), patch(
            "free_manager.is_scaling_in_progress", return_value=False
        ), patch(
            "free_manager.set_scaling_lock"
        ):
            mock_asg.describe_auto_scaling_groups.return_value = {
                "AutoScalingGroups": []
            }
            mock_ecs.list_tasks.return_value = {"taskArns": []}

            with pytest.raises(Exception, match="not found"):
                handler({"source": "aws.events"}, MagicMock())


class TestIsDistributionBalanced:
    """Pure function tests for is_distribution_balanced."""

    def test_balanced(self):
        """Balanced distribution returns True."""
        from free_user_utils import is_distribution_balanced

        dist = {"i-1": 5, "i-2": 5, "i-3": 4}
        assert is_distribution_balanced(dist, tolerance=1)

    def test_imbalanced(self):
        """Imbalanced distribution returns False."""
        from free_user_utils import is_distribution_balanced

        dist = {"i-1": 10, "i-2": 2, "i-3": 1}
        assert not is_distribution_balanced(dist, tolerance=1)

    def test_empty(self):
        """Empty dict returns True."""
        from free_user_utils import is_distribution_balanced

        assert is_distribution_balanced({})


class TestPublishActiveUserMetric:
    """Test CloudWatch metric publishing."""

    def test_publishes_metric(self, mock_env_vars_free):
        """Calls cloudwatch_client.put_metric_data."""
        env_prefix = mock_env_vars_free["ENV_PREFIX"]
        with patch.dict("os.environ", mock_env_vars_free), patch(
            "free_manager.ecs_client"
        ), patch("free_manager.autoscaling_client"), patch(
            "free_manager.cloudwatch_client"
        ) as mock_cw, patch(
            "free_manager.ec2_client"
        ):
            from free_manager import publish_active_user_metric

            publish_active_user_metric(42)

            mock_cw.put_metric_data.assert_called_once()
            call_kwargs = mock_cw.put_metric_data.call_args[1]
            assert call_kwargs["Namespace"] == f"OptiNiSt/FreeUsers/{env_prefix}"
            metric = call_kwargs["MetricData"][0]
            assert metric["MetricName"] == "ActiveLogins"
            assert metric["Value"] == 42
