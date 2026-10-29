"""
Background job for expiration lifecycle deletion.

Runs daily. Sends grace-period notification emails (when enabled), then
finds users past the grace period whose storage exceeds the free-tier
quota, and enumerates and deletes data (both S3 and local) in priority
order until storage is within the free-tier quota.

Deletion is streamed one unit at a time to limit memory usage.
"""

import asyncio
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import List, Optional, Tuple, Union

import aioboto3
from sqlalchemy import or_ as db_or

from studio.app.common.core.background.cleanup_job import DataCleanupJob
from studio.app.common.core.cloud.storage_operations import decrement_storage_idempotent
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.storage.remote_storage_controller import RemoteStorageType
from studio.app.common.core.storage.s3_storage_controller import (
    S3StorageController,
    is_no_such_bucket_error,
)
from studio.app.common.core.subscription.constants import (
    DeletionPriority,
    ExpirationDeletion,
)
from studio.app.common.core.subscription.email_service import EmailService
from studio.app.common.core.subscription.subscription_service import SubscriptionService
from studio.app.common.core.utils.datetime_utils import get_current_datetime
from studio.app.common.core.utils.filepath_creater import join_filepath
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
        """Daily job: send grace notifications, then execute expiration deletion."""
        logger.info("Starting expiration lifecycle job")

        try:
            if RemoteStorageType.get_activated_type() != RemoteStorageType.S3:
                logger.warning(
                    "Remote storage is not S3, skipping expiration lifecycle. "
                    "Local-only cleanup is handled by DataCleanupJob."
                )
                return

            # Deliberately after the storage guard: when no deletion can
            # happen, no deletion warnings are sent either.
            emails_sent = 0
            emails_failed = 0
            try:
                emails_sent, emails_failed = await cls._send_grace_notifications()
            except Exception as e:
                logger.error(f"Grace notification pass failed: {e}", exc_info=True)

            with session_scope() as db:
                users = SubscriptionService.get_users_for_expiration_deletion(
                    db, require_notice=EmailService.is_enabled()
                )

            if not users:
                logger.debug("No users eligible for expiration deletion")
                cls._publish_metrics(0, 0, emails_sent, emails_failed)
                return

            logger.info(f"Processing {len(users)} users for expiration deletion")

            processed = 0
            errors = 0

            for user_info in users:
                try:
                    bucket_name = DataCleanupJob._resolve_user_bucket_name(
                        user_info["user_id"]
                    )
                    if not bucket_name:
                        logger.warning(
                            f"No bucket resolved for user {user_info['user_id']}, "
                            f"keeping their data"
                        )
                        continue
                    sent, failed = await cls._process_user(user_info, bucket_name)
                    emails_sent += sent
                    emails_failed += failed
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
            cls._publish_metrics(processed, errors, emails_sent, emails_failed)

        except Exception as e:
            logger.error(f"Fatal error in expiration lifecycle job: {e}", exc_info=True)

    @classmethod
    async def _process_user(cls, user_info: dict, bucket_name: str) -> Tuple[int, int]:
        """Process expiration deletion for a single user.

        Returns (emails_sent, emails_failed) for the deletion summary email.
        """
        user_id = user_info["user_id"]
        excess_bytes = user_info["excess_bytes"]

        # Check subscription status and active workflows in one session
        with session_scope() as db:
            if SubscriptionService.get_user_subscription(db, user_id):
                logger.info(
                    f"User {user_id} has active subscription, skipping deletion"
                )
                SubscriptionService.mark_deletion_processed(db, user_id)
                return 0, 0

            if SubscriptionService.has_active_workflows(db, user_id):
                logger.info(f"User {user_id} has active workflows, deferring deletion")
                return 0, 0

            # Read deletion priority from UserPreferences
            priority = SubscriptionService.get_deletion_priority(db, user_id)

            # Fetch DB data while session is open, close before S3 ops
            experiments, workspace_inputs = cls._fetch_user_data(db, user_id)

        if not experiments and not workspace_inputs:
            logger.info(f"No deletable data found for user {user_id}")
            with session_scope() as db:
                SubscriptionService.mark_deletion_processed(db, user_id)
            return 0, 0

        # Re-check current storage to get fresh excess_bytes (may have
        # changed since the eligibility query ran)
        with session_scope() as db:
            fresh_excess = SubscriptionService.get_current_excess_bytes(db, user_id)
        if fresh_excess is not None and fresh_excess <= 0:
            logger.info(f"User {user_id} storage now within quota, skipping deletion")
            with session_scope() as db:
                SubscriptionService.mark_deletion_processed(db, user_id)
            return 0, 0
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
            with session_scope() as db:
                SubscriptionService.mark_deletion_processed(db, user_id)
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

        # Per-run summary: sent whenever bytes were actually freed, including
        # aborted (re-subscribed mid-run) and partial-failure runs.
        if (
            result["bytes_deleted"] > 0
            and EmailService.is_enabled()
            and user_info.get("active")
            and user_info.get("email")
        ):
            ok = await asyncio.to_thread(
                EmailService.send_deletion_summary_email,
                user_info["email"],
                result["bytes_deleted"],
                result["succeeded"],
            )
            return (1, 0) if ok else (0, 1)
        return 0, 0

    @classmethod
    async def _send_grace_notifications(cls) -> Tuple[int, int]:
        """Send grace-period start/reminder emails. Returns (sent, failed).

        Claim-then-send: the notification stamp is claimed with a conditional
        UPDATE before sending, so overlapping job instances cannot double-send.
        On send failure the claim is rolled back and retried on the next run.
        """
        if not EmailService.is_enabled():
            logger.info("Email notifications disabled, skipping grace notifications")
            return 0, 0

        with session_scope() as db:
            pending = SubscriptionService.get_users_for_grace_notifications(db)

        sent = 0
        failed = 0
        for item in pending:
            claimed = {}
            try:
                kind = "start" if item["needs_start"] else "reminder"
                with session_scope() as db:
                    stamp = SubscriptionService.claim_grace_notification(
                        db, item["user_id"], item["expiration"], kind
                    )
                    if stamp:
                        claimed[kind] = stamp
                        # A same-run reminder adds nothing after a (late) start
                        # email: claim it too, but send only the start email.
                        if kind == "start" and item["needs_reminder"]:
                            reminder_stamp = (
                                SubscriptionService.claim_grace_notification(
                                    db, item["user_id"], item["expiration"], "reminder"
                                )
                            )
                            if reminder_stamp:
                                claimed["reminder"] = reminder_stamp
                if not claimed:
                    continue

                if kind == "start":
                    deletion_not_before = max(
                        item["grace_end"],
                        get_current_datetime()
                        + timedelta(days=ExpirationDeletion.MIN_NOTICE_DAYS),
                    )
                    ok = await asyncio.to_thread(
                        EmailService.send_grace_period_start_email,
                        item["email"],
                        item["grace_end"],
                        item["storage_usage_bytes"],
                        deletion_not_before,
                    )
                else:
                    ok = await asyncio.to_thread(
                        EmailService.send_grace_period_reminder_email,
                        item["email"],
                        item["grace_end"],
                        item["storage_usage_bytes"],
                    )

                if ok:
                    sent += 1
                else:
                    failed += 1
                    cls._clear_claims(item["user_id"], claimed)
            except Exception as e:
                # A claimed-but-unsent stamp would satisfy the notice gate
                # without any email having gone out, so roll it back.
                logger.error(
                    f"Grace notification failed for user {item.get('user_id')}: {e}",
                    exc_info=True,
                )
                failed += 1
                cls._clear_claims(item.get("user_id"), claimed)

        if sent or failed:
            logger.info(f"Grace notifications: {sent} sent, {failed} failed")
        return sent, failed

    @staticmethod
    def _clear_claims(user_id, claimed: dict):
        """Best-effort rollback of claimed notification stamps."""
        if not claimed:
            return
        try:
            with session_scope() as db:
                for kind, stamp in claimed.items():
                    SubscriptionService.clear_grace_notification(
                        db, user_id, kind, stamp
                    )
        except Exception as e:
            logger.error(f"Failed to clear notification claim for {user_id}: {e}")

    @classmethod
    def _update_data_flags(
        cls,
        tier: _DeletionTier,
        item: Union[_ExperimentInfo, _WorkspaceInputInfo],
        bytes_deleted: int,
    ):
        """Clear data-existence flags on experiment records after tier deletion.

        For INPUTS tier, all experiments in the workspace are marked
        ``has_inputs=False`` even if deletion stopped early (byte target
        reached).  This is intentional: workspace inputs are shared across
        experiments, so partial deletion makes the input directory unreliable
        for every experiment in that workspace.
        """
        if bytes_deleted <= 0:
            return

        with session_scope() as db:
            if tier == _DeletionTier.INTERMEDIATES:
                db.query(ExperimentRecord).filter(
                    ExperimentRecord.uid == item.uid,
                ).update(
                    {"has_intermediates": False},
                    synchronize_session="fetch",
                )
            elif tier == _DeletionTier.OUTPUTS:
                db.query(ExperimentRecord).filter(
                    ExperimentRecord.uid == item.uid,
                ).update(
                    {"has_outputs": False, "has_nwb": False},
                    synchronize_session="fetch",
                )
            elif tier == _DeletionTier.INPUTS:
                db.query(ExperimentRecord).filter(
                    ExperimentRecord.workspace_id == item.workspace_id,
                ).update(
                    {"has_inputs": False},
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
                    # Only consider experiments that still have deletable data
                    db_or(
                        ExperimentRecord.has_intermediates == True,  # noqa: E712
                        ExperimentRecord.has_outputs == True,  # noqa: E712
                        ExperimentRecord.has_inputs == True,  # noqa: E712
                    ),
                )
                # MySQL has no NULLS FIRST; ascending order already puts NULL first
                .order_by(ExperimentRecord.analyzed_at.asc())
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
        priority: DeletionPriority,
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
        }
        accumulated = 0
        units_processed = 0

        # Determine tier ordering based on priority
        if priority == DeletionPriority.PRESERVE_INPUTS:
            tier_order = [
                _DeletionTier.INTERMEDIATES,
                _DeletionTier.OUTPUTS,
                _DeletionTier.INPUTS,
            ]
        else:
            # Default: preserve_outputs
            tier_order = [
                _DeletionTier.INTERMEDIATES,
                _DeletionTier.INPUTS,
                _DeletionTier.OUTPUTS,
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

            for tier in tier_order:
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
                            user_id,
                            bucket,
                            tier,
                            item,
                            run_id,
                            remaining_bytes=target_bytes - accumulated,
                        )
                        result["succeeded"] += 1
                        result["bytes_deleted"] += bytes_deleted
                        accumulated += bytes_deleted

                        cls._update_data_flags(tier, item, bytes_deleted)
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
        remaining_bytes: int,
    ) -> int:
        """
        Enumerate S3 keys for a single unit and delete them one at a time.

        Each file is deleted from S3, then the corresponding local copy is
        removed, before moving to the next file. Stops early once
        remaining_bytes of data has been freed.

        Returns total bytes deleted.
        """
        if tier == _DeletionTier.INPUTS:
            prefix = S3StorageController.make_s3_input_prefix(
                workspace_id=str(item.workspace_id)
            )
            local_base = join_filepath([DIRPATH.INPUT_DIR, str(item.workspace_id)])
            keys_with_sizes = await cls._list_all_objects(bucket, prefix)
        else:
            prefix = S3StorageController.make_s3_output_prefix(
                workspace_id=str(item.workspace_id),
                unique_id=item.uid,
            )
            local_base = join_filepath(
                [DIRPATH.OUTPUT_DIR, str(item.workspace_id), item.uid]
            )
            keys_with_sizes = await cls._list_experiment_objects(bucket, prefix, tier)

        if not keys_with_sizes:
            return 0

        # Build idempotency key
        unit_id = item.uid if hasattr(item, "uid") else f"ws_{item.workspace_id}"
        idempotency_key = f"exp_del_{run_id}_{item.workspace_id}_{unit_id}_{tier.value}"

        total_deleted_bytes = 0

        for s3_key, size in keys_with_sizes:
            # Delete from S3
            try:
                await bucket.delete_objects(
                    Delete={"Objects": [{"Key": s3_key}], "Quiet": True}
                )
            except Exception as e:
                if is_no_such_bucket_error(e):
                    logger.warning(
                        f"Bucket does not exist, stopping deletion for {prefix}"
                    )
                    break
                logger.error(f"S3 deletion failed for key {s3_key}: {e}")
                continue  # Skip this file, try next

            total_deleted_bytes += size

            # Delete corresponding local file
            relative_path = s3_key[len(prefix) :]
            if relative_path:
                local_path = join_filepath([local_base, relative_path])
                cls._delete_local_file(local_path, user_id)

            # Check if we've freed enough
            if total_deleted_bytes >= remaining_bytes:
                break

        if total_deleted_bytes > 0:
            decrement_storage_idempotent(user_id, total_deleted_bytes, idempotency_key)

        logger.info(
            f"Deleted {total_deleted_bytes} bytes for user {user_id}, "
            f"tier={tier.value}, prefix={prefix}"
        )

        return total_deleted_bytes

    @staticmethod
    def _delete_local_file(local_path: str, user_id: int):
        """Delete a single local file. Best-effort — logs but does not raise."""
        try:
            if os.path.isfile(local_path):
                os.remove(local_path)
            elif os.path.isdir(local_path):
                shutil.rmtree(local_path)
        except Exception as e:
            logger.warning(
                f"Failed to delete local file for user {user_id}: " f"{local_path}: {e}"
            )

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
                keys_with_sizes.append((obj.key, (await obj.size) or 0))

        return keys_with_sizes

    @classmethod
    async def _list_all_objects(cls, bucket, prefix: str) -> List[Tuple[str, int]]:
        """List all S3 objects under a prefix. Returns list of (key, size) tuples."""
        keys_with_sizes: List[Tuple[str, int]] = []

        async for obj in bucket.objects.filter(Prefix=prefix):
            keys_with_sizes.append((obj.key, (await obj.size) or 0))

        return keys_with_sizes

    @staticmethod
    def _has_active_subscription(user_id: int) -> bool:
        """Check if user has an active (unexpired) subscription."""
        with session_scope() as db:
            return SubscriptionService.get_user_subscription(db, user_id) is not None

    @classmethod
    def _publish_metrics(
        cls,
        processed: int,
        errors: int,
        emails_sent: int = 0,
        email_failures: int = 0,
    ):
        """Publish job metrics to CloudWatch.

        Wraps the sync boto3 call in run_in_executor to avoid blocking
        the async event loop.
        """

        def _put_metrics():
            import boto3

            now = get_current_datetime()
            cloudwatch = boto3.client("cloudwatch")
            cloudwatch.put_metric_data(
                Namespace=(
                    f"{ExpirationDeletion.METRIC_NAMESPACE_BASE}"
                    f"/{os.environ.get('ENV_PREFIX', 'default')}"
                ),
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
                    {
                        "MetricName": ExpirationDeletion.METRIC_EMAILS_SENT,
                        "Value": emails_sent,
                        "Unit": "Count",
                        "Timestamp": now,
                    },
                    {
                        "MetricName": ExpirationDeletion.METRIC_EMAIL_FAILURES,
                        "Value": email_failures,
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
