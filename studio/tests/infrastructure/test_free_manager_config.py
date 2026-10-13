"""Configuration regression tests for the Free Manager's infrastructure.

The Lambda owns the free ASG's capacity in both directions by user count:
free_manager.tf must grant what scale-in needs, and monitoring.tf must not
declare a second writer that removes capacity.
"""

import re

import pytest
from terraform_source import TERRAFORM_DIR, attribute, read, resource_block

FREE_MANAGER_TF = TERRAFORM_DIR / "free_manager.tf"
MONITORING_TF = TERRAFORM_DIR / "monitoring.tf"
COMPUTE_TF = TERRAFORM_DIR / "compute.tf"


class TestOnlyTheFreeManagerRemovesCapacity:
    """No second writer lowers the free ASG. The load alarms may still add an
    instance; the former cpu-low / memory-low alarms and the scale-down
    policy they drove must stay gone, or the group would again pick an
    instance to terminate blind to running workflows."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.monitoring = read(MONITORING_TF)
        self.compute = read(COMPUTE_TF)

    def test_no_scale_down_policy(self):
        policies = re.findall(
            r'resource\s+"aws_autoscaling_policy"\s+"([^"]+)"', self.compute
        )
        assert policies == ["scale_up"]

    def test_no_low_alarms_on_the_free_service(self):
        alarms = re.findall(r'alarm_name\s*=\s*"([^"]+)"', self.monitoring)
        assert not [a for a in alarms if a.endswith(("-cpu-low", "-memory-low"))]
        assert "scale_down" not in self.monitoring

    @pytest.mark.parametrize("alarm", ["cpu_high", "memory_high"])
    def test_high_alarms_still_add_capacity(self, alarm):
        block = resource_block(self.monitoring, "aws_cloudwatch_metric_alarm", alarm)
        assert (
            attribute(block, "alarm_actions") == "[aws_autoscaling_policy.scale_up.arn]"
        )

    def test_the_group_publishes_its_desired_capacity(self):
        """surplus_of_one_has_persisted reads GroupDesiredCapacity; without
        it in enabled_metrics a surplus of one would silently never go."""
        asg = resource_block(self.compute, "aws_autoscaling_group", "main")
        metrics = re.search(r"enabled_metrics\s*=\s*\[(.*?)\]", asg, re.DOTALL)
        assert metrics, "enabled_metrics not found"
        assert '"GroupDesiredCapacity"' in metrics.group(1)

    def test_dashboard_plots_the_target_against_the_group(self):
        """TargetInstances next to GroupDesiredCapacity is the only place
        the two can be compared; without it a missed scale-in is invisible."""
        title = self.monitoring.find('"Autoscaling: Free Tier Capacity Management"')
        assert title > 0, "capacity widget not found"
        widget = self.monitoring[self.monitoring.rfind("metrics = [", 0, title) : title]
        assert '"TargetInstances"' in widget
        assert '"GroupDesiredCapacity"' in widget


class TestFreeManagerLambda:
    @pytest.fixture(autouse=True)
    def _load(self):
        tf = read(FREE_MANAGER_TF)
        self.function = resource_block(tf, "aws_lambda_function", "free_manager")
        self.policy = resource_block(
            tf, "aws_iam_role_policy", "free_manager_lambda_policy"
        )

    def test_scale_in_may_terminate_the_instance_it_chose(self):
        """Scale-in picks the instance and terminates it with the desired
        capacity decremented in the same call. Without this action every
        scale-in would fail and the group would never shrink."""
        asg_statement = re.search(
            r'\{\s*Effect\s*=\s*"Allow"\s*Action\s*=\s*\[([^\]]*)\]\s*'
            r"Resource\s*=\s*aws_autoscaling_group\.main\.arn",
            self.policy,
            re.DOTALL,
        )
        assert asg_statement, "no statement scoped to the free ASG"
        actions = re.findall(r'"([^"]+)"', asg_statement.group(1))
        assert "autoscaling:TerminateInstanceInAutoScalingGroup" in actions
        assert "autoscaling:SetDesiredCapacity" in actions

    def test_scale_in_may_read_the_load_alarms(self):
        """No scale-in while cpu-high / memory-high is in ALARM: the Lambda
        needs their names and the right to read their state."""
        assert "cloudwatch:DescribeAlarms" in self.policy
        names = attribute(self.function, "LOAD_ALARM_NAMES")
        assert names.startswith("join(")
        for alarm in ("cpu_high", "memory_high"):
            assert f"aws_cloudwatch_metric_alarm.{alarm}.alarm_name" in self.function

    def test_the_schedule_delivers_the_event_with_its_fire_time(self):
        """The Lambda stamps TargetInstances and bounds the surplus history
        with the scheduled event's own time. A fixed target input would
        replace the event and drop it."""
        tf = read(FREE_MANAGER_TF)
        target = resource_block(
            tf, "aws_cloudwatch_event_target", "free_manager_target"
        )
        assert "input" not in target.replace("input_", "")

    def test_no_user_threshold_gates_the_scaling_decision(self):
        """The target is computed on every run at any user count; a threshold
        would leave the group unable to return to its minimum."""
        assert "FREE_USER_THRESHOLD" not in self.function

    def test_idle_threshold_is_still_configured(self):
        assert attribute(self.function, "FREE_IDLE_THRESHOLD_MINUTES") == '"5"'
