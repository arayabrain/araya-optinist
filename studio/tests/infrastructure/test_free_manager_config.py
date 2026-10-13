"""Configuration regression tests for the Free Manager's infrastructure.

The Lambda owns the free ASG's capacity in both directions by user count:
free_manager.tf must grant what scale-in needs, and monitoring.tf must not
declare a second writer that removes capacity.
"""

import re

import pytest
from terraform_source import TERRAFORM_DIR, attribute, read, resource_block

FREE_MANAGER_TF = TERRAFORM_DIR / "free_manager.tf"


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

    def test_no_user_threshold_gates_the_scaling_decision(self):
        """The target is computed on every run at any user count; a threshold
        would leave the group unable to return to its minimum."""
        assert "FREE_USER_THRESHOLD" not in self.function

    def test_idle_threshold_is_still_configured(self):
        assert attribute(self.function, "FREE_IDLE_THRESHOLD_MINUTES") == '"5"'
