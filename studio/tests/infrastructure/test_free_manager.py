"""Tests for free_manager Lambda function."""

import itertools
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
    def _run(mock_env_vars_free, users, desired, minimum, maximum, ecs_desired=None):
        """
        Drive scale_and_rebalance against mocked AWS clients, returning both.

        The clients are the mock boundary, not get_service_info: mocking that
        would hide whether the bounds are actually read off the group, which is
        the behaviour under test.
        """
        from free_manager import scale_and_rebalance

        ecs_desired = desired if ecs_desired is None else ecs_desired
        ready = [f"i-{n}" for n in range(max(maximum, desired))]

        # The scale-up branch polls on a wall-clock loop bounded by
        # max_wait_time. Advance time.time() per call so a clamp regression
        # that over-asks exits the loop instead of busy-waiting for 17 minutes.
        clock = itertools.count(0.0, 120.0)

        with patch.dict("os.environ", mock_env_vars_free), patch(
            "free_manager.autoscaling_client"
        ) as mock_asg, patch("free_manager.ecs_client") as mock_ecs, patch(
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
        ), patch(
            "time.time", side_effect=lambda: next(clock)
        ):
            mock_asg.describe_auto_scaling_groups.return_value = {
                "AutoScalingGroups": [
                    {
                        "DesiredCapacity": desired,
                        "MinSize": minimum,
                        "MaxSize": maximum,
                        "Instances": [],
                    }
                ]
            }
            mock_ecs.describe_services.return_value = {
                "services": [
                    {
                        "desiredCount": ecs_desired,
                        "runningCount": desired,
                        "pendingCount": 0,
                    }
                ]
            }
            mock_ecs.list_tasks.return_value = {"taskArns": ready}
            scale_and_rebalance(active_user_count=users)
            return mock_asg, mock_ecs

    def test_floor_is_the_asg_minimum_on_scale_down(self, mock_env_vars_free):
        """users=5 alone wants 1; the minimum of 3 is requested instead."""
        asg, _ = self._run(mock_env_vars_free, users=5, desired=5, minimum=3, maximum=5)

        asg.set_desired_capacity.assert_called_once()
        assert asg.set_desired_capacity.call_args[1]["DesiredCapacity"] == 3

    def test_no_call_when_the_minimum_already_holds_capacity(self, mock_env_vars_free):
        """
        The old-bug shape: users=5, min=max=desired=3. The old floor of 1 made
        this a scale-down to 1 that the group rejects; the clamped target
        equals current desired, so nothing is requested at all.
        """
        asg, _ = self._run(mock_env_vars_free, users=5, desired=3, minimum=3, maximum=3)

        asg.set_desired_capacity.assert_not_called()

    def test_ceiling_is_the_asg_maximum_on_scale_up(self, mock_env_vars_free):
        """users=50 alone wants 10; the maximum of 3 is requested instead."""
        asg, _ = self._run(
            mock_env_vars_free, users=50, desired=1, minimum=1, maximum=3
        )

        asg.set_desired_capacity.assert_called_once()
        assert asg.set_desired_capacity.call_args[1]["DesiredCapacity"] == 3

    def test_scale_down_hysteresis_is_retained(self, mock_env_vars_free):
        """A gap of 1 is left alone, as before: 6 users want 2, desired is 3."""
        asg, _ = self._run(mock_env_vars_free, users=6, desired=3, minimum=1, maximum=5)

        asg.set_desired_capacity.assert_not_called()

    def test_scale_down_lowers_the_asg_but_leaves_ecs_to_the_terminate_event(
        self, mock_env_vars_free
    ):
        """
        7 users want 2 against a desired of 4, so the gap of 2 clears
        hysteresis. The ASG is lowered; ECS is **not**, because the instance is
        still deregistering and ECS would pick a task to stop by AZ balance,
        possibly the survivor's.
        """
        asg, ecs = self._run(
            mock_env_vars_free, users=7, desired=4, minimum=1, maximum=10
        )

        assert asg.set_desired_capacity.call_args[1]["DesiredCapacity"] == 2
        ecs.update_service.assert_not_called()

    def test_scale_up_does_raise_ecs(self, mock_env_vars_free):
        """Raising is safe and still happens inline, unlike lowering."""
        _, ecs = self._run(
            mock_env_vars_free, users=50, desired=1, minimum=1, maximum=3
        )

        assert ecs.update_service.call_args[1]["desiredCount"] == 3


class TestScheduledRunResyncsEcs:
    """
    The ASG-event sync fires once per event and is not retried, so the
    scheduled run repeats it as a backstop. It must run before the user
    threshold is considered: a task-less instance needs correcting whether or
    not there is anyone to scale for.
    """

    @staticmethod
    def _run(mock_env_vars_free, active_users, asg_desired, ecs_desired):
        from free_manager import handle_scheduled_monitoring

        with patch.dict("os.environ", mock_env_vars_free), patch(
            "free_manager.autoscaling_client"
        ) as mock_asg, patch("free_manager.ecs_client") as mock_ecs, patch(
            "free_manager.cloudwatch_client"
        ), patch(
            "free_manager.count_active_free_users", return_value=active_users
        ), patch(
            "free_manager.publish_active_user_metric"
        ), patch(
            "free_manager.scale_and_rebalance", return_value={"scaling_action": "none"}
        ):
            mock_asg.describe_auto_scaling_groups.return_value = {
                "AutoScalingGroups": [{"DesiredCapacity": asg_desired}]
            }
            mock_ecs.describe_services.return_value = {
                "services": [{"desiredCount": ecs_desired}]
            }

            result = handle_scheduled_monitoring({"source": "aws.events"}, MagicMock())
            return mock_ecs, result

    def test_resyncs_below_the_user_threshold(self, mock_env_vars_free):
        """No active users, ASG 3 and ECS 1: still corrected."""
        ecs, result = self._run(
            mock_env_vars_free, active_users=0, asg_desired=3, ecs_desired=1
        )

        ecs.update_service.assert_called_once_with(
            cluster="test-cluster",
            service="subscr-optinist-cloud-service",
            desiredCount=3,
        )
        assert json.loads(result["body"])["ecs_resynced"] is True

    def test_does_not_lower_ecs(self, mock_env_vars_free):
        """
        ASG 1 against ECS 2 is left alone. Lowering here would land inside the
        target group's deregistration window, where ECS picks which task to
        stop by AZ balance -- possibly the survivor's, leaving no healthy
        target. The terminate event lowers it instead.
        """
        ecs, result = self._run(
            mock_env_vars_free, active_users=0, asg_desired=1, ecs_desired=2
        )

        ecs.update_service.assert_not_called()
        assert json.loads(result["body"])["ecs_resynced"] is False

    def test_a_resync_failure_does_not_abort_the_run(self, mock_env_vars_free):
        """
        The count, the metric and the scaling decision still happen, and the
        failure still reaches the Errors metric by being re-raised at the end.
        """
        from free_manager import handle_scheduled_monitoring

        with patch.dict("os.environ", mock_env_vars_free), patch(
            "free_manager.autoscaling_client"
        ) as mock_asg, patch("free_manager.ecs_client"), patch(
            "free_manager.cloudwatch_client"
        ), patch(
            "free_manager.count_active_free_users", return_value=0
        ), patch(
            "free_manager.publish_active_user_metric"
        ) as mock_metric:
            mock_asg.describe_auto_scaling_groups.side_effect = Exception("throttled")

            with pytest.raises(Exception, match="throttled"):
                handle_scheduled_monitoring({"source": "aws.events"}, MagicMock())

        mock_metric.assert_called_once_with(0)

    def test_no_call_when_already_in_sync(self, mock_env_vars_free):
        """Matching counts leave the service alone."""
        ecs, result = self._run(
            mock_env_vars_free, active_users=0, asg_desired=2, ecs_desired=2
        )

        ecs.update_service.assert_not_called()
        assert json.loads(result["body"])["ecs_resynced"] is False


class TestScheduledRunBelowThreshold:
    """
    Current specification, pinned so that changing it is deliberate: the
    scaling path is not entered below FREE_USER_THRESHOLD, so the Lambda
    neither grows nor shrinks the group there. Shrinking back to baseline
    after a quiet period is the load alarms' job today.
    """

    def test_scale_and_rebalance_is_not_called(self, mock_env_vars_free):
        """4 active users against a threshold of 5: no scaling decision."""
        from free_manager import handle_scheduled_monitoring

        with patch.dict("os.environ", mock_env_vars_free), patch(
            "free_manager.autoscaling_client"
        ) as mock_asg, patch("free_manager.ecs_client") as mock_ecs, patch(
            "free_manager.cloudwatch_client"
        ), patch(
            "free_manager.count_active_free_users", return_value=4
        ), patch(
            "free_manager.publish_active_user_metric"
        ), patch(
            "free_manager.scale_and_rebalance"
        ) as mock_scale:
            mock_asg.describe_auto_scaling_groups.return_value = {
                "AutoScalingGroups": [{"DesiredCapacity": 3}]
            }
            mock_ecs.describe_services.return_value = {
                "services": [{"desiredCount": 3}]
            }

            result = handle_scheduled_monitoring({"source": "aws.events"}, MagicMock())

        mock_scale.assert_not_called()
        assert json.loads(result["body"])["status"] == "no_action_needed"


class TestDatabaseFailureReachesTheAlarm:
    """
    A database failure must not look like an idle tier. Returning 0 would
    scale nothing, report success and publish ActiveLogins = 0, hiding the
    outage behind a plausible-looking metric.
    """

    def test_count_active_free_users_raises_rather_than_returning_zero(self):
        """
        The real function, with the database unreachable. Mocking
        count_active_free_users here would test nothing: the defect being
        guarded against lives inside it.
        """
        from free_user_utils import count_active_free_users

        with patch(
            "free_user_utils.get_db_connection",
            side_effect=Exception("Can't connect to MySQL server"),
        ):
            with pytest.raises(Exception, match="MySQL"):
                count_active_free_users(activity_threshold_minutes=5)

    def test_db_error_reaches_the_handler_and_publishes_no_metric(
        self, mock_env_vars_free
    ):
        """
        End to end over the real count path: the error leaves the handler, and
        no ActiveLogins datapoint is written.
        """
        from free_manager import handler

        with patch.dict("os.environ", mock_env_vars_free), patch(
            "free_manager.autoscaling_client"
        ) as mock_asg, patch("free_manager.ecs_client") as mock_ecs, patch(
            "free_manager.cloudwatch_client"
        ) as mock_cw, patch(
            "free_user_utils.get_db_connection",
            side_effect=Exception("Can't connect to MySQL server"),
        ):
            mock_asg.describe_auto_scaling_groups.return_value = {
                "AutoScalingGroups": [{"DesiredCapacity": 1}]
            }
            mock_ecs.describe_services.return_value = {
                "services": [{"desiredCount": 1}]
            }

            with pytest.raises(Exception, match="MySQL"):
                handler({"source": "aws.events"}, MagicMock())

        mock_cw.put_metric_data.assert_not_called()


class TestHandlerFailsTheInvocation:
    """
    The free-manager-errors alarm watches AWS/Lambda Errors, which counts only
    invocations ending in an unhandled exception. A handler that returned an
    error body instead would leave the alarm permanently blind, so the
    re-raise is the alarm's precondition and is pinned here.
    """

    def test_scheduled_failure_raises_out_of_handler(self, mock_env_vars_free):
        """
        A rejected SetDesiredCapacity reaches the platform as a failure. The
        AWS client is the mock boundary, so handle_scheduled_monitoring's own
        except block is exercised rather than replaced.
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
                "AutoScalingGroups": [
                    {
                        "DesiredCapacity": 5,
                        "MinSize": 3,
                        "MaxSize": 5,
                        "Instances": [],
                    }
                ]
            }
            mock_ecs.describe_services.return_value = {
                "services": [{"desiredCount": 5, "runningCount": 5, "pendingCount": 0}]
            }
            mock_ecs.list_tasks.return_value = {"taskArns": []}
            mock_asg.set_desired_capacity.side_effect = Exception(
                "ValidationError: desired capacity below MinSize"
            )

            with pytest.raises(Exception, match="ValidationError"):
                handler({"source": "aws.events"}, MagicMock())

    def test_asg_event_failure_raises_out_of_handler(self, mock_env_vars_free):
        """
        The ASG-event path fails the invocation too, driven through the real
        handle_asg_event rather than a replacement for it.
        """
        from free_manager import handler

        event = {
            "source": "aws.autoscaling",
            "detail-type": "EC2 Instance Launch Successful",
            "detail": {"AutoScalingGroupName": "test-free-asg"},
        }

        with patch.dict("os.environ", mock_env_vars_free), patch(
            "free_manager.ecs_client"
        ), patch("free_manager.autoscaling_client") as mock_asg, patch(
            "free_manager.cloudwatch_client"
        ), patch(
            "free_manager.ec2_client"
        ):
            mock_asg.describe_auto_scaling_groups.side_effect = Exception("boom")

            with pytest.raises(Exception, match="boom"):
                handler(event, MagicMock())

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
