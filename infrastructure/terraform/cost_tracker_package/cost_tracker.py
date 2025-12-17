"""
Cost Tracker Lambda Function

Tracks resource usage and costs for premium and free tier instances.
Triggered by CloudWatch Events on a schedule.
"""

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict

import boto3

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Cost tracking Lambda function handler

    Args:
        event: CloudWatch event data
        context: Lambda context

    Returns:
        Response with tracking results
    """
    logger.info("Cost tracking Lambda invoked")
    logger.info(f"Event: {json.dumps(event)}")

    try:
        # Get environment variables
        spot_fleet_id = os.environ.get("SPOT_FLEET_ID")
        asg_name = os.environ.get("ASG_NAME")
        region = os.environ.get("REGION", "ap-northeast-1")

        logger.info(
            f"Tracking costs for: Spot Fleet {spot_fleet_id}, "
            f"ASG {asg_name}, Region {region}"
        )

        # Initialize AWS clients
        ec2_client = boto3.client("ec2", region_name=region)
        cloudwatch_client = boto3.client("cloudwatch", region_name=region)

        # Track premium instances (spot fleet)
        premium_metrics = track_premium_instances(
            ec2_client, cloudwatch_client, spot_fleet_id
        )

        # Track free tier instances (auto scaling group)
        free_metrics = track_free_instances(ec2_client, cloudwatch_client, asg_name)

        # Calculate utilization metrics
        utilization = calculate_premium_utilization(premium_metrics, free_metrics)

        # Publish metrics to CloudWatch
        publish_cost_metrics(
            cloudwatch_client, premium_metrics, free_metrics, utilization
        )

        logger.info("Cost tracking completed successfully")

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": "Cost tracking completed",
                    "premium_metrics": premium_metrics,
                    "free_metrics": free_metrics,
                    "utilization": utilization,
                    "timestamp": datetime.utcnow().isoformat(),
                }
            ),
        }

    except Exception as e:
        logger.error(f"Error in cost tracking: {e}")
        return {
            "statusCode": 500,
            "body": json.dumps(
                {
                    "message": f"Cost tracking failed: {str(e)}",
                    "timestamp": datetime.utcnow().isoformat(),
                }
            ),
        }


def track_premium_instances(
    ec2_client, cloudwatch_client, spot_fleet_id: str
) -> Dict[str, Any]:
    """Track premium instance metrics"""
    try:
        if not spot_fleet_id:
            logger.warning("No spot fleet ID provided")
            return {"instance_count": 0, "running_instances": 0}

        # Get spot fleet instances
        response = ec2_client.describe_spot_fleet_instances(
            SpotFleetRequestId=spot_fleet_id
        )
        instances = response.get("ActiveInstances", [])

        running_instances = len(
            [i for i in instances if i.get("InstanceHealth") == "healthy"]
        )

        logger.info(
            f"Premium instances - Total: {len(instances)}, Running: {running_instances}"
        )

        return {
            "instance_count": len(instances),
            "running_instances": running_instances,
            "spot_fleet_id": spot_fleet_id,
        }

    except Exception as e:
        logger.warning(f"Error tracking premium instances: {e}")
        return {"instance_count": 0, "running_instances": 0}


def track_free_instances(
    ec2_client, cloudwatch_client, asg_name: str
) -> Dict[str, Any]:
    """Track free tier instance metrics"""
    try:
        if not asg_name:
            logger.warning("No ASG name provided")
            return {"instance_count": 0, "running_instances": 0}

        # Get ASG instances
        asg_client = boto3.client("autoscaling")
        response = asg_client.describe_auto_scaling_groups(
            AutoScalingGroupNames=[asg_name]
        )

        if not response["AutoScalingGroups"]:
            logger.warning(f"ASG {asg_name} not found")
            return {"instance_count": 0, "running_instances": 0}

        asg = response["AutoScalingGroups"][0]
        instances = asg.get("Instances", [])
        running_instances = len(
            [i for i in instances if i.get("LifecycleState") == "InService"]
        )

        logger.info(
            f"Free tier instances - Total: {len(instances)}, "
            f"Running: {running_instances}"
        )

        return {
            "instance_count": len(instances),
            "running_instances": running_instances,
            "asg_name": asg_name,
        }

    except Exception as e:
        logger.warning(f"Error tracking free instances: {e}")
        return {"instance_count": 0, "running_instances": 0}


def calculate_premium_utilization(
    premium_metrics: Dict[str, Any], free_metrics: Dict[str, Any]
) -> int:
    """Calculate premium utilization percentage"""
    try:
        total_instances = premium_metrics.get(
            "running_instances", 0
        ) + free_metrics.get("running_instances", 0)

        if total_instances == 0:
            return 0

        # For now, return a simple calculation based on active instances
        # In a real implementation, you'd check actual user assignments
        return min(100, (total_instances * 80))  # Assume 80% utilization per instance

    except Exception as e:
        logger.warning(f"Error calculating premium utilization: {e}")
        return 0


def publish_cost_metrics(
    cloudwatch_client,
    premium_metrics: Dict[str, Any],
    free_metrics: Dict[str, Any],
    utilization: int,
):
    """Publish metrics to CloudWatch"""
    try:
        namespace = "Optinist/CostTracking"

        metrics = [
            {
                "MetricName": "PremiumInstanceCount",
                "Value": premium_metrics.get("running_instances", 0),
                "Unit": "Count",
            },
            {
                "MetricName": "FreeInstanceCount",
                "Value": free_metrics.get("running_instances", 0),
                "Unit": "Count",
            },
            {
                "MetricName": "PremiumUtilization",
                "Value": utilization,
                "Unit": "Percent",
            },
        ]

        for metric in metrics:
            cloudwatch_client.put_metric_data(
                Namespace=namespace,
                MetricData=[
                    {
                        "MetricName": metric["MetricName"],
                        "Value": metric["Value"],
                        "Unit": metric["Unit"],
                        "Timestamp": datetime.utcnow(),
                    }
                ],
            )

        logger.info(f"Published {len(metrics)} metrics to CloudWatch")

    except Exception as e:
        logger.error(f"Error publishing metrics: {e}")
