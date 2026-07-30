"""
Background job to clean up workspace data for logged-out free users.

Runs every hour, deletes data for users logged out >1 hour ago.

COMPREHENSIVE SAFETY CHECKS:
1. Only processes users with active_workflow_count = 0 (no running workflows)
2. Verifies S3 backup exists before deleting experiment outputs AND input data
3. Keeps local data if S3 verification fails (prevents data loss)
4. Only the owning instance (matching FreeUserAssignment.instance_id) may
   close an assignment, so cleanup on another instance can never orphan data

This ensures:
- No data deletion while workflows are running (even long 2+ hour workflows)
- No data loss if S3 upload failed
- User data is cleaned up after logout with appropriate grace period
"""

import os
import shutil
from datetime import timedelta
from typing import List, Optional, Tuple

from sqlalchemy import func, update
from sqlmodel import select

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.storage.s3_storage_controller import S3StorageController
from studio.app.common.core.subscription.constants import SyncStatusConstants
from studio.app.common.core.utils.datetime_utils import get_current_datetime
from studio.app.common.core.utils.filepath_creater import join_filepath
from studio.app.common.core.utils.instance_utils import resolve_instance_id
from studio.app.common.db.database import session_scope
from studio.app.common.models import (
    FreeUserAssignment,
    InstanceUsageLog,
    User,
    Workspace,
)
from studio.app.common.models.instance_usage import UsageTier
from studio.app.dir_path import DIRPATH

logger = AppLogger.get_logger()


class DataCleanupJob:
    """Background job to clean up data for logged-out free users"""

    # Cache the resolved instance id (constant for a process lifetime) to
    # avoid repeating IMDS round-trips when INSTANCE_ID is unset.
    _instance_id_cache = None

    @classmethod
    def _get_current_instance_id(cls) -> str:
        """Return the EC2 instance ID for the current host (cached).

        Delegates to the shared ``resolve_instance_id`` (env → IMDSv2 →
        IMDSv1 → "local") so the worker resolves the *same* id the
        middleware writes into ``FreeUserAssignment.instance_id``; a
        divergence would silently drop the per-instance cleanup filter.
        """
        if cls._instance_id_cache is None:
            cls._instance_id_cache = resolve_instance_id()
        return cls._instance_id_cache

    @classmethod
    async def run(cls):
        """
        Main cleanup job execution:
        1. Handle orphaned data from terminated instances
        2. Query free users logged out >1 hour ago
        3. Delete user's workspace data from local storage
        4. Mark data as cleaned in database
        """
        instance_id = cls._get_current_instance_id()
        logger.info(
            f"Starting data cleanup job for logged-out free users "
            f"(instance: {instance_id})"
        )

        try:
            # Handle orphaned data only on the background service.
            # Free-tier cleanup workers (ENABLE_LOCAL_CLEANUP=1) should not
            # run orphan detection — that responsibility stays with the
            # background service which has EC2 describe permissions.
            if os.environ.get("ENABLE_LOCAL_CLEANUP") != "1":
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
                    if cls._check_user_relogin(user_id):
                        logger.info(
                            f"Skipping cleanup for user {user_id}: "
                            f"user logged back in"
                        )
                        continue

                    success = cls._cleanup_user_data(user_id, workspace_ids)

                    if success:
                        # Re-check workflow count and re-login before marking cleaned
                        # Prevents race condition where workflow/login during cleanup
                        if cls._check_user_relogin(user_id):
                            logger.warning(
                                f"Skipping cleanup completion for user {user_id}: "
                                f"user logged back in during cleanup"
                            )
                            error_count += 1
                        elif cls._verify_no_active_workflows(user_id):
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
            cutoff_time = get_current_datetime() - timedelta(
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
            )

            # Filter by instance_id so each cleanup worker only processes
            # users assigned to *this* instance.  In local dev (no
            # INSTANCE_ID env var) the filter is skipped for backward
            # compatibility.
            current_instance_id = cls._get_current_instance_id()
            if current_instance_id != "local":
                statement = statement.where(
                    FreeUserAssignment.instance_id == current_instance_id
                )

            statement = statement.group_by(FreeUserAssignment.user_id).limit(
                SyncStatusConstants.MAX_USERS_PER_RUN
            )

            result = db.execute(statement)

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
    def _resolve_user_bucket_name(cls, user_id: str) -> Optional[str]:
        """
        Resolve the S3 bucket that holds a user's experiment backups.

        Experiment outputs live in a per-user bucket, not the shared
        ``S3_DEFAULT_BUCKET_NAME``. Resolution order (most authoritative
        first):

        1. ``users.attributes.remote_bucket_name`` — the exact bucket the
           upload path recorded on the user row.
        2. ``create_user_bucket_name`` — the deterministic formula the writer
           uses (needs ``S3_USER_BUCKET_PREFIX`` / ``S3_USER_BUCKET_SECRET``
           to match).
        3. ``S3_DEFAULT_BUCKET_NAME`` — legacy single-bucket deployments.

        Returns the bucket name, or None if none could be resolved (caller
        then keeps the data rather than risking deletion).
        """
        # 1) Authoritative: bucket recorded on the user row.
        try:
            with session_scope() as db:
                result_row = db.execute(
                    select(User).where(User.id == int(user_id))
                ).first()
                user = result_row[0] if result_row else None
                if user and user.remote_bucket_name:
                    return user.remote_bucket_name
        except Exception as e:
            logger.warning(f"Could not read remote_bucket_name for user {user_id}: {e}")

        # 2) Deterministic fallback: same formula the writer uses.
        try:
            from studio.app.common.core.storage.remote_storage_controller import (
                RemoteStorageController,
            )

            prefix = os.environ.get("S3_USER_BUCKET_PREFIX", "optinist-user")
            return RemoteStorageController.create_user_bucket_name(
                id=int(user_id), prefix=prefix
            )
        except Exception as e:
            logger.warning(
                f"Could not derive per-user bucket name for user {user_id}: {e}"
            )

        # 3) Last resort: legacy shared bucket.
        return os.environ.get("S3_DEFAULT_BUCKET_NAME")

    @classmethod
    def _verify_s3_input_backup_exists(
        cls, s3_bucket: Optional[str], workspace_id: str
    ) -> bool:
        """
        Verify every local input file has an S3 backup before deletion.

        Input uploads are pushed to S3 synchronously on the free-tier upload
        path, but verify here (symmetric with experiment outputs) so a
        failed/rolled-back upload can never cause silent input data loss.

        Returns:
            True only if the bucket resolves and every regular file under the
            workspace input dir (excluding the ``.locks`` working dir) is
            present in S3. False on any missing file or verification error.
        """
        try:
            import boto3
            from botocore.exceptions import ClientError

            if not s3_bucket:
                logger.warning(
                    "No S3 bucket resolved for input verification; "
                    "keeping data to prevent loss"
                )
                return False

            input_dir = join_filepath([DIRPATH.INPUT_DIR, workspace_id])
            if not os.path.isdir(input_dir):
                return True

            from studio.app.common.core.storage.remote_storage_controller import (
                InputFileLock,
            )

            s3 = boto3.client("s3")
            s3_prefix = (
                f"{S3StorageController.S3_BASE_PATH}"
                f"/{S3StorageController.S3_INPUT_DIR}/{workspace_id}/"
            )

            for root, dirs, files in os.walk(input_dir):
                # Skip the per-file flock working dir; it is never uploaded.
                dirs[:] = [d for d in dirs if d != InputFileLock.LOCKS_DIRNAME]
                for filename in files:
                    rel_path = os.path.relpath(os.path.join(root, filename), input_dir)
                    s3_key = f"{s3_prefix}{rel_path}"
                    try:
                        s3.head_object(Bucket=s3_bucket, Key=s3_key)
                    except ClientError as e:
                        if e.response["Error"]["Code"] == "404":
                            logger.warning(
                                f"Input file not found in S3: {s3_key}. "
                                f"Skipping deletion to prevent data loss."
                            )
                            return False
                        raise

            logger.debug(f"Verified S3 input backup exists for {workspace_id}")
            return True

        except Exception as e:
            logger.error(
                f"Error verifying S3 input backup for workspace {workspace_id}: {e}",
                exc_info=True,
            )
            # If we can't verify, err on the side of caution and don't delete
            return False

    @classmethod
    def _verify_s3_backup_exists(
        cls, s3_bucket: Optional[str], workspace_id: str, experiment_id: str
    ) -> bool:
        """
        Verify that experiment data exists in S3 before deleting from local storage.

        Args:
            s3_bucket: The user's S3 bucket (see ``_resolve_user_bucket_name``)
            workspace_id: Workspace ID
            experiment_id: Experiment unique ID

        Returns:
            True if data exists in S3, False otherwise
        """
        try:
            import boto3
            from botocore.exceptions import ClientError

            if not s3_bucket:
                logger.warning(
                    "No S3 bucket resolved for verification; "
                    "keeping data to prevent loss"
                )
                return False

            s3 = boto3.client("s3")

            # Check if critical experiment files exist in S3
            critical_files = ["experiment.yaml", "workflow.yaml"]
            s3_prefix = (
                f"{S3StorageController.S3_BASE_PATH}"
                f"/output/{workspace_id}/{experiment_id}/"
            )

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
        3. Checks if user logged back in before each workspace

        Args:
            user_id: User ID
            workspace_ids: List of workspace IDs to clean

        Returns:
            True if ALL data was successfully cleaned, False if any data remains
            or errors occurred
        """
        logger.info(f"Cleaning up data for user {user_id}, workspaces: {workspace_ids}")

        fully_cleaned = True
        data_found = False
        total_experiments_kept = 0

        # Experiment outputs live in the user's own bucket, not the shared
        # default bucket. Resolved lazily on first need and cached, so users
        # with no output data incur no bucket lookup.
        s3_bucket = None
        s3_bucket_resolved = False

        for workspace_id in workspace_ids:
            # Check if user logged back in before processing workspace
            if cls._check_user_relogin(user_id):
                logger.info(f"Aborting cleanup for user {user_id}: user logged back in")
                return False
            try:
                # Clean input data. Uploads are pushed to S3 synchronously on
                # the free-tier upload path, but verify the backup before
                # deleting (symmetric with experiment outputs) so a failed or
                # rolled-back upload can't cause silent data loss.
                input_dir = join_filepath([DIRPATH.INPUT_DIR, workspace_id])
                if os.path.exists(input_dir):
                    data_found = True

                    # Resolve the user's bucket once (shared with outputs).
                    if not s3_bucket_resolved:
                        s3_bucket = cls._resolve_user_bucket_name(user_id)
                        s3_bucket_resolved = True

                    if cls._verify_s3_input_backup_exists(s3_bucket, workspace_id):
                        logger.debug(f"Deleting input directory: {input_dir}")
                        shutil.rmtree(input_dir)
                    else:
                        fully_cleaned = False
                        logger.warning(
                            f"Keeping input directory {input_dir} for user "
                            f"{user_id} (S3 backup not verified)"
                        )

                # Clean output data (with S3 verification)
                output_dir = join_filepath([DIRPATH.OUTPUT_DIR, workspace_id])
                if os.path.exists(output_dir):
                    data_found = True

                    # Resolve the user's bucket once, on first output data.
                    if not s3_bucket_resolved:
                        s3_bucket = cls._resolve_user_bucket_name(user_id)
                        s3_bucket_resolved = True

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
                                s3_bucket, workspace_id, experiment_id
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
                        logger.debug(
                            f"Deleting experiment: {workspace_id}/{experiment_id} "
                            f"(S3 backup verified)"
                        )
                        shutil.rmtree(experiment_path)

                    # If all experiments deleted, remove workspace output dir
                    if experiments_to_delete and not experiments_to_keep:
                        if os.path.exists(output_dir) and not os.listdir(output_dir):
                            logger.debug(
                                f"Deleting empty output directory: {output_dir}"
                            )
                            shutil.rmtree(output_dir)

                    logger.debug(
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

        if not data_found:
            # No local data on disk:
            # - filtered run → this worker owns the assignment, nothing to
            #   delete → success (let _mark_cleaned close assignment/usage log)
            # - unfiltered run → data may be on another instance → keep guard
            current_instance_id = cls._get_current_instance_id()
            if current_instance_id != "local":
                # Defense-in-depth: only treat "no local data" as a completed
                # cleanup when this instance actually owns the assignment.
                # The selection query already filters on instance_id, but a
                # stray mid-run reassignment must never let a non-owning
                # instance delete the DB record and orphan another instance's
                # data. If ownership can't be confirmed, keep the record.
                if not cls._owns_assignment(user_id, current_instance_id):
                    logger.warning(
                        f"No local data for user {user_id}, but this instance "
                        f"({current_instance_id}) no longer owns the assignment. "
                        f"Keeping DB record to avoid orphaning data elsewhere."
                    )
                    return False
                logger.info(f"No local data for user {user_id}; nothing to clean.")
                return True
            logger.warning(
                f"No local data found for user {user_id} (unfiltered run). "
                f"Returning False to prevent premature DB record deletion."
            )
            return False

        return fully_cleaned

    @classmethod
    def _owns_assignment(cls, user_id: str, current_instance_id: str) -> bool:
        """Return True if the assignment is pinned to ``current_instance_id``.

        ``FreeUserAssignment.instance_id`` is the authoritative "which
        instance owns this user's local EBS data" marker (pinned at INSERT by
        the middleware and never rewritten). A worker may only close an
        assignment for which it is the owning instance; otherwise "no local
        data" would delete a record whose data lives on another instance.
        """
        with session_scope() as db:
            result_row = db.execute(
                select(FreeUserAssignment.instance_id).where(
                    FreeUserAssignment.user_id == user_id
                )
            ).first()
            if not result_row:
                # Assignment already gone — nothing to orphan.
                return True
            return result_row[0] == current_instance_id

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
            result_row = db.execute(statement).first()
            assignment = result_row[0] if result_row else None

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
    def _check_user_relogin(cls, user_id: str) -> bool:
        """
        Check if user has logged back in during cleanup.
        If logged_out_at is NULL, user has logged back in.

        Returns:
            True if user has logged back in (abort cleanup), False if still logged out
        """
        with session_scope() as db:
            statement = select(FreeUserAssignment).where(
                FreeUserAssignment.user_id == user_id
            )
            result_row = db.execute(statement).first()
            assignment = result_row[0] if result_row else None

            if not assignment:
                # Assignment removed, safe to proceed with cleanup
                return False

            if assignment.logged_out_at is None:
                logger.warning(
                    f"User {user_id} has logged back in during cleanup "
                    f"(logged_out_at is NULL) - aborting cleanup"
                )
                return True

            return False

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
            result_row = db.execute(
                select(FreeUserAssignment).where(
                    FreeUserAssignment.user_id == user_id,
                    FreeUserAssignment.logged_out_at.is_not(None),
                )
            ).first()
            assignment = result_row[0] if result_row else None

            if assignment:
                # Close usage log before deleting assignment
                db.execute(
                    update(InstanceUsageLog)
                    .where(
                        InstanceUsageLog.user_id == user_id,
                        InstanceUsageLog.tier == UsageTier.FREE,
                        InstanceUsageLog.ended_at.is_(None),
                    )
                    .values(ended_at=get_current_datetime())
                )
                db.delete(assignment)
                db.commit()

            logger.info(f"Marked user {user_id} as cleaned (assignment removed)")

    @classmethod
    def _cleanup_orphaned_assignment(cls, db, assignment: FreeUserAssignment):
        """
        Remove the DB record for an orphaned assignment (terminated instance).

        When an EC2 instance is terminated its local EBS is destroyed, so
        there is no filesystem data to clean up.  This method only removes
        the stale ``FreeUserAssignment`` row and closes the open
        ``InstanceUsageLog``.

        Args:
            db: Database session
            assignment: FreeUserAssignment to clean up

        Returns:
            True if cleanup successful, False otherwise
        """
        try:
            # Close usage log before deleting assignment
            db.execute(
                update(InstanceUsageLog)
                .where(
                    InstanceUsageLog.user_id == assignment.user_id,
                    InstanceUsageLog.tier == UsageTier.FREE,
                    InstanceUsageLog.ended_at.is_(None),
                )
                .values(ended_at=get_current_datetime())
            )

            # Remove assignment
            db.delete(assignment)
            db.commit()
            logger.info(
                f"Removed orphaned assignment for user {assignment.user_id} "
                f"from terminated instance {assignment.instance_id} "
                f"(EBS already destroyed, DB record only)"
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

            # Get current instance ID via the shared resolver (env → IMDS →
            # "local") so orphan detection uses the same id as the rest of the
            # job rather than depending on INSTANCE_ID being exported.
            instance_id = cls._get_current_instance_id()
            if not instance_id or instance_id == "local":
                return

            ec2 = boto3.client("ec2")

            with session_scope() as db:
                # Get all user assignments
                assignments_result = db.execute(select(FreeUserAssignment)).all()

                for row in assignments_result:
                    assignment = row[0]
                    # Check if assigned instance still exists
                    try:
                        response = ec2.describe_instances(
                            InstanceIds=[assignment.instance_id]
                        )
                        reservations = response.get("Reservations", [])
                        if not reservations or not reservations[0].get("Instances"):
                            logger.warning(
                                f"Instance "
                                f"{assignment.instance_id} has "
                                f"no reservation data, "
                                f"treating as terminated"
                            )
                            cls._cleanup_orphaned_assignment(db, assignment)
                            continue

                        instance_state = reservations[0]["Instances"][0]["State"][
                            "Name"
                        ]

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

            env_prefix = os.environ.get("ENV_PREFIX", "default")
            cloudwatch.put_metric_data(
                Namespace=f"OptiNiSt/BackgroundJobs/{env_prefix}",
                MetricData=[
                    {
                        "MetricName": "DataCleanupCount",
                        "Value": cleaned_count,
                        "Unit": "Count",
                        "Timestamp": get_current_datetime(),
                    },
                    {
                        "MetricName": "CleanupErrors",
                        "Value": error_count,
                        "Unit": "Count",
                        "Timestamp": get_current_datetime(),
                    },
                ],
            )
            logger.debug(
                f"Published CloudWatch metrics: {cleaned_count} cleaned, "
                f"{error_count} errors"
            )
        except Exception as e:
            logger.warning(f"Failed to publish metrics: {e}")
