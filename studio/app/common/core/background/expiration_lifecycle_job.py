"""
Background job for expiration lifecycle deletion.

Runs daily. Finds users past the grace period whose storage exceeds
the free-tier quota, then enumerates and deletes S3 data in priority
order until storage is within the free-tier quota.

Deletion is streamed one unit at a time to limit memory usage.
"""

import asyncio
import os
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import List, Optional, Tuple, Union

import aioboto3
from sqlalchemy import or_ as db_or

from studio.app.common.core.cloud.storage_operations import decrement_storage_idempotent
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.storage.s3_storage_controller import (
    S3StorageController,
    is_no_such_bucket_error,
)
from studio.app.common.core.subscription.constants import (
    DeletionPriority,
    ExpirationDeletion,
    S3Pagination,
)
from studio.app.common.core.subscription.subscription_service import SubscriptionService
from studio.app.common.db.database import session_scope
from studio.app.common.models.experiment import ExperimentRecord
from studio.app.common.models.workspace import Workspace
from studio.app.common.schemas.dataview import PublishStatus
from studio.app.dir_path import DIRPATH

logger = AppLogger.get_logger()

_YAML_EXTENSIONS = frozenset({".yaml", ".yml"})


class _DeletionTier(str, Enum):
    INTERMEDIATES = "intermediates"
    OUTPUTS = "outputs"
    INPUTS = "inputs"


@dataclass
class _ExperimentInfo:
    """Lightweight experiment metadata fetched from DB."""

    workspace_id: int
    uid: str
    is_published: bool
    analyzed_at: Optional[datetime]


@dataclass
class _WorkspaceInputInfo:
    """Lightweight workspace metadata for input deletion."""

    workspace_id: int
    has_published_experiments: bool
    created_at: Optional[datetime]


class ExpirationLifecycleJob:
    @classmethod
    async def run(cls):
        """Daily job: find users past grace period, execute deletion."""
        logger.info("Starting expiration lifecycle job")

        try:
            bucket_name = getattr(DIRPATH, "DATA_BUCKET_NAME", None)
            if not bucket_name:
                logger.warning(
                    "No remote bucket configured, skipping expiration lifecycle"
                )
                return

            with session_scope() as db:
                users = SubscriptionService.get_users_for_expiration_deletion(db)

            if not users:
                logger.debug("No users eligible for expiration deletion")
                return

            logger.info(f"Processing {len(users)} users for expiration deletion")

            processed = 0
            errors = 0

            for user_info in users:
                try:
                    await cls._process_user(user_info, bucket_name)
                    processed += 1
                except Exception as e:
                    logger.error(
                        f"Error processing expiration deletion for user "
                        f"{user_info['user_id']}: {e}",
                        exc_info=True,
                    )
                    errors += 1

            logger.info(
                f"Expiration lifecycle job completed: "
                f"{processed} users processed, {errors} errors"
            )
            cls._publish_metrics(processed, errors)

        except Exception as e:
            logger.error(f"Fatal error in expiration lifecycle job: {e}", exc_info=True)

    @classmethod
    async def _process_user(cls, user_info: dict, bucket_name: str):
        """Process expiration deletion for a single user."""
        user_id = user_info["user_id"]
        excess_bytes = user_info["excess_bytes"]

        # Check subscription status and active workflows in one session
        with session_scope() as db:
            if SubscriptionService.get_user_subscription(db, user_id):
                logger.info(
                    f"User {user_id} has active subscription, skipping deletion"
                )
                SubscriptionService.mark_deletion_processed(db, user_id)
                return

            if SubscriptionService.has_active_workflows(db, user_id):
                logger.info(f"User {user_id} has active workflows, deferring deletion")
                return

            # Read deletion priority from UserPreferences
            priority = SubscriptionService.get_deletion_priority(db, user_id)

            # Fetch DB data while session is open, close before S3 ops
            experiments, workspace_inputs = cls._fetch_user_data(db, user_id)

        if not experiments and not workspace_inputs:
            logger.info(f"No deletable data found for user {user_id}")
            with session_scope() as db:
                SubscriptionService.mark_deletion_processed(db, user_id)
            return

        # Re-check current storage to get fresh excess_bytes (may have
        # changed since the eligibility query ran)
        with session_scope() as db:
            fresh_excess = SubscriptionService.get_current_excess_bytes(db, user_id)
        if fresh_excess is not None and fresh_excess <= 0:
            logger.info(f"User {user_id} storage now within quota, skipping deletion")
            with session_scope() as db:
                SubscriptionService.mark_deletion_processed(db, user_id)
            return
        if fresh_excess is not None:
            excess_bytes = fresh_excess

        # Execute deletion (streaming — enumerate and delete one unit at a time)
        run_id = f"{user_id}_{int(time.time())}"
        result = await cls._execute_deletion(
            user_id=user_id,
            bucket_name=bucket_name,
            experiments=experiments,
            workspace_inputs=workspace_inputs,
            priority=priority,
            target_bytes=excess_bytes,
            run_id=run_id,
        )

        # Only mark processed if deletion fully succeeded or target met
        if result["failed"] == 0 or result["bytes_deleted"] >= excess_bytes:
            purged_uids = list(result.get("purged_uids", set()))
            with session_scope() as db:
                SubscriptionService.mark_deletion_processed(db, user_id)
                if purged_uids:
                    cls._mark_experiments_purged(db, purged_uids)
        else:
            logger.warning(
                f"Partial failure for user {user_id}: "
                f"{result['failed']} units failed, "
                f"{result['bytes_deleted']}/{excess_bytes} bytes freed. "
                f"User will be retried next run."
            )

        logger.info(
            f"Expiration deletion for user {user_id}: "
            f"{result['succeeded']} units deleted, "
            f"{result['failed']} failed, "
            f"{result['bytes_deleted']} bytes freed"
            + (" (aborted: user re-subscribed)" if result["aborted"] else "")
        )

    @classmethod
    def _mark_experiments_purged(cls, db, purged_uids: List[str]):
        """Mark experiments as data-purged after successful S3 deletion.

        Note: Caller is responsible for committing (e.g. via session_scope).
        """
        if not purged_uids:
            return
        db.query(ExperimentRecord).filter(ExperimentRecord.uid.in_(purged_uids)).update(
            {"deletion_error": ExpirationDeletion.DATA_PURGED_MARKER},
            synchronize_session="fetch",
        )

    @classmethod
    def _fetch_user_data(
        cls, db, user_id: int
    ) -> Tuple[List[_ExperimentInfo], List[_WorkspaceInputInfo]]:
        """Fetch experiment and workspace metadata from DB (no S3 calls)."""
        workspaces = (
            db.query(Workspace)
            .filter(
                Workspace.user_id == user_id,
                Workspace.deleted == False,  # noqa: E712
            )
            .all()
        )

        experiments: List[_ExperimentInfo] = []
        workspace_inputs: List[_WorkspaceInputInfo] = []

        for ws in workspaces:
            ws_experiments = (
                db.query(ExperimentRecord)
                .filter(
                    ExperimentRecord.workspace_id == ws.id,
                    db_or(
                        ExperimentRecord.deletion_error.is_(None),
                        ExperimentRecord.deletion_error
                        != ExpirationDeletion.DATA_PURGED_MARKER,
                    ),
                )
                .order_by(ExperimentRecord.analyzed_at.asc().nulls_first())
                .all()
            )

            has_published = False
            for exp in ws_experiments:
                is_published = exp.publish_status == PublishStatus.on.value
                if is_published:
                    has_published = True
                experiments.append(
                    _ExperimentInfo(
                        workspace_id=ws.id,
                        uid=exp.uid,
                        is_published=is_published,
                        analyzed_at=exp.analyzed_at,
                    )
                )

            workspace_inputs.append(
                _WorkspaceInputInfo(
                    workspace_id=ws.id,
                    has_published_experiments=has_published,
                    created_at=ws.created_at,
                )
            )

        return experiments, workspace_inputs

    @classmethod
    async def _execute_deletion(
        cls,
        user_id: int,
        bucket_name: str,
        experiments: List[_ExperimentInfo],
        workspace_inputs: List[_WorkspaceInputInfo],
        priority: str,
        target_bytes: int,
        run_id: str,
    ) -> dict:
        """
        Stream through deletion units in priority order.

        Enumerates S3 and deletes one unit at a time to limit memory.
        Stops when target_bytes is reached or user re-subscribes.

        Tier ordering by priority preference:
            preserve_outputs:  INTERMEDIATES -> INPUTS -> OUTPUTS
            preserve_inputs:   INTERMEDIATES -> OUTPUTS -> INPUTS

        Within each tier, units are sorted:
            1. Unpublished before published
            2. Oldest analyzed_at first (NULL sorts earliest)

        Root-level YAML files (.yaml, .yml) are always protected
        and never included in any tier.
        """
        result = {
            "succeeded": 0,
            "failed": 0,
            "bytes_deleted": 0,
            "aborted": False,
            "purged_uids": set(),
        }
        accumulated = 0
        units_processed = 0

        # Determine tier ordering based on priority
        if priority == DeletionPriority.PRESERVE_INPUTS.value:
            tier_order = [
                (_DeletionTier.INTERMEDIATES, experiments),
                (_DeletionTier.OUTPUTS, experiments),
                (_DeletionTier.INPUTS, workspace_inputs),
            ]
        else:
            # Default: preserve_outputs
            tier_order = [
                (_DeletionTier.INTERMEDIATES, experiments),
                (_DeletionTier.INPUTS, workspace_inputs),
                (_DeletionTier.OUTPUTS, experiments),
            ]

        sorted_experiments = sorted(
            experiments,
            key=lambda exp: (exp.is_published, exp.analyzed_at or datetime.min),
        )
        sorted_inputs = sorted(
            workspace_inputs,
            key=lambda ws: (
                ws.has_published_experiments,
                ws.created_at or datetime.min,
            ),
        )

        async with aioboto3.Session().resource("s3") as s3_resource:
            bucket = await s3_resource.Bucket(bucket_name)

            for tier, items in tier_order:
                if accumulated >= target_bytes:
                    break

                sorted_items = (
                    sorted_inputs
                    if tier == _DeletionTier.INPUTS
                    else sorted_experiments
                )

                for item in sorted_items:
                    if accumulated >= target_bytes:
                        break

                    # Periodic re-subscription check
                    units_processed += 1
                    if (
                        units_processed > 1
                        and units_processed
                        % ExpirationDeletion.RECHECK_SUBSCRIPTION_INTERVAL
                        == 0
                    ):
                        if cls._has_active_subscription(user_id):
                            logger.info(
                                f"User {user_id} re-subscribed, "
                                f"aborting after {units_processed} units"
                            )
                            result["aborted"] = True
                            return result

                    try:
                        bytes_deleted = await cls._delete_unit(
                            user_id, bucket, tier, item, run_id
                        )
                        result["succeeded"] += 1
                        result["bytes_deleted"] += bytes_deleted
                        accumulated += bytes_deleted

                        if (
                            tier == _DeletionTier.OUTPUTS
                            and hasattr(item, "uid")
                            and bytes_deleted > 0
                        ):
                            result["purged_uids"].add(item.uid)
                    except Exception as e:
                        logger.error(
                            f"Deletion failed for user {user_id}, "
                            f"tier {tier.value}: {e}"
                        )
                        result["failed"] += 1

        return result

    @classmethod
    async def _delete_unit(
        cls,
        user_id: int,
        bucket,
        tier: _DeletionTier,
        item: Union[_ExperimentInfo, _WorkspaceInputInfo],
        run_id: str,
    ) -> int:
        """
        Enumerate S3 keys for a single unit and delete them.

        Returns bytes deleted.
        """
        if tier == _DeletionTier.INPUTS:
            prefix = S3StorageController.make_s3_input_prefix(
                workspace_id=str(item.workspace_id)
            )
            keys_with_sizes = await cls._list_all_objects(bucket, prefix)
        else:
            prefix = S3StorageController.make_s3_output_prefix(
                workspace_id=str(item.workspace_id),
                unique_id=item.uid,
            )
            keys_with_sizes = await cls._list_experiment_objects(bucket, prefix, tier)

        if not keys_with_sizes:
            return 0

        # Build idempotency key
        unit_id = item.uid if hasattr(item, "uid") else f"ws_{item.workspace_id}"
        idempotency_key = f"exp_del_{run_id}_{item.workspace_id}_{unit_id}_{tier.value}"

        total_deleted_bytes = 0

        for i in range(0, len(keys_with_sizes), S3Pagination.PAGE_SIZE):
            batch = keys_with_sizes[i : i + S3Pagination.PAGE_SIZE]
            batch_keys = [{"Key": key} for key, _ in batch]
            batch_bytes = sum(size for _, size in batch)

            try:
                await bucket.delete_objects(
                    Delete={"Objects": batch_keys, "Quiet": True}
                )
                total_deleted_bytes += batch_bytes
            except Exception as e:
                if is_no_such_bucket_error(e):
                    logger.warning(
                        f"Bucket does not exist, skipping deletion for {prefix}"
                    )
                    break
                logger.error(
                    f"Batch deletion failed for {prefix} "
                    f"(batch {i // S3Pagination.PAGE_SIZE}): {e}"
                )
                break  # Stop further batches on failure

        if total_deleted_bytes > 0:
            decrement_storage_idempotent(user_id, total_deleted_bytes, idempotency_key)

        logger.info(
            f"Deleted {total_deleted_bytes} bytes for user {user_id}, "
            f"tier={tier.value}, prefix={prefix}"
        )

        return total_deleted_bytes

    @classmethod
    async def _list_experiment_objects(
        cls, bucket, prefix: str, tier: _DeletionTier
    ) -> List[Tuple[str, int]]:
        """
        List S3 objects under an experiment prefix filtered by tier.

        S3 layout per experiment (under S3_OUTPUT_DIR):
            {workspace_id}/{unique_id}/
                experiment.yaml    <- protected config (never deleted)
                workflow.yml       <- protected config (never deleted)
                result.npy         <- output (root-level non-YAML)
                subdir/temp.dat    <- intermediate (any file in a subdirectory)

        Classification (relative to experiment prefix):
        - Root-level .yaml/.yml -> protected (never deleted)
        - Files in subdirectories -> intermediates
        - Root-level non-YAML -> outputs

        Returns list of (key, size) tuples.
        """
        keys_with_sizes: List[Tuple[str, int]] = []

        async for obj in bucket.objects.filter(Prefix=prefix):
            remaining = obj.key[len(prefix) :]
            if not remaining:
                continue

            _, ext = os.path.splitext(remaining)

            if "/" in remaining:
                obj_tier = _DeletionTier.INTERMEDIATES
            elif ext.lower() in _YAML_EXTENSIONS:
                continue  # Protected
            else:
                obj_tier = _DeletionTier.OUTPUTS

            if obj_tier == tier:
                keys_with_sizes.append((obj.key, obj.size or 0))

        return keys_with_sizes

    @classmethod
    async def _list_all_objects(cls, bucket, prefix: str) -> List[Tuple[str, int]]:
        """List all S3 objects under a prefix. Returns list of (key, size) tuples."""
        keys_with_sizes: List[Tuple[str, int]] = []

        async for obj in bucket.objects.filter(Prefix=prefix):
            keys_with_sizes.append((obj.key, obj.size or 0))

        return keys_with_sizes

    @staticmethod
    def _has_active_subscription(user_id: int) -> bool:
        """Check if user has an active (unexpired) subscription."""
        with session_scope() as db:
            return SubscriptionService.get_user_subscription(db, user_id) is not None

    @classmethod
    def _publish_metrics(cls, processed: int, errors: int):
        """Publish job metrics to CloudWatch.

        Wraps the sync boto3 call in run_in_executor to avoid blocking
        the async event loop.
        """

        def _put_metrics():
            import boto3

            from studio.app.common.core.utils.datetime_utils import get_current_datetime

            now = get_current_datetime()
            cloudwatch = boto3.client("cloudwatch")
            cloudwatch.put_metric_data(
                Namespace=ExpirationDeletion.METRIC_NAMESPACE,
                MetricData=[
                    {
                        "MetricName": ExpirationDeletion.METRIC_PROCESSED,
                        "Value": processed,
                        "Unit": "Count",
                        "Timestamp": now,
                    },
                    {
                        "MetricName": ExpirationDeletion.METRIC_ERRORS,
                        "Value": errors,
                        "Unit": "Count",
                        "Timestamp": now,
                    },
                ],
            )

        def _on_done(f):
            exc = f.exception()
            if exc:
                logger.error(f"Failed to publish metrics: {exc}")

        try:
            loop = asyncio.get_running_loop()
            future = loop.run_in_executor(None, _put_metrics)
            future.add_done_callback(_on_done)
        except Exception as e:
            logger.error(f"Failed to schedule metrics: {e}")
