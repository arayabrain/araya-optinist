"""Tests for free_manager Lambda function."""

import itertools
import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call, patch

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
    def _run(
        mock_env_vars_free,
        users,
        desired,
        minimum,
        maximum,
        ecs_desired=None,
        context=None,
        ready=None,
        persisted=False,
        alarm=False,
        extra_instances=(),
    ):
        """
        Drive scale_and_rebalance against mocked AWS clients, returning both.

        The clients are the mock boundary, not get_service_info: mocking that
        would hide whether the bounds are actually read off the group, which is
        the behaviour under test. The group reports ``desired`` InService
        instances, i-0 upwards, all in one zone and with no workflow, so a
        scale-in has as many safe candidates as it needs. The surplus history
        and the load alarms are stubbed (``persisted``, ``alarm``); their
        own reads are tested in TestSurplusOfOne.
        """
        from free_manager import scale_and_rebalance

        ecs_desired = desired if ecs_desired is None else ecs_desired
        if ready is None:
            ready = [f"i-{n}" for n in range(max(maximum, desired))]
        in_group = [f"i-{n}" for n in range(desired)]

        # The scale-up branch polls on a wall-clock loop bounded by
        # max_wait_time. Advance time.time() per call so a clamp regression
        # that over-asks exits the loop instead of busy-waiting for 17 minutes.
        clock = itertools.count(0.0, 120.0)

        with patch.dict("os.environ", mock_env_vars_free), patch(
            "free_manager.autoscaling_client"
        ) as mock_asg, patch("free_manager.ecs_client") as mock_ecs, patch(
            "free_manager.ec2_client"
        ) as mock_ec2, patch(
            "free_manager.cloudwatch_client"
        ), patch(
            "free_manager.is_scaling_in_progress", return_value=False
        ), patch(
            "free_manager.set_scaling_lock"
        ) as mock_lock, patch(
            "free_manager.get_available_instance_ids", return_value=ready
        ), patch(
            "free_manager.get_users_per_instance", return_value={}
        ), patch(
            "free_manager.get_workload_per_instance", return_value={}
        ), patch(
            "free_manager.surplus_of_one_has_persisted", return_value=persisted
        ), patch(
            "free_manager.load_alarm_holds_capacity", return_value=alarm
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
                        "Instances": [
                            {
                                "InstanceId": instance_id,
                                "LifecycleState": "InService",
                                "HealthStatus": "Healthy",
                                "AvailabilityZone": "ap-northeast-1a",
                                "ProtectedFromScaleIn": False,
                            }
                            for instance_id in in_group
                        ]
                        + list(extra_instances),
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
            mock_ec2.describe_instances.return_value = {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": instance_id,
                                "LaunchTime": datetime(
                                    2026, 10, 13, 0, n, tzinfo=timezone.utc
                                ),
                            }
                            for n, instance_id in enumerate(in_group)
                        ]
                    }
                ]
            }
            result = scale_and_rebalance(active_user_count=users, context=context)
            mock_asg.result = result
            mock_asg.lock = mock_lock
            return mock_asg, mock_ecs

    @staticmethod
    def _terminated(asg):
        return [
            c[1]["InstanceId"]
            for c in asg.terminate_instance_in_auto_scaling_group.call_args_list
        ]

    def test_floor_is_the_asg_minimum_on_scale_down(self, mock_env_vars_free):
        """users=5 alone wants 1; the minimum of 3 is the target instead, so
        two of the five are removed, not four."""
        asg, _ = self._run(mock_env_vars_free, users=5, desired=5, minimum=3, maximum=5)

        assert len(self._terminated(asg)) == 2
        asg.set_desired_capacity.assert_not_called()

    def test_no_call_when_the_minimum_already_holds_capacity(self, mock_env_vars_free):
        """
        The old-bug shape: users=5, min=max=desired=3. The old floor of 1 made
        this a scale-down to 1 that the group rejects; the clamped target
        equals current desired, so nothing is requested at all.
        """
        asg, _ = self._run(mock_env_vars_free, users=5, desired=3, minimum=3, maximum=3)

        asg.set_desired_capacity.assert_not_called()
        asg.terminate_instance_in_auto_scaling_group.assert_not_called()

    def test_ceiling_is_the_asg_maximum_on_scale_up(self, mock_env_vars_free):
        """users=50 alone wants 10; the maximum of 3 is requested instead."""
        asg, _ = self._run(
            mock_env_vars_free, users=50, desired=1, minimum=1, maximum=3
        )

        asg.set_desired_capacity.assert_called_once()
        assert asg.set_desired_capacity.call_args[1]["DesiredCapacity"] == 3

    def test_a_fresh_surplus_of_one_is_left_alone(self, mock_env_vars_free):
        """6 users want 2, desired is 3: the boundary case waits until it
        has persisted."""
        asg, _ = self._run(mock_env_vars_free, users=6, desired=3, minimum=1, maximum=5)

        asg.set_desired_capacity.assert_not_called()
        asg.terminate_instance_in_auto_scaling_group.assert_not_called()
        assert asg.result["scaling_action"] == "none"

    def test_a_persisted_surplus_of_one_goes(self, mock_env_vars_free):
        """The same shape three runs later: one instance is removed. This is
        the 2 -> 1 return at MinSize = 1 that no alarm makes any more."""
        asg, _ = self._run(
            mock_env_vars_free, users=0, desired=2, minimum=1, maximum=5, persisted=True
        )

        assert len(self._terminated(asg)) == 1
        assert asg.result["scaling_action"] == "scale_down"

    def test_surplus_is_measured_on_desired_capacity_not_on_instances(
        self, mock_env_vars_free
    ):
        """
        After a first termination the group still lists three instances, one
        of them Terminating:Wait, against a desired capacity of 2. The surplus
        is 1 against the target of 1, not 2: counting instances would remove
        one too many.
        """
        terminating = {
            "InstanceId": "i-going",
            "LifecycleState": "Terminating:Wait",
            "HealthStatus": "Healthy",
            "AvailabilityZone": "ap-northeast-1c",
            "ProtectedFromScaleIn": False,
        }
        asg, _ = self._run(
            mock_env_vars_free,
            users=0,
            desired=2,
            minimum=1,
            maximum=5,
            persisted=True,
            extra_instances=[terminating],
        )

        assert len(self._terminated(asg)) == 1
        assert "i-going" not in self._terminated(asg)

    def test_no_scale_in_while_a_load_alarm_is_firing(self, mock_env_vars_free):
        """A surplus of 2 with cpu-high in ALARM: the alarm says the capacity
        is needed, so nothing is removed until it clears."""
        asg, _ = self._run(
            mock_env_vars_free, users=0, desired=3, minimum=1, maximum=5, alarm=True
        )

        asg.terminate_instance_in_auto_scaling_group.assert_not_called()
        assert asg.result["scaling_action"] == "none"

    def test_zero_users_return_the_group_to_its_minimum(self, mock_env_vars_free):
        """
        The state after a busy period: nobody left, desired still 3. The
        target is MinSize and the two surplus instances go in one run, which
        the threshold gate used to prevent.
        """
        asg, _ = self._run(mock_env_vars_free, users=0, desired=3, minimum=1, maximum=5)

        assert len(self._terminated(asg)) == 2
        assert asg.result["scaling_action"] == "scale_down"

    def test_scale_down_terminates_chosen_instances_and_leaves_ecs_to_the_event(
        self, mock_env_vars_free
    ):
        """
        7 users want 2 against a desired of 4, so the gap of 2 clears
        hysteresis. Two instances are terminated with the desired capacity
        decremented in the same call -- the group never lowers the capacity
        itself and so never chooses. ECS is **not** lowered: the terminate
        event does that once each instance is gone.
        """
        asg, ecs = self._run(
            mock_env_vars_free, users=7, desired=4, minimum=1, maximum=10
        )

        assert len(self._terminated(asg)) == 2
        for c in asg.terminate_instance_in_auto_scaling_group.call_args_list:
            assert c[1]["ShouldDecrementDesiredCapacity"] is True
        asg.set_desired_capacity.assert_not_called()
        ecs.update_service.assert_not_called()

    def test_scale_in_takes_no_lock_and_scale_up_still_does(self, mock_env_vars_free):
        """The terminate requests return at once; only the scale-out wait
        holds the lock."""
        asg, _ = self._run(mock_env_vars_free, users=0, desired=3, minimum=1, maximum=5)
        asg.lock.assert_not_called()

        asg, _ = self._run(mock_env_vars_free, users=6, desired=1, minimum=1, maximum=5)
        assert asg.lock.call_args_list == [call(True), call(False)]

    def test_scale_up_wait_is_bounded_by_the_remaining_invocation_time(
        self, mock_env_vars_free
    ):
        """
        With 4 minutes left, the loop gets 3 and stops after one check (the
        mocked clock advances 2 minutes per read); the lock is cleared by this
        invocation's finally. Unbounded, the loop would outlive the function
        and the kill would skip the finally.
        """
        context = MagicMock()
        context.get_remaining_time_in_millis.return_value = 240_000

        asg, _ = self._run(
            mock_env_vars_free,
            users=6,
            desired=1,
            minimum=1,
            maximum=5,
            context=context,
            ready=["i-0"],
        )

        assert asg.result["rebalancing_attempts"] == 1
        assert asg.lock.call_args_list[-1] == call(False)

    def test_scale_up_does_raise_ecs(self, mock_env_vars_free):
        """Raising is safe and still happens inline, unlike lowering."""
        _, ecs = self._run(
            mock_env_vars_free, users=50, desired=1, minimum=1, maximum=3
        )

        assert ecs.update_service.call_args[1]["desiredCount"] == 3

    def test_target_between_asg_and_ecs_does_not_lower_ecs(self, mock_env_vars_free):
        """
        An alarm has already taken the ASG to 2 while ECS is still at 4, and
        the user count wants 3. Against the ASG that is a scale-up, so the
        branch raises the group -- but 3 is still *below* the ECS count, and
        lowering it here is the unsafe mid-deregistration operation.
        """
        asg, ecs = self._run(
            mock_env_vars_free,
            users=15,
            desired=2,
            minimum=1,
            maximum=5,
            ecs_desired=4,
        )

        assert asg.set_desired_capacity.call_args[1]["DesiredCapacity"] == 3
        ecs.update_service.assert_not_called()


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

    @pytest.mark.parametrize(
        "asg_desired,ecs_desired,expected",
        [
            # 4 -> 2 scale-in: one decrement per completed termination, so the
            # second instance is never still deregistering while ECS has to
            # choose two tasks to stop.
            (2, 4, 3),
            (2, 3, 2),
            # A single-instance scale-in converges in one event.
            (1, 2, 1),
            # Never below the ASG, however late the events arrive.
            (2, 2, None),
        ],
    )
    def test_terminate_event_lowers_ecs_by_one(
        self, mock_env_vars_free, asg_desired, ecs_desired, expected
    ):
        """The terminate event is per instance, so the decrement is too."""
        from free_manager import handle_asg_event

        event = {
            "source": "aws.autoscaling",
            "detail-type": "EC2 Instance Terminate Successful",
            "detail": {"AutoScalingGroupName": "test-free-asg"},
        }

        with patch.dict("os.environ", mock_env_vars_free), patch(
            "free_manager.autoscaling_client"
        ) as mock_asg, patch("free_manager.ecs_client") as mock_ecs:
            mock_asg.describe_auto_scaling_groups.return_value = {
                "AutoScalingGroups": [{"DesiredCapacity": asg_desired}]
            }
            mock_ecs.describe_services.return_value = {
                "services": [{"desiredCount": ecs_desired}]
            }

            handle_asg_event(event, MagicMock())

        if expected is None:
            mock_ecs.update_service.assert_not_called()
        else:
            assert mock_ecs.update_service.call_args[1]["desiredCount"] == expected

    def test_launch_event_does_not_lower_ecs(self, mock_env_vars_free):
        """Only a completed termination may lower it."""
        from free_manager import handle_asg_event

        event = {
            "source": "aws.autoscaling",
            "detail-type": "EC2 Instance Launch Successful",
            "detail": {"AutoScalingGroupName": "test-free-asg"},
        }

        with patch.dict("os.environ", mock_env_vars_free), patch(
            "free_manager.autoscaling_client"
        ) as mock_asg, patch("free_manager.ecs_client") as mock_ecs:
            mock_asg.describe_auto_scaling_groups.return_value = {
                "AutoScalingGroups": [{"DesiredCapacity": 2}]
            }
            mock_ecs.describe_services.return_value = {
                "services": [{"desiredCount": 4}]
            }

            handle_asg_event(event, MagicMock())

        mock_ecs.update_service.assert_not_called()

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


class TestScheduledRunComputesATargetAtAnyUserCount:
    """
    The user-count model owns both directions, so every scheduled run makes
    a scaling decision. The old threshold gate skipped it below 5 users,
    which left the group unable to return to its baseline after a busy
    period.
    """

    @pytest.mark.parametrize("active_users", [0, 4])
    def test_scale_and_rebalance_is_called(self, mock_env_vars_free, active_users):
        from free_manager import handle_scheduled_monitoring

        context = MagicMock()
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
        ) as mock_scale:
            mock_asg.describe_auto_scaling_groups.return_value = {
                "AutoScalingGroups": [{"DesiredCapacity": 3}]
            }
            mock_ecs.describe_services.return_value = {
                "services": [{"desiredCount": 3}]
            }

            handle_scheduled_monitoring({"source": "aws.events"}, context)

        mock_scale.assert_called_once_with(
            active_user_count=active_users, context=context
        )


class TestScaleInChoosesByWorkload:
    """
    select_instances_to_terminate, as a pure function. The order is: never
    an instance with a running workflow; then the fewest active users; then
    the zone with the most instances; then the newest.
    """

    @staticmethod
    def _inst(instance_id, az="1a", launch=0, state="InService", protected=False):
        return {
            "id": instance_id,
            "az": az,
            "launch_time": launch,
            "lifecycle_state": state,
            "protected": protected,
        }

    def test_a_running_workflow_is_never_chosen(self):
        """i-b has the fewest users but a workflow; i-a (2 users) goes
        instead, and the second slot stays unfilled rather than take i-b."""
        from free_manager import select_instances_to_terminate

        chosen = select_instances_to_terminate(
            [self._inst("i-a"), self._inst("i-b"), self._inst("i-c")],
            users_per_instance={"i-a": 2, "i-b": 0, "i-c": 3},
            workload_per_instance={"i-b": 1},
            count=2,
        )

        assert chosen == ["i-a", "i-c"]

    def test_shortfall_is_left_for_a_later_run(self):
        """Every candidate but one is busy: one id, not two."""
        from free_manager import select_instances_to_terminate

        chosen = select_instances_to_terminate(
            [self._inst("i-a"), self._inst("i-b"), self._inst("i-c")],
            users_per_instance={},
            workload_per_instance={"i-b": 2, "i-c": 1},
            count=2,
        )

        assert chosen == ["i-a"]

    def test_fewest_active_users_first(self):
        from free_manager import select_instances_to_terminate

        chosen = select_instances_to_terminate(
            [self._inst("i-a"), self._inst("i-b"), self._inst("i-c")],
            users_per_instance={"i-a": 3, "i-b": 1, "i-c": 2},
            workload_per_instance={},
            count=2,
        )

        assert chosen == ["i-b", "i-c"]

    def test_the_heavier_zone_breaks_a_tie(self):
        """Two in 1a, one in 1c, no users anywhere: removing from 1a keeps
        the zones balanced, so the group has no reason to rebalance."""
        from free_manager import select_instances_to_terminate

        chosen = select_instances_to_terminate(
            [
                self._inst("i-c1", az="1c"),
                self._inst("i-a1", az="1a"),
                self._inst("i-a2", az="1a"),
            ],
            users_per_instance={},
            workload_per_instance={},
            count=1,
        )

        assert chosen[0] in {"i-a1", "i-a2"}

    def test_two_picks_alternate_zones(self):
        """Two in each zone, remove two: one from each, never both from
        one zone, which would leave 2/0 and trigger a rebalance."""
        from free_manager import select_instances_to_terminate

        chosen = select_instances_to_terminate(
            [
                self._inst("i-a1", az="1a", launch=1),
                self._inst("i-a2", az="1a", launch=2),
                self._inst("i-c1", az="1c", launch=3),
                self._inst("i-c2", az="1c", launch=4),
            ],
            users_per_instance={},
            workload_per_instance={},
            count=2,
        )

        assert len(chosen) == 2
        assert {c[2] for c in chosen} == {"a", "c"}

    def test_zones_are_counted_over_in_service_instances_only(self):
        """A Terminating instance in 1c does not make 1c the heavier zone:
        with one InService in each, the newest (i-a, launched later) goes."""
        from free_manager import select_instances_to_terminate

        chosen = select_instances_to_terminate(
            [
                self._inst("i-a", az="1a", launch=2),
                self._inst("i-c", az="1c", launch=1),
                self._inst("i-c-going", az="1c", launch=3, state="Terminating:Wait"),
            ],
            users_per_instance={},
            workload_per_instance={},
            count=1,
        )

        assert chosen == ["i-a"]

    def test_the_newest_breaks_a_tie_within_a_zone(self):
        from free_manager import select_instances_to_terminate

        chosen = select_instances_to_terminate(
            [self._inst("i-old", launch=100), self._inst("i-new", launch=200)],
            users_per_instance={},
            workload_per_instance={},
            count=1,
        )

        assert chosen == ["i-new"]

    def test_only_in_service_unprotected_instances_are_candidates(self):
        from free_manager import select_instances_to_terminate

        chosen = select_instances_to_terminate(
            [
                self._inst("i-wait", state="Terminating:Wait"),
                self._inst("i-pending", state="Pending"),
                self._inst("i-protected", protected=True),
                self._inst("i-ok"),
            ],
            users_per_instance={},
            workload_per_instance={},
            count=3,
        )

        assert chosen == ["i-ok"]


class TestScaleInTerminates:
    """scale_in against the mocked clients: one TerminateInstanceInAutoScalingGroup
    per chosen instance, decrementing the desired capacity."""

    @staticmethod
    def _run(mock_env_vars_free, instances, users, workload, count, side_effect=None):
        from free_manager import scale_in

        with patch.dict("os.environ", mock_env_vars_free), patch(
            "free_manager.autoscaling_client"
        ) as mock_asg, patch("free_manager.ec2_client") as mock_ec2, patch(
            "free_manager.get_users_per_instance", return_value=users
        ), patch(
            "free_manager.get_workload_per_instance", side_effect=workload
        ):
            mock_ec2.describe_instances.return_value = {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": inst["id"],
                                "LaunchTime": datetime(
                                    2026, 10, 13, 0, n, tzinfo=timezone.utc
                                ),
                            }
                            for n, inst in enumerate(instances)
                        ]
                    }
                ]
            }
            if side_effect is not None:
                mock_asg.terminate_instance_in_auto_scaling_group.side_effect = (
                    side_effect
                )
            terminated = scale_in(instances, count)
            return mock_asg, terminated

    @staticmethod
    def _inst(instance_id):
        return {
            "id": instance_id,
            "az": "1a",
            "lifecycle_state": "InService",
            "protected": False,
        }

    def test_terminates_each_chosen_instance_with_a_decrement(self, mock_env_vars_free):
        asg, terminated = self._run(
            mock_env_vars_free,
            instances=[self._inst("i-a"), self._inst("i-b"), self._inst("i-c")],
            users={"i-a": 2},
            workload=lambda: {},
            count=2,
        )

        assert set(terminated) == {"i-b", "i-c"}
        assert asg.terminate_instance_in_auto_scaling_group.call_count == 2
        for c in asg.terminate_instance_in_auto_scaling_group.call_args_list:
            assert c[1]["ShouldDecrementDesiredCapacity"] is True

    def test_a_refused_termination_is_skipped_not_fatal(self, mock_env_vars_free):
        """ScalingActivityInProgress on the first: the second still goes, the
        first is reported as not terminated and waits for the next run."""
        asg, terminated = self._run(
            mock_env_vars_free,
            instances=[self._inst("i-a"), self._inst("i-b")],
            users={},
            workload=lambda: {},
            count=2,
            side_effect=[Exception("ScalingActivityInProgress"), {}],
        )

        assert len(terminated) == 1
        assert asg.terminate_instance_in_auto_scaling_group.call_count == 2

    def test_nothing_safe_means_no_call(self, mock_env_vars_free):
        asg, terminated = self._run(
            mock_env_vars_free,
            instances=[self._inst("i-a"), self._inst("i-b")],
            users={},
            workload=lambda: {"i-a": 1, "i-b": 1},
            count=1,
        )

        assert terminated == []
        asg.terminate_instance_in_auto_scaling_group.assert_not_called()

    def test_an_unreadable_workload_aborts_the_scale_in(self, mock_env_vars_free):
        """A database failure must not read as "no workflow anywhere"."""
        with pytest.raises(Exception, match="MySQL"):
            self._run(
                mock_env_vars_free,
                instances=[self._inst("i-a"), self._inst("i-b")],
                users={},
                workload=Exception("Can't connect to MySQL server"),
                count=1,
            )

    def test_an_unreadable_user_count_aborts_the_scale_in(self, mock_env_vars_free):
        """
        The users read is asked to raise rather than return {}: an empty map
        would order the candidates by zone and age alone and could remove an
        instance with idle sessions ahead of an empty one.
        """
        from free_manager import scale_in

        with patch.dict("os.environ", mock_env_vars_free), patch(
            "free_manager.autoscaling_client"
        ) as mock_asg, patch(
            "free_manager.get_users_per_instance",
            side_effect=Exception("Lost connection to MySQL server"),
        ) as mock_users, patch(
            "free_manager.get_workload_per_instance", return_value={}
        ):
            with pytest.raises(Exception, match="MySQL"):
                scale_in([self._inst("i-a"), self._inst("i-b")], 1)

        assert mock_users.call_args[1]["raise_on_error"] is True
        mock_asg.terminate_instance_in_auto_scaling_group.assert_not_called()

    def test_unreadable_launch_times_abort_the_scale_in(self, mock_env_vars_free):
        """EC2 refusing describe_instances fails the run; nothing is
        terminated on a partial picture."""
        from free_manager import scale_in

        with patch.dict("os.environ", mock_env_vars_free), patch(
            "free_manager.autoscaling_client"
        ) as mock_asg, patch("free_manager.ec2_client") as mock_ec2, patch(
            "free_manager.get_users_per_instance", return_value={}
        ), patch(
            "free_manager.get_workload_per_instance", return_value={}
        ):
            mock_ec2.describe_instances.side_effect = Exception("throttled")
            with pytest.raises(Exception, match="throttled"):
                scale_in([self._inst("i-a"), self._inst("i-b")], 1)

        mock_asg.terminate_instance_in_auto_scaling_group.assert_not_called()

    def test_the_callers_instance_list_is_not_modified(self, mock_env_vars_free):
        instances = [self._inst("i-a"), self._inst("i-b")]
        before = [dict(i) for i in instances]

        self._run(
            mock_env_vars_free,
            instances=instances,
            users={},
            workload=lambda: {},
            count=1,
        )

        assert instances == before


class TestSurplusOfOne:
    """
    surplus_of_one_has_persisted reads TargetInstances against
    GroupDesiredCapacity, period by period; load_alarm_holds_capacity reads
    the -high alarms' state and when it last changed. Both raise rather than
    guess when the read fails.
    """

    # The run is at 09:03:20, so the current period is 09:00-09:05 and the
    # three completed periods before it start at 08:55, 08:50 and 08:45.
    NOW = datetime(2026, 10, 13, 9, 3, 20, tzinfo=timezone.utc)
    PERIOD_START = datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc)

    @classmethod
    def _history(cls, mock_env_vars_free, targets, desireds, current=None):
        """
        targets/desireds: one value per completed period, newest first (08:55,
        08:50, 08:45, 08:40, ...); None = no datapoint. ``current`` adds a
        (target, desired) datapoint in the incomplete 09:00 period, as
        CloudWatch may already serve this run's own publication.
        """
        from free_manager import surplus_of_one_has_persisted

        stamps = [
            cls.PERIOD_START - timedelta(minutes=5 * (i + 1))
            for i in range(len(targets))
        ]

        def series(values, current_value):
            pairs = [(t, v) for t, v in zip(stamps, values) if v is not None]
            if current_value is not None:
                pairs.insert(0, (cls.PERIOD_START, current_value))
            return {
                "Timestamps": [t for t, _ in pairs],
                "Values": [float(v) for _, v in pairs],
            }

        current = current or (None, None)
        with patch.dict("os.environ", mock_env_vars_free), patch(
            "free_manager.cloudwatch_client"
        ) as mock_cw:
            mock_cw.get_metric_data.return_value = {
                "MetricDataResults": [
                    {"Id": "target", **series(targets, current[0])},
                    {"Id": "desired", **series(desireds, current[1])},
                ]
            }
            persisted = surplus_of_one_has_persisted(now=cls.NOW)
            return persisted, mock_cw

    def test_three_completed_periods_of_surplus_count(self, mock_env_vars_free):
        persisted, _ = self._history(
            mock_env_vars_free, targets=[1, 1, 1], desireds=[2, 2, 2]
        )
        assert persisted is True

    def test_two_periods_do_not(self, mock_env_vars_free):
        persisted, _ = self._history(
            mock_env_vars_free, targets=[1, 1], desireds=[2, 2]
        )
        assert persisted is False

    def test_the_current_period_is_not_counted(self, mock_env_vars_free):
        """Two completed periods plus this run's own datapoint: not three.
        Whether the fresh point is readable yet must not change the answer."""
        persisted, _ = self._history(
            mock_env_vars_free, targets=[1, 1], desireds=[2, 2], current=(1, 2)
        )
        assert persisted is False

    def test_a_period_without_surplus_breaks_the_run(self, mock_env_vars_free):
        """Newest first: the middle period had desired == target."""
        persisted, _ = self._history(
            mock_env_vars_free, targets=[1, 2, 1], desireds=[2, 2, 2]
        )
        assert persisted is False

    def test_only_the_latest_periods_are_read(self, mock_env_vars_free):
        """An older period without surplus does not matter once three newer
        ones have it."""
        persisted, _ = self._history(
            mock_env_vars_free, targets=[1, 1, 1, 3], desireds=[2, 2, 2, 3]
        )
        assert persisted is True

    def test_a_hole_restarts_the_wait(self, mock_env_vars_free):
        """A failed run publishes no target. Three surplus periods around a
        hole are not three consecutive ones."""
        persisted, _ = self._history(
            mock_env_vars_free, targets=[1, None, 1, 1], desireds=[2, 2, 2, 2]
        )
        assert persisted is False

    def test_the_query_covers_exactly_the_completed_periods(self, mock_env_vars_free):
        """Bounds on period multiples, so CloudWatch cuts the periods there;
        the end is the current period's start, so it is never read."""
        _, mock_cw = self._history(mock_env_vars_free, targets=[], desireds=[])
        kwargs = mock_cw.get_metric_data.call_args[1]
        assert kwargs["EndTime"] == self.PERIOD_START
        assert kwargs["StartTime"] == self.PERIOD_START - timedelta(minutes=15)

    def test_reads_the_most_demanding_target_and_the_lowest_desired(
        self, mock_env_vars_free
    ):
        _, mock_cw = self._history(mock_env_vars_free, targets=[], desireds=[])
        queries = {
            q["Id"]: q["MetricStat"]
            for q in mock_cw.get_metric_data.call_args[1]["MetricDataQueries"]
        }
        assert queries["target"]["Stat"] == "Maximum"
        assert queries["target"]["Metric"]["MetricName"] == "TargetInstances"
        assert queries["desired"]["Stat"] == "Minimum"
        assert queries["desired"]["Metric"]["Dimensions"] == [
            {"Name": "AutoScalingGroupName", "Value": "test-free-asg"}
        ]

    def test_an_unreadable_history_raises(self, mock_env_vars_free):
        from free_manager import surplus_of_one_has_persisted

        with patch.dict("os.environ", mock_env_vars_free), patch(
            "free_manager.cloudwatch_client"
        ) as mock_cw:
            mock_cw.get_metric_data.side_effect = Exception("throttled")
            with pytest.raises(Exception, match="throttled"):
                surplus_of_one_has_persisted()

    # (state, minutes since the state changed) per alarm. An alarm in ALARM
    # holds capacity whatever its age; an alarm out of ALARM holds it for
    # ALARM_QUIET_PERIOD (15 min) after the transition, so the instance it
    # added is not taken back on the first run after OK.
    @pytest.mark.parametrize(
        "states,expected",
        [
            ({"cpu-high": ("ALARM", 60), "memory-high": ("OK", 60)}, True),
            ({"cpu-high": ("ALARM", 1), "memory-high": ("OK", 60)}, True),
            ({"cpu-high": ("OK", 60), "memory-high": ("OK", 60)}, False),
            ({"cpu-high": ("OK", 5), "memory-high": ("OK", 60)}, True),
            ({"cpu-high": ("OK", 14), "memory-high": ("OK", 60)}, True),
            ({"cpu-high": ("OK", 15), "memory-high": ("OK", 60)}, False),
            ({"cpu-high": ("INSUFFICIENT_DATA", 60), "memory-high": ("OK", 60)}, False),
            ({"cpu-high": ("INSUFFICIENT_DATA", 2), "memory-high": ("OK", 60)}, True),
            ({"cpu-high": ("OK", None), "memory-high": ("OK", None)}, False),
        ],
    )
    def test_load_alarm_state_and_age(self, mock_env_vars_free, states, expected):
        from free_manager import load_alarm_holds_capacity

        now = datetime(2026, 10, 13, 9, 3, 20, tzinfo=timezone.utc)
        alarms = []
        for name, (state, minutes_ago) in states.items():
            alarm = {"AlarmName": f"test-optinist-{name}", "StateValue": state}
            if minutes_ago is not None:
                alarm["StateTransitionedTimestamp"] = now - timedelta(
                    minutes=minutes_ago
                )
            alarms.append(alarm)

        with patch.dict("os.environ", mock_env_vars_free), patch(
            "free_manager.cloudwatch_client"
        ) as mock_cw:
            mock_cw.describe_alarms.return_value = {"MetricAlarms": alarms}
            assert load_alarm_holds_capacity(now=now) is expected
            assert mock_cw.describe_alarms.call_args[1]["AlarmNames"] == [
                "test-optinist-cpu-high",
                "test-optinist-memory-high",
            ]

    def test_no_alarm_names_means_no_read(self, mock_env_vars_free):
        from free_manager import load_alarm_holds_capacity

        env = {**mock_env_vars_free, "LOAD_ALARM_NAMES": ""}
        with patch.dict("os.environ", env), patch(
            "free_manager.cloudwatch_client"
        ) as mock_cw:
            assert load_alarm_holds_capacity() is False
            mock_cw.describe_alarms.assert_not_called()

    def test_unreadable_alarms_raise(self, mock_env_vars_free):
        from free_manager import load_alarm_holds_capacity

        with patch.dict("os.environ", mock_env_vars_free), patch(
            "free_manager.cloudwatch_client"
        ) as mock_cw:
            mock_cw.describe_alarms.side_effect = Exception("AccessDenied")
            with pytest.raises(Exception, match="AccessDenied"):
                load_alarm_holds_capacity()


class TestScaleUpWaitBudget:
    """scale_up_wait_budget: the remaining time less the margin, capped at
    MAX_WAIT_TIME, and never negative."""

    @pytest.mark.parametrize(
        "remaining_ms,expected",
        [
            (900_000, 840),
            (1_500_000, 1020),
            (120_000, 60),
            (30_000, 0),
        ],
    )
    def test_budget(self, remaining_ms, expected):
        from free_manager import scale_up_wait_budget

        context = MagicMock()
        context.get_remaining_time_in_millis.return_value = remaining_ms

        assert scale_up_wait_budget(context) == expected

    def test_no_context_falls_back_to_the_constant(self):
        from free_manager import MAX_WAIT_TIME, scale_up_wait_budget

        assert scale_up_wait_budget(None) == MAX_WAIT_TIME
        assert scale_up_wait_budget({}) == MAX_WAIT_TIME


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
            "free_manager.count_active_free_users", return_value=6
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
                        "DesiredCapacity": 1,
                        "MinSize": 1,
                        "MaxSize": 3,
                        "Instances": [],
                    }
                ]
            }
            mock_ecs.describe_services.return_value = {
                "services": [{"desiredCount": 1, "runningCount": 1, "pendingCount": 0}]
            }
            mock_ecs.list_tasks.return_value = {"taskArns": []}
            mock_asg.set_desired_capacity.side_effect = Exception(
                "ValidationError: desired capacity above MaxSize"
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

    def test_publishes_the_target_next_to_active_logins(self, mock_env_vars_free):
        """Same namespace as ActiveLogins, so the two plot together against
        the group's GroupDesiredCapacity."""
        env_prefix = mock_env_vars_free["ENV_PREFIX"]
        with patch.dict("os.environ", mock_env_vars_free), patch(
            "free_manager.cloudwatch_client"
        ) as mock_cw:
            from free_manager import publish_target_metric

            publish_target_metric(3)

            call_kwargs = mock_cw.put_metric_data.call_args[1]
            assert call_kwargs["Namespace"] == f"OptiNiSt/FreeUsers/{env_prefix}"
            metric = call_kwargs["MetricData"][0]
            assert metric["MetricName"] == "TargetInstances"
            assert metric["Value"] == 3

    def test_target_is_published_even_when_the_lock_is_held(self, mock_env_vars_free):
        """A held lock skips the actions, not the measurement: the target is
        exactly what a reader needs to judge a skipped run."""
        from free_manager import scale_and_rebalance

        with patch.dict("os.environ", mock_env_vars_free), patch(
            "free_manager.autoscaling_client"
        ) as mock_asg, patch("free_manager.ecs_client") as mock_ecs, patch(
            "free_manager.is_scaling_in_progress", return_value=True
        ), patch(
            "free_manager.publish_target_metric"
        ) as mock_target:
            mock_asg.describe_auto_scaling_groups.return_value = {
                "AutoScalingGroups": [
                    {
                        "DesiredCapacity": 1,
                        "MinSize": 1,
                        "MaxSize": 3,
                        "Instances": [],
                    }
                ]
            }
            mock_ecs.describe_services.return_value = {
                "services": [{"desiredCount": 1, "runningCount": 1, "pendingCount": 0}]
            }
            mock_ecs.list_tasks.return_value = {"taskArns": []}

            result = scale_and_rebalance(active_user_count=7)

        assert result["status"] == "scaling_in_progress"
        mock_target.assert_called_once_with(2)
        mock_asg.set_desired_capacity.assert_not_called()
