"""The root-disk alarm reads the series the CloudWatch agent rolls up."""

import json
import re

from terraform_source import (
    PROJECT_ROOT,
    TERRAFORM_DIR,
    attribute,
    read,
    resource_block,
)

USER_DATA = PROJECT_ROOT / "infrastructure" / "scripts" / "ecs-user-data.sh"
MONITORING_TF = TERRAFORM_DIR / "monitoring.tf"


def agent_metrics():
    match = re.search(r"<< 'CW_CONFIG'\n(.*?)\nCW_CONFIG\n", read(USER_DATA), re.DOTALL)
    assert match, "CloudWatch agent heredoc not found"
    return json.loads(match.group(1))["metrics"]


def alarm():
    return resource_block(
        read(MONITORING_TF), "aws_cloudwatch_metric_alarm", "root_disk_used_high"
    )


class TestRootDiskAlarmMatchesAgent:
    def test_agent_tags_and_rolls_up_by_asg(self):
        metrics = agent_metrics()
        assert "AutoScalingGroupName" in metrics["append_dimensions"]
        assert ["AutoScalingGroupName"] in metrics["aggregation_dimensions"]

    def test_agent_collects_only_the_root_filesystem(self):
        disk = agent_metrics()["metrics_collected"]["disk"]
        assert "used_percent" in disk["measurement"]
        assert disk["resources"] == ["/"]

    def test_alarm_reads_the_agent_metric(self):
        block = alarm()
        assert attribute(block, "namespace") == f'"{agent_metrics()["namespace"]}"'
        assert attribute(block, "metric_name") == '"disk_used_percent"'

    def test_alarm_dimensions_are_exactly_the_rollup(self):
        dims = re.search(
            r"^\s*dimensions\s*=\s*\{(.*?)\}", alarm(), re.DOTALL | re.MULTILINE
        )
        assert dims, "dimensions block not found"
        keys = re.findall(r"^\s*(\w+)\s*=", dims.group(1), re.MULTILINE)
        assert keys == ["AutoScalingGroupName"]
