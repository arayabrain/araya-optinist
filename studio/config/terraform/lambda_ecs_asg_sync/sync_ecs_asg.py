"""
Lambda function to sync ECS service desired count with ASG capacity (1:1 ratio)

This function is triggered by ASG scaling events and ensures that the ECS service
desired count matches the number of instances in the Auto Scaling Group.
"""

import json
import os
from typing import Any, Dict

import boto3

ecs_client = boto3.client("ecs")
asg_client = boto3.client("autoscaling")

CLUSTER_NAME = os.environ["ECS_CLUSTER_NAME"]
SERVICE_NAME = os.environ["ECS_SERVICE_NAME"]
ASG_NAME = os.environ["ASG_NAME"]


def get_asg_desired_capacity() -> int:
    """Get the desired capacity of the Auto Scaling Group"""
    response = asg_client.describe_auto_scaling_groups(AutoScalingGroupNames=[ASG_NAME])

    if not response["AutoScalingGroups"]:
        raise ValueError(f"ASG {ASG_NAME} not found")

    return response["AutoScalingGroups"][0]["DesiredCapacity"]


def get_ecs_desired_count() -> int:
    """Get the current desired count of the ECS service"""
    response = ecs_client.describe_services(
        cluster=CLUSTER_NAME, services=[SERVICE_NAME]
    )

    if not response["services"]:
        raise ValueError(
            f"ECS service {SERVICE_NAME} not found in cluster {CLUSTER_NAME}"
        )

    return response["services"][0]["desiredCount"]


def update_ecs_desired_count(desired_count: int) -> Dict[str, Any]:
    """Update the ECS service desired count"""
    response = ecs_client.update_service(
        cluster=CLUSTER_NAME, service=SERVICE_NAME, desiredCount=desired_count
    )

    return response["service"]


def lambda_handler(event, context):
    """
    Main Lambda handler

    Triggered by:
    1. ASG lifecycle events (scale up/down)
    2. CloudWatch Events (ASG state changes)
    """

    print(f"Event: {json.dumps(event)}")

    try:
        # Get current ASG desired capacity
        asg_desired = get_asg_desired_capacity()
        print(f"ASG desired capacity: {asg_desired}")

        # Get current ECS desired count
        ecs_desired = get_ecs_desired_count()
        print(f"ECS current desired count: {ecs_desired}")

        # Sync if they don't match
        if asg_desired != ecs_desired:
            print(f"Syncing ECS desired count from {ecs_desired} to {asg_desired}")

            updated_service = update_ecs_desired_count(asg_desired)

            print(f"Successfully updated ECS service desired count to {asg_desired}")
            print(f"Service status: {updated_service['status']}")

            return {
                "statusCode": 200,
                "body": json.dumps(
                    {
                        "message": "ECS service scaled successfully",
                        "asg_desired": asg_desired,
                        "ecs_previous": ecs_desired,
                        "ecs_new": asg_desired,
                    }
                ),
            }
        else:
            print(f"ECS and ASG already in sync at {asg_desired} tasks/instances")

            return {
                "statusCode": 200,
                "body": json.dumps(
                    {"message": "Already in sync", "capacity": asg_desired}
                ),
            }

    except Exception as e:
        print(f"Error syncing ECS with ASG: {str(e)}")
        raise
