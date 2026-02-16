"""Tests for free_manager Lambda function."""

import json
import os
from unittest.mock import MagicMock, patch

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
            assert call_kwargs["Namespace"] == "OptiNiSt/FreeUsers"
            metric = call_kwargs["MetricData"][0]
            assert metric["MetricName"] == "ActiveLogins"
            assert metric["Value"] == 42
