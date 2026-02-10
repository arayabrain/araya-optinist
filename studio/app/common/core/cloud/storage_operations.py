"""
Storage operation functions for idempotent tracking and reconciliation.

Extracted from cloud_utils.py to reduce module size and improve cohesion.
"""
from datetime import datetime, timedelta, timezone

from sqlmodel import select

from studio.app.common.core.cloud.storage_tracking import (
    decrement_user_storage,
    increment_user_storage,
)
from studio.app.common.core.logger import AppLogger
from studio.app.common.db.database import session_scope
from studio.app.common.models.subscription import (
    StorageOperation,
    StorageOperationStatus,
    StorageOperationType,
)

logger = AppLogger.get_logger()

STALE_PENDING_THRESHOLD_MINUTES = 30


def increment_storage_idempotent(
    user_id: int,
    bytes_delta: int,
    idempotency_key: str,
) -> bool:
    """
    Idempotent storage increment to prevent double-counting.
    Uses operation tracking to prevent drift.

    Args:
        user_id: User ID to update
        bytes_delta: Number of bytes to add
        idempotency_key: Unique key for dedup

    Returns:
        True if operation completed (or was already done),
        False on failure
    """
    if bytes_delta <= 0:
        return True

    try:
        with session_scope() as db:
            existing = db.execute(
                select(StorageOperation).where(
                    StorageOperation.idempotency_key == idempotency_key,
                    StorageOperation.status == StorageOperationStatus.COMPLETED.value,
                )
            ).first()

            if existing:
                logger.debug(
                    "Idempotent increment already done " f"for key {idempotency_key}"
                )
                return True

            pending = db.execute(
                select(StorageOperation).where(
                    StorageOperation.idempotency_key == idempotency_key,
                    StorageOperation.status == StorageOperationStatus.PENDING.value,
                )
            ).first()

            if pending:
                logger.warning(
                    "Pending operation exists " f"for key {idempotency_key}, skipping"
                )
                return False

            operation = StorageOperation(
                user_id=user_id,
                idempotency_key=idempotency_key,
                operation_type=(StorageOperationType.INCREMENT.value),
                bytes_delta=bytes_delta,
                status=StorageOperationStatus.PENDING.value,
            )
            db.add(operation)
            db.commit()

            operation_id = operation.id

        success = increment_user_storage(user_id, bytes_delta)

        with session_scope() as db:
            op = db.get(StorageOperation, operation_id)
            if op:
                if success:
                    op.status = StorageOperationStatus.COMPLETED.value
                    op.completed_at = datetime.now(timezone.utc)
                else:
                    op.status = StorageOperationStatus.FAILED.value
                    op.error_message = "Increment operation failed"
                db.commit()

        return success

    except Exception as e:
        logger.error(
            f"Failed idempotent increment for user {user_id}, "
            f"key {idempotency_key}: {e}"
        )
        return False


def decrement_storage_idempotent(
    user_id: int,
    bytes_delta: int,
    idempotency_key: str,
) -> bool:
    """
    Idempotent storage decrement to prevent double-subtraction.
    Uses operation tracking to prevent drift.

    Args:
        user_id: User ID to update
        bytes_delta: Number of bytes to remove (positive value)
        idempotency_key: Unique key for dedup

    Returns:
        True if operation completed (or was already done),
        False on failure
    """
    if bytes_delta <= 0:
        return True

    try:
        with session_scope() as db:
            existing = db.execute(
                select(StorageOperation).where(
                    StorageOperation.idempotency_key == idempotency_key,
                    StorageOperation.status == StorageOperationStatus.COMPLETED.value,
                )
            ).first()

            if existing:
                logger.debug(
                    "Idempotent decrement already done " f"for key {idempotency_key}"
                )
                return True

            pending = db.execute(
                select(StorageOperation).where(
                    StorageOperation.idempotency_key == idempotency_key,
                    StorageOperation.status == StorageOperationStatus.PENDING.value,
                )
            ).first()

            if pending:
                logger.warning(
                    "Pending operation exists " f"for key {idempotency_key}, skipping"
                )
                return False

            operation = StorageOperation(
                user_id=user_id,
                idempotency_key=idempotency_key,
                operation_type=(StorageOperationType.DECREMENT.value),
                bytes_delta=bytes_delta,
                status=StorageOperationStatus.PENDING.value,
            )
            db.add(operation)
            db.commit()

            operation_id = operation.id

        success = decrement_user_storage(user_id, bytes_delta)

        with session_scope() as db:
            op = db.get(StorageOperation, operation_id)
            if op:
                if success:
                    op.status = StorageOperationStatus.COMPLETED.value
                    op.completed_at = datetime.now(timezone.utc)
                else:
                    op.status = StorageOperationStatus.FAILED.value
                    op.error_message = "Decrement operation failed"
                db.commit()

        return success

    except Exception as e:
        logger.error(
            f"Failed idempotent decrement for user {user_id}, "
            f"key {idempotency_key}: {e}"
        )
        return False


def get_pending_storage_operations(user_id: int) -> list:
    """Get pending storage operations for reconciliation."""
    try:
        with session_scope() as db:
            result = db.execute(
                select(StorageOperation).where(
                    StorageOperation.user_id == user_id,
                    StorageOperation.status == StorageOperationStatus.PENDING.value,
                )
            ).all()
            return [row[0] for row in result] if result else []
    except Exception as e:
        logger.error("Failed to get pending operations " f"for user {user_id}: {e}")
        return []


def cleanup_old_storage_operations(days_old: int = 7) -> int:
    """Clean up completed storage operations older than
    specified days."""
    try:
        from sqlalchemy import delete

        cutoff = datetime.now(timezone.utc) - timedelta(days=days_old)

        with session_scope() as db:
            result = db.execute(
                delete(StorageOperation).where(
                    StorageOperation.status == StorageOperationStatus.COMPLETED.value,
                    StorageOperation.completed_at < cutoff,
                )
            )
            deleted_count = result.rowcount
            db.commit()

            if deleted_count > 0:
                logger.info(
                    f"Cleaned up {deleted_count} old storage "
                    f"operations (older than {days_old} days)"
                )
            return deleted_count

    except Exception as e:
        logger.error(f"Failed to cleanup old storage operations: {e}")
        return 0


def process_failed_storage_operations(
    max_retries: int | None = None, batch_size: int = 100
) -> int:
    """
    Process failed storage operations with retry logic.

    Finds failed decrement operations and retries them.
    Operations exceeding max_retries are logged but not retried.
    """
    from studio.app.common.models.subscription import STORAGE_OPERATION_MAX_RETRIES

    if max_retries is None:
        max_retries = STORAGE_OPERATION_MAX_RETRIES

    retried_count = 0

    try:
        from studio.app.common.models import UserStorageUsage

        with session_scope() as db:
            failed_ops_with_usage = db.execute(
                select(StorageOperation, UserStorageUsage)
                .outerjoin(
                    UserStorageUsage,
                    StorageOperation.user_id == UserStorageUsage.user_id,
                )
                .where(
                    StorageOperation.status == StorageOperationStatus.FAILED.value,
                    StorageOperation.retry_count < max_retries,
                )
                .order_by(StorageOperation.created_at)
                .limit(batch_size)
            ).all()

            for op, usage_record in failed_ops_with_usage:
                try:
                    op.retry_count = op.retry_count + 1

                    if not usage_record:
                        logger.warning(
                            "No storage record for user " f"{op.user_id}, skipping"
                        )
                        continue

                    if op.operation_type == StorageOperationType.DECREMENT.value:
                        new_usage = max(
                            0,
                            usage_record.storage_usage_bytes - op.bytes_delta,
                        )
                        usage_record.storage_usage_bytes = new_usage
                        usage_record.last_updated = datetime.now(timezone.utc)

                    op.status = StorageOperationStatus.COMPLETED.value
                    op.completed_at = datetime.now(timezone.utc)
                    op.error_message = None

                    db.commit()
                    retried_count += 1
                    logger.info(
                        f"Retried storage operation {op.id} " f"for user {op.user_id}"
                    )

                except Exception as retry_error:
                    op.error_message = str(retry_error)[:200]
                    db.commit()
                    logger.warning(
                        "Retry failed for operation " f"{op.id}: {retry_error}"
                    )

            if retried_count > 0:
                logger.info(
                    f"Successfully retried {retried_count} " "storage operations"
                )

            return retried_count

    except Exception as e:
        logger.error("Failed to process failed storage operations: " f"{e}")
        return 0


def process_stale_pending_operations(
    max_retries: int = 3, batch_size: int = 50
) -> dict:
    """
    Process storage operations stuck in PENDING too long (recovery).

    Operations can become stuck if the app crashes between creating
    the pending record and completing the operation.
    """
    result = {"processed": 0, "succeeded": 0, "failed": 0}
    cutoff = datetime.now(timezone.utc) - timedelta(
        minutes=STALE_PENDING_THRESHOLD_MINUTES
    )

    try:
        with session_scope() as db:
            stale_ops = db.execute(
                select(StorageOperation)
                .where(
                    StorageOperation.status == StorageOperationStatus.PENDING.value,
                    StorageOperation.created_at < cutoff,
                )
                .order_by(StorageOperation.created_at)
                .limit(batch_size)
            ).all()

            for (op,) in stale_ops:
                result["processed"] += 1
                current_retries = op.retry_count or 0

                if current_retries >= max_retries:
                    op.status = StorageOperationStatus.FAILED.value
                    op.error_message = "Exceeded max retries for stale pending"
                    result["failed"] += 1
                    logger.warning(
                        f"Stale operation {op.idempotency_key} " "exceeded max retries"
                    )
                    continue

                try:
                    op.retry_count = current_retries + 1

                    if op.operation_type == StorageOperationType.INCREMENT.value:
                        success = increment_user_storage(op.user_id, op.bytes_delta)
                    else:
                        success = decrement_user_storage(op.user_id, op.bytes_delta)

                    if success:
                        op.status = StorageOperationStatus.COMPLETED.value
                        op.completed_at = datetime.now(timezone.utc)
                        result["succeeded"] += 1
                        logger.info(
                            "Recovered stale operation " f"{op.idempotency_key}"
                        )
                    else:
                        op.status = StorageOperationStatus.FAILED.value
                        op.error_message = "Recovery retry returned false"
                        result["failed"] += 1
                        logger.warning(
                            "Failed to recover stale operation " f"{op.idempotency_key}"
                        )
                except Exception as e:
                    op.error_message = str(e)[:200]
                    result["failed"] += 1
                    logger.error(
                        "Error recovering operation " f"{op.idempotency_key}: {e}"
                    )

            db.commit()

        if result["processed"] > 0:
            logger.info(
                f"Processed {result['processed']} stale pending "
                f"operations: {result['succeeded']} succeeded, "
                f"{result['failed']} failed"
            )
        return result

    except Exception as e:
        logger.error("Failed to process stale pending operations: " f"{e}")
        return result
