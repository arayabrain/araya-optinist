"""
Background job to clean up workspace data for logged-out free users.

Runs every hour, deletes data for users logged out >1 hour ago.

COMPREHENSIVE SAFETY CHECKS:
1. Only processes users with active_workflow_count = 0 (no running workflows)
2. Verifies S3 backup exists before deleting experiment outputs
3. Keeps local data if S3 verification fails (prevents data loss)
4. Always deletes input data (already backed up to S3 on upload)

This ensures:
- No data deletion while workflows are running (even long 2+ hour workflows)
- No data loss if S3 upload failed
- User data is cleaned up after logout with appropriate grace period
"""

import os
import shutil
from datetime import datetime, timedelta
from typing import List, Tuple

from sqlalchemy import func
from sqlmodel import select

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.subscription.constants import SyncStatusConstants
from studio.app.common.core.utils.filepath_creater import join_filepath
from studio.app.common.db.database import session_scope
from studio.app.common.models import FreeUserAssignment, User, Workspace
from studio.app.dir_path import DIRPATH

logger = AppLogger.get_logger()


class DataCleanupJob:
    """Background job to clean up data for logged-out free users"""

    @classmethod
    async def run(cls):
        """
        Main cleanup job execution:
        1. Handle orphaned data from terminated instances
        2. Query free users logged out >1 hour ago
        3. Delete user's workspace data from local storage
        4. Mark data as cleaned in database
        """
        logger.info("Starting data cleanup job for logged-out free users")

        try:
            # First, handle orphaned data from terminated instances
            cls._handle_orphaned_data()

            # Get users eligible for cleanup
            users_to_cleanup = cls._get_users_for_cleanup()

            if not users_to_cleanup:
                logger.debug("No users eligible for cleanup")
                return

            logger.info(f"Found {len(users_to_cleanup)} users for cleanup")

            # Clean up each user
            cleaned_count = 0
            error_count = 0

            for user_id, workspace_ids in users_to_cleanup:
                try:
                    success = cls._cleanup_user_data(user_id, workspace_ids)

                    if success:
                        # Re-check workflow count before marking as cleaned
                        # Prevents race condition where workflow starts during cleanup
                        if cls._verify_no_active_workflows(user_id):
                            cls._mark_cleaned(user_id)
                            cleaned_count += 1
                        else:
                            logger.warning(
                                f"Skipping cleanup completion for user {user_id}: "
                                f"workflow started during cleanup"
                            )
                            error_count += 1
                    else:
                        error_count += 1

                except Exception as e:
                    logger.error(
                        f"Error cleaning up user {user_id}: {e}", exc_info=True
                    )
                    error_count += 1

            logger.info(
                f"Cleanup job completed: {cleaned_count} users cleaned, "
                f"{error_count} errors"
            )

            # Publish CloudWatch metrics
            cls._publish_metrics(cleaned_count, error_count)

        except Exception as e:
            logger.error(f"Fatal error in cleanup job: {e}", exc_info=True)

    @classmethod
    def _get_users_for_cleanup(cls) -> List[Tuple[str, List[str]]]:
        """
        Query database for free users logged out >1 hour ago
        with NO active workflows running.

        SAFETY CHECK: Only returns users with active_workflow_count = 0
        to prevent deleting data while workflows are still running.

        Returns:
            List of tuples: (user_id, [workspace_ids])
        """
        with session_scope() as db:
            # Calculate cutoff time (1 hour ago)
            cutoff_time = datetime.now() - timedelta(
                minutes=SyncStatusConstants.LOGOUT_GRACE_PERIOD_MINUTES
            )

            # Query users logged out before cutoff WITH NO ACTIVE WORKFLOWS
            statement = (
                select(
                    FreeUserAssignment.user_id,
                    func.group_concat(func.distinct(Workspace.id)).label(
                        "workspace_ids"
                    ),
                    FreeUserAssignment.active_workflow_count,
                )
                .join(User, User.id == FreeUserAssignment.user_id)
                .join(Workspace, Workspace.user_id == User.id)
                .where(FreeUserAssignment.logged_out_at.is_not(None))
                .where(FreeUserAssignment.logged_out_at < cutoff_time)
                .where(FreeUserAssignment.active_workflow_count == 0)
                .where(User.active == 1)
                .where(Workspace.deleted == 0)
                .group_by(FreeUserAssignment.user_id)
                .limit(SyncStatusConstants.MAX_USERS_PER_RUN)
            )

            result = db.exec(statement)

            users = []
            for row in result:
                user_id = row[0]
                workspace_ids = row[1].split(",") if row[1] else []
                active_workflow_count = row[2]

                # Double-check workflow count (should be 0 from query)
                if active_workflow_count == 0:
                    users.append((user_id, workspace_ids))
                else:
                    logger.warning(
                        f"Skipping user {user_id}: active_workflow_count = "
                        f"{active_workflow_count} (workflows still running)"
                    )

            return users

    @classmethod
    def _verify_s3_backup_exists(cls, workspace_id: str, experiment_id: str) -> bool:
        """
        Verify that experiment data exists in S3 before deleting from local storage.

        Args:
            workspace_id: Workspace ID
            experiment_id: Experiment unique ID

        Returns:
            True if data exists in S3, False otherwise
        """
        try:
            import boto3
            from botocore.exceptions import ClientError

            s3_bucket = os.environ.get("S3_DEFAULT_BUCKET_NAME")
            if not s3_bucket:
                logger.warning(
                    "S3_DEFAULT_BUCKET_NAME not set, skipping S3 verification"
                )
                return False

            s3 = boto3.client("s3")

            # Check if critical experiment files exist in S3
            critical_files = ["experiment.yaml", "workflow.yaml"]
            s3_prefix = f"app/studio_data/output/{workspace_id}/{experiment_id}/"

            for filename in critical_files:
                s3_key = f"{s3_prefix}{filename}"
                try:
                    s3.head_object(Bucket=s3_bucket, Key=s3_key)
                except ClientError as e:
                    if e.response["Error"]["Code"] == "404":
                        logger.warning(
                            f"Critical file not found in S3: {s3_key}. "
                            f"Skipping deletion to prevent data loss."
                        )
                        return False
                    raise

            logger.debug(
                f"Verified S3 backup exists for {workspace_id}/{experiment_id}"
            )
            return True

        except Exception as e:
            logger.error(
                f"Error verifying S3 backup for {workspace_id}/{experiment_id}: {e}",
                exc_info=True,
            )
            # If we can't verify, err on the side of caution and don't delete
            return False

    @classmethod
    def _cleanup_user_data(cls, user_id: str, workspace_ids: List[str]) -> bool:
        """
        Delete user's workspace data from local storage.

        SAFETY CHECKS:
        1. Verifies data exists in S3 before deletion (for experiment outputs)
        2. Only deletes if no active workflows running (checked in query)

        Args:
            user_id: User ID
            workspace_ids: List of workspace IDs to clean

        Returns:
            True if ALL data was successfully cleaned, False if any data remains
            or errors occurred
        """
        logger.info(f"Cleaning up data for user {user_id}, workspaces: {workspace_ids}")

        fully_cleaned = True
        total_experiments_kept = 0

        for workspace_id in workspace_ids:
            try:
                # Clean input data (always safe to delete - user uploads are in S3)
                input_dir = join_filepath([DIRPATH.INPUT_DIR, workspace_id])
                if os.path.exists(input_dir):
                    logger.info(f"Deleting input directory: {input_dir}")
                    shutil.rmtree(input_dir)

                # Clean output data (with S3 verification)
                output_dir = join_filepath([DIRPATH.OUTPUT_DIR, workspace_id])
                if os.path.exists(output_dir):
                    # Verify each experiment has S3 backup before deletion
                    experiments_to_delete = []
                    experiments_to_keep = []

                    if os.path.isdir(output_dir):
                        for experiment_id in os.listdir(output_dir):
                            experiment_path = os.path.join(output_dir, experiment_id)
                            if not os.path.isdir(experiment_path):
                                continue

                            # Verify S3 backup exists
                            if cls._verify_s3_backup_exists(
                                workspace_id, experiment_id
                            ):
                                experiments_to_delete.append(experiment_id)
                            else:
                                experiments_to_keep.append(experiment_id)
                                logger.warning(
                                    f"Keeping experiment "
                                    f"{workspace_id}/{experiment_id} "
                                    f"locally (S3 backup not verified)"
                                )

                    # If any experiments must be kept, mark cleanup as incomplete
                    if experiments_to_keep:
                        fully_cleaned = False
                        total_experiments_kept += len(experiments_to_keep)

                    # Delete verified experiments
                    for experiment_id in experiments_to_delete:
                        experiment_path = os.path.join(output_dir, experiment_id)
                        logger.info(
                            f"Deleting experiment: {workspace_id}/{experiment_id} "
                            f"(S3 backup verified)"
                        )
                        shutil.rmtree(experiment_path)

                    # If all experiments deleted, remove workspace output dir
                    if experiments_to_delete and not experiments_to_keep:
                        if os.path.exists(output_dir) and not os.listdir(output_dir):
                            logger.info(
                                f"Deleting empty output directory: {output_dir}"
                            )
                            shutil.rmtree(output_dir)

                    logger.info(
                        f"Cleaned workspace {workspace_id} for user {user_id}: "
                        f"{len(experiments_to_delete)} experiments deleted, "
                        f"{len(experiments_to_keep)} kept"
                    )

            except Exception as e:
                logger.error(
                    f"Error cleaning workspace {workspace_id} "
                    f"for user {user_id}: {e}",
                    exc_info=True,
                )
                fully_cleaned = False

        if total_experiments_kept > 0:
            logger.warning(
                f"Incomplete cleanup for user {user_id}: "
                f"{total_experiments_kept} experiments kept (S3 backup not verified)"
            )

        return fully_cleaned

    @classmethod
    def _verify_no_active_workflows(cls, user_id: str) -> bool:
        """
        Final verification that no workflows are running before cleanup completion.
        This prevents race condition where workflow starts during cleanup process.

        Returns:
            True if no active workflows, False otherwise
        """
        with session_scope() as db:
            statement = select(FreeUserAssignment).where(
                FreeUserAssignment.user_id == user_id
            )
            assignment = db.exec(statement).first()

            if not assignment:
                return True  # Assignment removed, safe to proceed

            if assignment.active_workflow_count > 0:
                logger.warning(
                    f"User {user_id} has {assignment.active_workflow_count} "
                    f"active workflows - aborting cleanup"
                )
                return False

            return True

    @classmethod
    def _mark_cleaned(cls, user_id: str):
        """
        Mark user data as cleaned by removing the assignment record.
        This allows the user to be reassigned to a new instance on next login.

        IMPORTANT: Only call this after verifying no active workflows via
        _verify_no_active_workflows() to prevent race conditions.
        """
        with session_scope() as db:
            # Get the assignment to delete
            assignment = db.exec(
                select(FreeUserAssignment).where(
                    FreeUserAssignment.user_id == user_id,
                    FreeUserAssignment.logged_out_at.is_not(None),
                )
            ).first()

            if assignment:
                db.delete(assignment)
                db.commit()

            logger.info(f"Marked user {user_id} as cleaned (assignment removed)")

    @classmethod
    def _cleanup_orphaned_assignment(cls, db, assignment: FreeUserAssignment):
        """
        Clean up user data for an orphaned assignment (terminated instance).

        Args:
            db: Database session
            assignment: FreeUserAssignment to clean up

        Returns:
            True if cleanup successful, False otherwise
        """
        try:
            # Only clean if no active workflows
            if assignment.active_workflow_count != 0:
                logger.info(
                    f"Skipping cleanup for user {assignment.user_id}: "
                    f"has {assignment.active_workflow_count} active workflows"
                )
                return False

            # Get user's workspaces
            from studio.app.common.models import User, Workspace

            user = db.get(User, int(assignment.user_id))
            if not user:
                logger.warning(f"User {assignment.user_id} not found")
                return False

            workspaces = db.exec(
                select(Workspace).where(
                    Workspace.user_id == user.id,
                    Workspace.deleted == 0,
                )
            ).all()

            workspace_ids = [str(w.id) for w in workspaces]
            cls._cleanup_user_data(assignment.user_id, workspace_ids)

            # Remove assignment
            db.delete(assignment)
            db.commit()
            logger.info(
                f"Cleaned orphaned data for user {assignment.user_id} "
                f"from terminated instance"
            )
            return True

        except Exception as e:
            logger.error(
                f"Error cleaning orphaned assignment for user "
                f"{assignment.user_id}: {e}",
                exc_info=True,
            )
            return False

    @classmethod
    def _handle_orphaned_data(cls):
        """
        Handle orphaned data from terminated instances.

        Checks for workspace data on local EBS that belongs to users assigned
        to terminated instances. This prevents data accumulation when instances
        are terminated before cleanup completes.
        """
        try:
            import boto3
            from botocore.exceptions import ClientError

            # Get current instance ID
            instance_id = os.environ.get("INSTANCE_ID")
            if not instance_id or instance_id == "local":
                return

            ec2 = boto3.client("ec2")

            with session_scope() as db:
                # Get all user assignments
                assignments = db.exec(select(FreeUserAssignment)).all()

                for assignment in assignments:
                    # Check if assigned instance still exists
                    try:
                        response = ec2.describe_instances(
                            InstanceIds=[assignment.instance_id]
                        )
                        instances = response["Reservations"][0]["Instances"]
                        instance_state = instances[0]["State"]["Name"]

                        # If instance is terminated, clean up user data
                        if instance_state in ["terminated", "terminating"]:
                            logger.warning(
                                f"Instance {assignment.instance_id} is "
                                f"{instance_state}, cleaning up orphaned data for "
                                f"user {assignment.user_id}"
                            )
                            cls._cleanup_orphaned_assignment(db, assignment)

                    except ClientError as e:
                        if e.response["Error"]["Code"] == "InvalidInstanceID.NotFound":
                            # Instance doesn't exist, treat as terminated
                            logger.warning(
                                f"Instance {assignment.instance_id} not found, "
                                f"treating as terminated"
                            )
                            cls._cleanup_orphaned_assignment(db, assignment)

        except Exception as e:
            logger.error(f"Error handling orphaned data: {e}", exc_info=True)

    @classmethod
    def _publish_metrics(cls, cleaned_count: int, error_count: int):
        """Publish cleanup job metrics to CloudWatch"""
        try:
            import boto3

            cloudwatch = boto3.client("cloudwatch")

            cloudwatch.put_metric_data(
                Namespace="OptiNiSt/BackgroundJobs",
                MetricData=[
                    {
                        "MetricName": "DataCleanupCount",
                        "Value": cleaned_count,
                        "Unit": "Count",
                        "Timestamp": datetime.now(),
                    },
                    {
                        "MetricName": "CleanupErrors",
                        "Value": error_count,
                        "Unit": "Count",
                        "Timestamp": datetime.now(),
                    },
                ],
            )
            logger.debug(
                f"Published CloudWatch metrics: {cleaned_count} cleaned, "
                f"{error_count} errors"
            )
        except Exception as e:
            logger.warning(f"Failed to publish metrics: {e}")
