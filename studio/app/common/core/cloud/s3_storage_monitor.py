"""
S3 Storage Monitoring Utility for Cloud Alerts.
Monitors S3 storage usage and generates alerts when thresholds are exceeded.
"""
import asyncio
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

import boto3

from studio.app.common.core.cloud.cloud_utils import (
    get_user_storage_usage,
    update_user_storage_usage,
)
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.storage.s3_storage_controller import S3StorageController
from studio.app.common.core.users import crud_users
from studio.app.common.db.database import session_scope
from studio.app.common.models.subscription import (
    PlanName,
    StorageSize,
    SubscriptionStatus,
    SubscriptionType,
)

logger = AppLogger.get_logger()


class S3StorageMonitor:
    """
    Monitors S3 storage usage for users and generates alerts
    when thresholds are exceeded.
    """

    def __init__(self, bucket_name: str):
        self.bucket_name = bucket_name
        self.s3_controller = S3StorageController(bucket_name)

        # Alert thresholds (percentage of quota)
        self.CRITICAL_THRESHOLD = 90  # 90%
        self.DANGER_THRESHOLD = 100  # 100%

        # Storage quotas by plan (in bytes)
        # These should match the values in your subscription plan features
        self.PLAN_QUOTAS = {
            SubscriptionType.FREE.value: 5 * StorageSize.GB,  # 5GB
            SubscriptionType.PREMIUM.value: 100 * StorageSize.GB,  # 100GB
        }

    async def get_user_s3_storage_size(self, user_id: int) -> int:
        """
        Calculate total storage size for a user's S3 data across all their workspaces.

        Args:
            user_id: The user ID to check storage for

        Returns:
            Total storage size in bytes
        """
        total_size = 0

        try:
            # Get all workspaces the user has access to
            from sqlmodel import or_, select

            from studio.app.common import models as common_model
            from studio.app.common.db.database import session_scope

            with session_scope() as db:
                workspaces_query = (
                    select(common_model.Workspace.id)
                    .join(
                        common_model.WorkspacesShareUser,
                        common_model.Workspace.id
                        == common_model.WorkspacesShareUser.workspace_id,
                        isouter=True,
                    )
                    .filter(
                        common_model.Workspace.deleted.is_(False),
                        or_(
                            common_model.WorkspacesShareUser.user_id == user_id,
                            common_model.Workspace.user_id == user_id,
                        ),
                    )
                )
                workspace_ids = db.execute(workspaces_query).scalars().all()

            logger.debug(
                f"Checking S3 storage for user {user_id} across "
                f"{len(workspace_ids)} workspaces"
            )

            # Create sync S3 client for boto3 operations
            s3_client = boto3.client("s3")

            # Check both input and output directories for each workspace
            for workspace_id in workspace_ids:
                prefixes = [
                    f"app/studio_data/"
                    f"{S3StorageController.S3_INPUT_DIR}/{workspace_id}/",
                    f"app/studio_data/"
                    f"{S3StorageController.S3_OUTPUT_DIR}/{workspace_id}/",
                ]

                for prefix in prefixes:
                    try:
                        logger.debug(f"Scanning prefix: {prefix}")
                        # Use paginator to handle large number of objects
                        paginator = s3_client.get_paginator("list_objects_v2")
                        page_iterator = paginator.paginate(
                            Bucket=self.bucket_name, Prefix=prefix
                        )

                        prefix_size = 0
                        object_count = 0
                        for page in page_iterator:
                            if "Contents" in page:
                                for obj in page["Contents"]:
                                    object_size = obj["Size"]
                                    total_size += object_size
                                    prefix_size += object_size
                                    object_count += 1

                        logger.debug(
                            f"Workspace {workspace_id} - Prefix {prefix}: "
                            f"{object_count} objects, {prefix_size:,} bytes"
                        )

                    except Exception as e:
                        logger.warning(f"Failed to get size for prefix {prefix}: {e}")
                        continue

        except Exception as e:
            logger.error(f"Failed to calculate S3 storage size for user {user_id}: {e}")
            return 0

        logger.info(
            f"Calculated S3 storage size for user {user_id}: {total_size:,} bytes"
        )
        return total_size

    def calculate_storage_alert_level(
        self, storage_usage_percent: float
    ) -> Optional[str]:
        """
        Determine alert level based on usage percentage.

        Args:
            storage_usage_percent: Storage usage as percentage of quota

        Returns:
            Alert level string or None if no alert needed
        """
        if storage_usage_percent >= self.DANGER_THRESHOLD:
            return "danger"
        elif storage_usage_percent >= self.CRITICAL_THRESHOLD:
            return "critical"
        return None

    async def check_user_storage_alerts(self, user_id: int) -> Optional[Dict]:
        """
        Check storage usage for a specific user and return alert info if needed.

        Args:
            user_id: User ID to check

        Returns:
            Dict with alert information or None if no alert needed
        """
        try:
            # Get current S3 usage
            current_s3_usage = await self.get_user_s3_storage_size(user_id)

            # Update database with current usage
            update_success = update_user_storage_usage(user_id, current_s3_usage)
            if not update_success:
                logger.warning(f"Failed to update storage usage for user {user_id}")

            # Get user's storage quota from database or calculate based on subscription
            storage_info = get_user_storage_usage(user_id)

            # If no storage info exists, try to determine quota from user's subscription
            if not storage_info:
                logger.debug(
                    f"No storage usage record found for user {user_id}, "
                    "checking subscription"
                )
                with session_scope() as db:
                    user_with_context = await crud_users.get_user_with_context(
                        db, user_id
                    )

                if user_with_context:
                    subscription_plan = user_with_context.subscription_type
                    storage_quota = self.PLAN_QUOTAS.get(
                        subscription_plan, self.PLAN_QUOTAS[SubscriptionType.FREE.value]
                    )
                    logger.info(
                        f"Using plan-based quota for user {user_id} "
                        f"({subscription_plan}): {storage_quota} bytes"
                    )
                else:
                    logger.warning(
                        f"No storage or subscription information "
                        f"found for user {user_id}"
                    )
                    return None
            else:
                storage_quota = storage_info["storage_quota_bytes"]
                if storage_quota <= 0:
                    # Fallback to plan-based quota if database has invalid data
                    with session_scope() as db:
                        user_with_context = await crud_users.get_user_with_context(
                            db, user_id
                        )

                    if user_with_context:
                        subscription_plan = user_with_context.subscription_type
                        storage_quota = self.PLAN_QUOTAS.get(
                            subscription_plan,
                            self.PLAN_QUOTAS[SubscriptionType.FREE.value],
                        )
                        logger.warning(
                            f"Invalid quota in database for user {user_id}, "
                            f"using plan-based quota: {storage_quota}"
                        )
                    else:
                        logger.warning(
                            f"Invalid quota limit for user {user_id}: {storage_quota}"
                        )
                        return None

            storage_usage_percent = (current_s3_usage / storage_quota) * 100
            alert_level = self.calculate_storage_alert_level(storage_usage_percent)

            if alert_level:
                return {
                    "user_id": user_id,
                    "alert_level": alert_level,
                    "storage_usage_bytes": current_s3_usage,
                    "storage_quota_bytes": storage_quota,
                    "storage_usage_percent": round(storage_usage_percent, 2),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

        except Exception as e:
            logger.error(f"Failed to check storage alerts for user {user_id}: {e}")

        return None

    async def check_all_users_storage_alerts(self) -> List[Dict]:
        """
        Check storage usage for all active users and return alerts.

        Returns:
            List of alert dictionaries
        """
        alerts = []

        try:
            # Get all active subscription users using crud_users
            with session_scope() as db:
                all_users = await crud_users.list_user(
                    db,
                    limit=1000,  # Large limit to get all users
                    skip=0,
                    user_id=None,
                    search=None,
                    email=None,
                )

                # Filter for users with active subscriptions
                active_users = []
                for user in all_users:
                    if (
                        user.subscription_status
                        and user.subscription_status != SubscriptionStatus.FREE.value
                    ):
                        active_users.append(
                            {
                                "id": user.id,
                                "name": user.name,
                                "email": str(user.email),
                                "subscription_plan": user.subscription_type,
                                "plan_name": user.subscription_plan_name
                                or PlanName.FREE.value,
                                "status": user.subscription_status
                                or SubscriptionStatus.FREE.value,
                            }
                        )

            if not active_users:
                logger.info("No active users found for storage monitoring")
                return alerts

            logger.info(f"Checking storage alerts for {len(active_users)} active users")

            # Check each user's storage
            for user in active_users:
                user_id = user["id"]
                alert = await self.check_user_storage_alerts(user_id)

                if alert:
                    # Add user information to alert
                    alert.update(
                        {
                            "user_name": user["name"],
                            "user_email": user["email"],
                            "subscription_plan": user["subscription_plan"],
                            "plan_name": user.get("plan_name", PlanName.UNKNOWN.value),
                            "subscription_status": user.get("status", "unknown"),
                        }
                    )
                    alerts.append(alert)
                    logger.info(
                        f"Storage alert for user {user['name']} "
                        f"({user['subscription_plan']}): {alert['alert_level']} "
                        f"at {alert['storage_usage_percent']}%"
                    )

        except Exception as e:
            logger.error(f"Failed to check storage alerts for all users: {e}")

        return alerts

    def format_bytes(self, bytes_size: int) -> str:
        """Format bytes into human readable format."""
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if bytes_size < StorageSize.KB:
                return f"{bytes_size:.1f} {unit}"
            bytes_size /= StorageSize.KB
        return f"{bytes_size:.1f} PB"

    def get_alert_message(self, alert: Dict) -> str:
        """
        Generate human-readable alert message.

        Args:
            alert: Alert dictionary

        Returns:
            Formatted alert message
        """
        usage_formatted = self.format_bytes(alert["storage_usage_bytes"])
        quota_formatted = self.format_bytes(alert["storage_quota_bytes"])
        percentage = alert["storage_usage_percent"]

        level_messages = {
            "critical": f"  Storage usage is at {percentage}% "
            f"({usage_formatted} of {quota_formatted}) - approaching limit",
            "danger": f" Storage quota exceeded at {percentage}% "
            f"({usage_formatted} of {quota_formatted}) - immediate action required",
        }

        return level_messages.get(alert["alert_level"], f"Storage usage: {percentage}%")

    def ensure_user_storage_record(self, user_id: int, subscription_plan: str) -> bool:
        """
        Ensure user has a storage usage record with appropriate quota for their plan.

        Args:
            user_id: User ID
            subscription_plan: User's subscription plan ('free' or 'paid')

        Returns:
            True if record exists or was created successfully
        """
        try:
            # Check if user already has storage record
            storage_info = get_user_storage_usage(user_id)
            if storage_info:
                return True

            # Create new storage record with plan-based quota
            storage_quota_bytes = self.PLAN_QUOTAS.get(
                subscription_plan, self.PLAN_QUOTAS[SubscriptionType.FREE.value]
            )

            from sqlmodel import select

            from studio.app.common.db.database import session_scope
            from studio.app.common.models import UserStorageUsage

            with session_scope() as db:
                # Check if storage record exists
                existing_usage = db.exec(
                    select(UserStorageUsage).where(UserStorageUsage.user_id == user_id)
                ).first()

                if existing_usage:
                    # Update quota if needed
                    existing_usage.storage_quota_bytes = storage_quota_bytes
                    db.add(existing_usage)
                else:
                    # Create new storage record
                    new_storage = UserStorageUsage(
                        user_id=user_id,
                        storage_usage_bytes=0,
                        storage_quota_bytes=storage_quota_bytes,
                    )
                    db.add(new_storage)

            logger.info(
                f"Created storage record for user {user_id} with {subscription_plan} "
                f"plan quota: {storage_quota_bytes} bytes"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to ensure storage record for user {user_id}: {e}")
            return False


async def monitor_storage_and_generate_alerts(bucket_name: str) -> List[Dict]:
    """
    Convenience function to monitor storage and generate alerts.

    Args:
        bucket_name: S3 bucket name to monitor

    Returns:
        List of alert dictionaries
    """
    monitor = S3StorageMonitor(bucket_name)
    return await monitor.check_all_users_storage_alerts()


# Example usage for testing
if __name__ == "__main__":

    async def test_monitor():
        bucket_name = os.environ.get("S3_BUCKET_NAME", "test-bucket")
        alerts = await monitor_storage_and_generate_alerts(bucket_name)

        if alerts:
            print(f"Found {len(alerts)} storage alerts:")
            for alert in alerts:
                monitor = S3StorageMonitor(bucket_name)
                message = monitor.get_alert_message(alert)
                print(f"- {alert['user_name']} ({alert['user_email']}): {message}")
        else:
            print("No storage alerts found")

    asyncio.run(test_monitor())
