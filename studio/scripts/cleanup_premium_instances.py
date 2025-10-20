#!/usr/bin/env python3
"""
Premium Instance Cleanup Script

This script cleans up orphaned premium instances and database entries
that are not part of the current active premium instances.
"""

import os
import sys
from pathlib import Path
from typing import Dict, List

import boto3
import pymysql

# Add the project root to the Python path to allow for absolute imports
# The project root is 2 parent directories up from this script's directory.
project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))


from studio.app.common.core.logger import AppLogger  # noqa: E402

logger = AppLogger.get_logger()


def get_db_connection():
    """Create database connection using environment variables"""
    try:
        connection = pymysql.connect(
            host=os.environ.get("RDS_HOST", "localhost").split(":")[0],
            port=3306,
            user=os.environ.get("RDS_USER", "root"),
            password=os.environ.get("RDS_PASSWORD", ""),
            database=os.environ.get("RDS_DATABASE", "optinist"),
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
        )
        return connection
    except Exception as e:
        logger.error(f"Failed to connect to database: {e}")
        return None


def get_active_premium_instances() -> List[str]:
    """Get instance IDs from premium instances (running/stopped)"""
    ec2 = boto3.client("ec2")
    try:
        # Get all premium instances
        response = ec2.describe_instances(
            Filters=[
                {"Name": "tag:Service", "Values": ["optinist-premium"]},
                {
                    "Name": "instance-state-name",
                    "Values": ["pending", "running", "stopping", "stopped"],
                },
            ]
        )

        instance_ids = []
        for reservation in response["Reservations"]:
            for instance in reservation["Instances"]:
                instance_ids.append(instance["InstanceId"])

        return instance_ids

    except Exception as e:
        logger.error(f"Failed to get active premium instances: {e}")
        return []


def get_all_premium_instances() -> List[Dict]:
    """Get all premium instances from AWS"""
    ec2 = boto3.client("ec2")
    try:
        response = ec2.describe_instances(
            Filters=[
                {"Name": "tag:Name", "Values": ["*premium*"]},
                {
                    "Name": "instance-state-name",
                    "Values": [
                        "pending",
                        "running",
                        "stopping",
                        "stopped",
                        "terminated",
                    ],
                },
            ]
        )

        instances = []
        for reservation in response["Reservations"]:
            for instance in reservation["Instances"]:
                instances.append(
                    {
                        "instance_id": instance["InstanceId"],
                        "instance_type": instance["InstanceType"],
                        "state": instance["State"]["Name"],
                        "launch_time": instance.get("LaunchTime"),
                    }
                )

        return instances

    except Exception as e:
        logger.error(f"Failed to get premium instances: {e}")
        return []


def cleanup_orphaned_instances(dry_run: bool = True) -> Dict:
    """
    Clean up orphaned premium instances that are not in the active premium instances

    Args:
        dry_run: If True, only report what would be cleaned up
    """
    ec2 = boto3.client("ec2")
    elbv2 = boto3.client("elbv2")

    # Get current state
    active_premium_instances = set(get_active_premium_instances())
    all_premium_instances = get_all_premium_instances()

    # Find orphaned instances (not in active premium instances and not terminated)
    orphaned_instances = [
        instance
        for instance in all_premium_instances
        if (
            instance["instance_id"] not in active_premium_instances
            and instance["state"] not in ["terminated", "terminating"]
        )
    ]

    cleanup_results = {
        "total_premium_instances": len(all_premium_instances),
        "active_premium_instances": len(active_premium_instances),
        "orphaned_instances": len(orphaned_instances),
        "terminated_instances": 0,
        "cleaned_target_groups": 0,
        "cleaned_db_entries": 0,
        "dry_run": dry_run,
    }

    logger.info(f"Found {len(orphaned_instances)} orphaned premium instances")

    # Clean up orphaned instances
    for instance in orphaned_instances:
        instance_id = instance["instance_id"]
        logger.info(
            f"Processing orphaned instance: {instance_id} (state: {instance['state']})"
        )

        if not dry_run:
            try:
                # Terminate the instance
                if instance["state"] in ["pending", "running", "stopping", "stopped"]:
                    ec2.terminate_instances(InstanceIds=[instance_id])
                    cleanup_results["terminated_instances"] += 1
                    logger.info(f"Terminated orphaned instance: {instance_id}")

            except Exception as e:
                logger.error(f"Failed to terminate instance {instance_id}: {e}")

    # Clean up orphaned target groups
    try:
        response = elbv2.describe_target_groups()
        premium_target_groups = [
            tg for tg in response["TargetGroups"] if "premium-" in tg["TargetGroupName"]
        ]

        for tg in premium_target_groups:
            tg_arn = tg["TargetGroupArn"]
            tg_name = tg["TargetGroupName"]

            # Check if target group has any healthy targets
            try:
                health_response = elbv2.describe_target_health(TargetGroupArn=tg_arn)
                targets = health_response["TargetHealthDescriptions"]

                # If no targets or all targets are from orphaned instances
                target_instance_ids = {target["Target"]["Id"] for target in targets}
                orphaned_target_instances = (
                    target_instance_ids - active_premium_instances
                )

                if (
                    target_instance_ids
                    and target_instance_ids == orphaned_target_instances
                ):
                    logger.info(f"Found orphaned target group: {tg_name}")

                    if not dry_run:
                        # Get associated rules and delete them
                        try:
                            elbv2.delete_target_group(TargetGroupArn=tg_arn)
                            cleanup_results["cleaned_target_groups"] += 1
                            logger.info(f"Deleted orphaned target group: {tg_name}")
                        except Exception as e:
                            logger.error(
                                f"Failed to delete target group {tg_name}: {e}"
                            )

            except Exception as e:
                logger.error(f"Error processing target group {tg_name}: {e}")

    except Exception as e:
        logger.error(f"Failed to process target groups: {e}")

    # Clean up database entries for terminated instances
    try:
        connection = get_db_connection()
        if connection:
            with connection.cursor() as cursor:
                # Get all assignments
                cursor.execute(
                    "SELECT user_id, instance_id FROM premium_user_assignments "
                    "WHERE status = 'active'"
                )
                db_assignments = cursor.fetchall()

                # Check which instances no longer exist
                all_instance_ids = {
                    instance["instance_id"] for instance in all_premium_instances
                }

                for assignment in db_assignments:
                    user_id = assignment["user_id"]
                    instance_id = assignment["instance_id"]

                    # If instance doesn't exist in AWS, clean up database
                    if instance_id not in all_instance_ids:
                        logger.info(
                            f"Cleaning up database entry for terminated instance "
                            f"{instance_id} (user {user_id})"
                        )

                        if not dry_run:
                            cursor.execute(
                                "DELETE FROM premium_user_assignments "
                                "WHERE user_id = %s",
                                (user_id,),
                            )
                            cleanup_results["cleaned_db_entries"] += 1

            connection.close()

    except Exception as e:
        logger.error(f"Failed to clean up database entries: {e}")

    return cleanup_results


def main():
    """Main cleanup function"""
    import argparse

    parser = argparse.ArgumentParser(description="Clean up orphaned premium instances")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be cleaned up without making changes",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually perform the cleanup (opposite of dry-run)",
    )

    args = parser.parse_args()

    # Default to dry-run unless explicitly told to execute
    dry_run = not args.execute

    if dry_run:
        logger.info("Running in DRY-RUN mode. No changes will be made.")
    else:
        logger.warning("Running in EXECUTE mode. Changes will be made!")
        confirm = input("Are you sure you want to proceed? (yes/no): ")
        if confirm.lower() != "yes":
            logger.info("Cleanup cancelled by user")
            return

    # Perform cleanup
    results = cleanup_orphaned_instances(dry_run=dry_run)

    # Print results
    logger.info("Cleanup Results:")
    logger.info(f"  Total premium instances: {results['total_premium_instances']}")
    logger.info(f"  Active premium instances: {results['active_premium_instances']}")
    logger.info(f"  Orphaned instances: {results['orphaned_instances']}")
    logger.info(f"  Terminated instances: {results['terminated_instances']}")
    logger.info(f"  Cleaned target groups: {results['cleaned_target_groups']}")
    logger.info(f"  Cleaned DB entries: {results['cleaned_db_entries']}")

    if dry_run and results["orphaned_instances"] > 0:
        logger.info("\nTo actually perform cleanup, run with --execute flag")


if __name__ == "__main__":
    main()
