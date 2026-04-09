"""Tests for agent_recovery_lambda — ASG/ECS reconciliation."""

import os
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

# Module-level boto3.client() calls in agent_recovery_lambda need these set
# at import time. boto3.client() itself doesn't require credentials, so the
# import is safe in any environment.
os.environ.setdefault("CLUSTERS", "test-cluster")
os.environ.setdefault("ASG_NAMES", "test-asg")

import agent_recovery_lambda  # noqa: E402


def _ecs_describe_response(instances):
    """Build a describe_container_instances response from {ec2_id: connected}."""
    return {
        "containerInstances": [
            {"ec2InstanceId": ec2_id, "agentConnected": connected}
            for ec2_id, connected in instances.items()
        ]
    }


def _asg_describe_response(asg_name, instance_ids, lifecycle_state="InService"):
    return {
        "AutoScalingGroups": [
            {
                "AutoScalingGroupName": asg_name,
                "Instances": [
                    {"InstanceId": iid, "LifecycleState": lifecycle_state}
                    for iid in instance_ids
                ],
            }
        ]
    }


@pytest.fixture
def mock_clients():
    """Patch the module-level boto3 clients with MagicMocks."""
    mock_ecs = MagicMock()
    mock_asg = MagicMock()
    mock_cw = MagicMock()
    # ClusterNotFoundException is read off the real client; preserve the
    # exceptions namespace so the handler's `except ecs.exceptions.X` still
    # references a real exception class.
    mock_ecs.exceptions = agent_recovery_lambda.ecs.exceptions
    with patch.object(agent_recovery_lambda, "ecs", mock_ecs), patch.object(
        agent_recovery_lambda, "asg", mock_asg
    ), patch.object(agent_recovery_lambda, "cw", mock_cw):
        yield mock_ecs, mock_asg, mock_cw


class TestHandler:
    def test_all_instances_registered(self, mock_clients):
        """Every ASG instance is a healthy ECS container instance → metric=0."""
        mock_ecs, mock_asg, mock_cw = mock_clients

        paginator = MagicMock()
        paginator.paginate.return_value = iter(
            [{"containerInstanceArns": ["arn:1", "arn:2"]}]
        )
        mock_ecs.get_paginator.return_value = paginator
        mock_ecs.describe_container_instances.return_value = _ecs_describe_response(
            {"i-aaa": True, "i-bbb": True}
        )
        mock_asg.describe_auto_scaling_groups.return_value = _asg_describe_response(
            "test-asg", ["i-aaa", "i-bbb"]
        )

        result = agent_recovery_lambda.handler({}, None)

        assert result["unregistered"] == 0
        assert result["details"] == []
        published = mock_cw.put_metric_data.call_args.kwargs["MetricData"][0]
        assert published["Value"] == 0

    def test_one_instance_missing(self, mock_clients):
        """ASG instance not in ECS → counted, named in details, metric=1."""
        mock_ecs, mock_asg, mock_cw = mock_clients

        paginator = MagicMock()
        paginator.paginate.return_value = iter([{"containerInstanceArns": ["arn:1"]}])
        mock_ecs.get_paginator.return_value = paginator
        mock_ecs.describe_container_instances.return_value = _ecs_describe_response(
            {"i-aaa": True}
        )
        mock_asg.describe_auto_scaling_groups.return_value = _asg_describe_response(
            "test-asg", ["i-aaa", "i-bbb-stranded"]
        )

        result = agent_recovery_lambda.handler({}, None)

        assert result["unregistered"] == 1
        assert any("i-bbb-stranded" in d for d in result["details"])
        published = mock_cw.put_metric_data.call_args.kwargs["MetricData"][0]
        assert published["Value"] == 1

    def test_disconnected_agents_count_as_missing(self, mock_clients):
        """Registered but agentConnected=false counts as unregistered."""
        mock_ecs, mock_asg, mock_cw = mock_clients

        paginator = MagicMock()
        paginator.paginate.return_value = iter([{"containerInstanceArns": ["arn:1"]}])
        mock_ecs.get_paginator.return_value = paginator
        mock_ecs.describe_container_instances.return_value = _ecs_describe_response(
            {"i-aaa": False}
        )
        mock_asg.describe_auto_scaling_groups.return_value = _asg_describe_response(
            "test-asg", ["i-aaa"]
        )

        result = agent_recovery_lambda.handler({}, None)

        assert result["unregistered"] == 1

    def test_pending_asg_instances_excluded(self, mock_clients):
        """ASG instances not in InService are not counted as missing."""
        mock_ecs, mock_asg, mock_cw = mock_clients

        paginator = MagicMock()
        paginator.paginate.return_value = iter([{"containerInstanceArns": []}])
        mock_ecs.get_paginator.return_value = paginator
        mock_asg.describe_auto_scaling_groups.return_value = _asg_describe_response(
            "test-asg", ["i-pending"], lifecycle_state="Pending"
        )

        result = agent_recovery_lambda.handler({}, None)

        assert result["unregistered"] == 0

    def test_describe_asg_client_error_does_not_crash(self, mock_clients):
        """Regression for the asg.exceptions.ClientError AttributeError bug.

        Boto3 service clients do not expose ClientError on `client.exceptions`;
        the handler must catch botocore.exceptions.ClientError directly.
        """
        mock_ecs, mock_asg, mock_cw = mock_clients

        paginator = MagicMock()
        paginator.paginate.return_value = iter([{"containerInstanceArns": []}])
        mock_ecs.get_paginator.return_value = paginator
        mock_asg.describe_auto_scaling_groups.side_effect = ClientError(
            {"Error": {"Code": "Throttling", "Message": "Rate exceeded"}},
            "DescribeAutoScalingGroups",
        )

        result = agent_recovery_lambda.handler({}, None)

        assert result["unregistered"] == 0
        assert result["details"] == []
        mock_cw.put_metric_data.assert_called_once()
