"""
Cloud utilities for user context and subscription management.
"""
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from sqlmodel import select

from studio.app.common.core.logger import AppLogger
from studio.app.common.db.database import session_scope
from studio.app.common.models import SubscriptionPlans
from studio.app.common.models import User as UserModel
from studio.app.common.models import UserStorageUsage, UserSubscription
from studio.app.common.models.subscription import (
    PlanName,
    StorageQuota,
    StorageSize,
    SubscriptionLifecycleStatus,
    SubscriptionPeriods,
    SubscriptionStatus,
    SubscriptionType,
)

logger = AppLogger.get_logger()


def _get_fallback_storage_quota(user_id: int) -> Dict[str, Any]:
    """
    Get fallback storage quota when storage usage table doesn't exist.
    Tries to determine quota based on user's subscription plan.
    """
    try:
        # Try to get user's subscription plan to determine appropriate quota
        # Simple synchronous query for just the subscription plan
        with session_scope() as db:
            statement = (
                select(SubscriptionPlans.name.label("plan_name"))
                .select_from(UserModel)
                .outerjoin(
                    UserSubscription,
                    (UserModel.id == UserSubscription.user_id)
                    & (UserSubscription.expiration > datetime.now()),
                )
                .outerjoin(
                    SubscriptionPlans, UserSubscription.plan_id == SubscriptionPlans.id
                )
                .where(UserModel.id == user_id, UserModel.active.is_(True))
            )
            result = db.execute(statement).first()

        if result and result.plan_name:
            plan_name = result.plan_name
            subscription_type = (
                SubscriptionType.PREMIUM.value
                if plan_name == PlanName.PREMIUM.value
                else SubscriptionType.FREE.value
            )
        else:
            plan_name = PlanName.FREE.value
            subscription_type = SubscriptionType.FREE.value

        # Set quotas based on Subscription Type
        if subscription_type == SubscriptionType.PREMIUM.value:
            default_quota_bytes = StorageQuota.PREMIUM * StorageSize.GB  # 100GB
            logger.info(
                f"Using paid plan quota for user {user_id} ({plan_name}): "
                f"{StorageQuota.PREMIUM}GB"
            )
        else:
            default_quota_bytes = StorageQuota.FREE * StorageSize.GB  # 5GB
            logger.info(
                f"Using free plan quota for user {user_id} ({plan_name}): "
                f"{StorageQuota.FREE}GB"
            )

    except Exception as e:
        logger.warning(
            f"Error determining subscription quota for user {user_id}: {e}, "
            "using free plan"
        )
        default_quota_bytes = StorageQuota.FREE * StorageSize.GB  # 5GB fallback

    return {
        "user_id": user_id,
        "storage_usage_bytes": 0,  # Unknown, will be calculated from S3
        "storage_quota_bytes": default_quota_bytes,
        "storage_usage_percent": 0.0,
        "last_updated": None,
    }


async def get_user_context_with_warnings(user_id: int) -> Optional[Dict[str, Any]]:
    """
    Get user context including subscription plan & limit warnings from database by ID.
    Returns user info with subscription details and limit warnings or None if not found.
    Note: This is an async function that includes live storage warning calculations.
    """
    try:
        # Get basic user context using crud_users
        from studio.app.common.core.users.crud_users import get_user_with_context

        with session_scope() as db:
            user = await get_user_with_context(db, user_id)

        if user:
            # Convert User object to dict format for backward compatibility
            user_context = {
                "id": user.id,
                "uid": user.uid,
                "name": user.name,
                "email": str(user.email),
                "active": True,  # get_user_with_context only returns active users
                "attributes": user.attributes,
                "subscription_plan_name": user.subscription_plan_name,
                "subscription_price": 0,  # Not available in User schema
                "subscription_status": user.subscription_status,
                "subscription_plan": user.subscription_type,
            }

            # Add limit warning information (async)
            limit_warning = await calculate_limit_warning(user_id)
            user_context["limit_warning"] = limit_warning

            logger.info(
                f"Complete user context with warnings for "
                f"user {user_id}: {user_context}"
            )
            return user_context
        else:
            return None

    except Exception as e:
        logger.error(
            f"Failed to get user context with warnings for user {user_id}: {e}"
        )
        return None


def get_user_storage_usage(user_id: int) -> Optional[Dict[str, Any]]:
    """
    Get storage usage information for a user.
    Falls back to default quota if storage table doesn't exist.
    """
    import os

    # Skip storage check during testing
    # - return safe default with subscription-aware quota
    skip_checks_value = os.environ.get("SKIP_STORAGE_CHECKS", "")
    if skip_checks_value.lower() == "true":
        logger.debug(f"Skipping storage usage lookup for user {user_id} (test mode)")
        # Get subscription-aware quota instead of hardcoded value
        fallback = _get_fallback_storage_quota(user_id)
        # Set last_updated to enable caching (avoid repeated recalculations)
        fallback["last_updated"] = datetime.now()
        return fallback

    try:
        with session_scope() as db:
            # Try to query using ORM model
            try:
                query_result = db.execute(
                    select(UserStorageUsage).where(UserStorageUsage.user_id == user_id)
                )
                result_row = query_result.first()
                storage_usage = result_row[0] if result_row else None

                if storage_usage:
                    result_dict = {
                        "user_id": storage_usage.user_id,
                        "storage_usage_bytes": storage_usage.storage_usage_bytes,
                        "storage_quota_bytes": storage_usage.storage_quota_bytes,
                        "storage_usage_percent": storage_usage.storage_usage_percent,
                        "last_updated": storage_usage.last_updated,
                    }

                    logger.info(
                        f"Retrieved storage usage for user {user_id}: "
                        f"storage_usage={result_dict.get('storage_usage_bytes')}, "
                        f"storage_quota={result_dict.get('storage_quota_bytes')}, "
                        f"storage_usage_percent="
                        f"{result_dict.get('storage_usage_percent')}%"
                    )
                    return result_dict
                else:
                    logger.warning(
                        f"No storage usage data found for user {user_id}, "
                        "using defaults"
                    )
                    return _get_fallback_storage_quota(user_id)

            except Exception as orm_error:
                logger.warning(
                    f"UserStorageUsage table not accessible: {orm_error}, "
                    "using default quota"
                )
                return _get_fallback_storage_quota(user_id)

    except Exception as e:
        logger.warning(
            f"Failed to get storage usage for user {user_id}: {e}, using defaults"
        )
        return _get_fallback_storage_quota(user_id)


def update_user_storage_usage(user_id: int, new_usage_bytes: int) -> bool:
    """
    Update storage usage for a user.
    Returns True if successful or if table doesn't exist (fallback scenario).
    """
    try:
        with session_scope() as db:
            try:
                # Try to find existing storage usage record
                query_result = db.execute(
                    select(UserStorageUsage).where(UserStorageUsage.user_id == user_id)
                )
                result_row = query_result.first()
                existing_usage = result_row[0] if result_row else None

                if existing_usage:
                    # Update existing record
                    existing_usage.storage_usage_bytes = new_usage_bytes
                    existing_usage.last_updated = datetime.now()
                    db.add(existing_usage)
                else:
                    # Need to determine quota - try to get from user's subscription
                    statement = (
                        select(SubscriptionPlans.name.label("plan_name"))
                        .select_from(UserModel)
                        .outerjoin(
                            UserSubscription,
                            (UserModel.id == UserSubscription.user_id)
                            & (UserSubscription.expiration > datetime.now()),
                        )
                        .outerjoin(
                            SubscriptionPlans,
                            UserSubscription.plan_id == SubscriptionPlans.id,
                        )
                        .where(UserModel.id == user_id, UserModel.active.is_(True))
                    )
                    result = db.execute(statement).first()

                    if result and result.plan_name == PlanName.PREMIUM.value:
                        default_quota = StorageQuota.PREMIUM * StorageSize.GB  # 100GB
                    else:
                        default_quota = StorageQuota.FREE * StorageSize.GB  # 5GB

                    # Create new record
                    new_storage_usage = UserStorageUsage(
                        user_id=user_id,
                        storage_usage_bytes=new_usage_bytes,
                        storage_quota_bytes=default_quota,
                    )
                    db.add(new_storage_usage)

                logger.info(
                    f"Updated storage usage for user {user_id}: {new_usage_bytes} bytes"
                )
                return True

            except Exception as orm_error:
                logger.warning(
                    f"UserStorageUsage table not accessible: {orm_error}, "
                    "skipping storage update"
                )
                return True  # Return True since we're in fallback mode

    except Exception as e:
        logger.warning(f"Failed to update storage usage for user {user_id}: {e}")
        return False


async def get_current_user_storage_usage(user_id: int, force_live: bool = False) -> int:
    """
    Get current storage usage for a user with hybrid caching approach.

    Args:
        user_id: User ID to check storage for
        force_live: If True, always calculate live usage (skip cache)

    Returns:
        Current storage usage in bytes
    """
    try:
        if not force_live:
            # Try database first (fast)
            storage_info = get_user_storage_usage(user_id)
            if storage_info and _is_storage_data_fresh(
                storage_info, max_age_minutes=20
            ):
                logger.info(f"Using cached storage data for user {user_id}")
                return storage_info["storage_usage_bytes"]
            else:
                logger.info(
                    f"Storage data for user {user_id} is stale or missing, "
                    f"calculating live"
                )

        # Calculate live usage
        live_usage = await _calculate_live_storage_usage(user_id)

        # Update database with fresh data
        update_user_storage_usage(user_id, live_usage)

        return live_usage

    except Exception as e:
        logger.error(f"Failed to get current storage usage for user {user_id}: {e}")
        # Fallback to database if available, otherwise 0
        storage_info = get_user_storage_usage(user_id)
        return storage_info.get("storage_usage_bytes", 0) if storage_info else 0


def _is_storage_data_fresh(storage_info: Dict, max_age_minutes: int = 60) -> bool:
    """
    Check if storage data is fresh enough to use.

    Args:
        storage_info: Storage info from database
        max_age_minutes: Maximum age in minutes to consider fresh

    Returns:
        True if data is fresh enough
    """
    try:
        last_updated = storage_info.get("last_updated")
        if not last_updated:
            return False

        # Convert to datetime if it's not already
        if isinstance(last_updated, str):
            last_updated = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))

        age_minutes = (datetime.now() - last_updated).total_seconds() / 60
        return age_minutes <= max_age_minutes

    except Exception as e:
        logger.warning(f"Failed to check storage data freshness: {e}")
        return False


async def _calculate_live_storage_usage(user_id: int) -> int:
    """
    Calculate live storage usage for a user.
    Detects S3 vs local environment and uses appropriate method.

    Args:
        user_id: User ID to check storage for

    Returns:
        Current storage usage in bytes
    """
    try:
        # Determine if we should use S3 or local storage based on environment
        import os

        from studio.app.common.core.storage.remote_storage_controller import (
            RemoteStorageType,
        )

        remote_storage_type = RemoteStorageType.get_activated_type()

        if remote_storage_type == RemoteStorageType.S3:
            # Get user-specific bucket name from user attributes
            from studio.app.common.core.users.crud_users import get_user_with_context

            with session_scope() as db:
                user = await get_user_with_context(db, user_id)
                if (
                    user
                    and user.attributes
                    and user.attributes.get("remote_bucket_name")
                ):
                    user_bucket_name = user.attributes.get("remote_bucket_name")
                else:
                    # Fallback to shared for admin or users without personal bucket
                    user_bucket_name = os.environ.get("S3_DEFAULT_BUCKET_NAME")
                    logger.warning(
                        f"User {user_id} has no personal bucket, using shared bucket: "
                        f"{user_bucket_name}"
                    )

            # Use S3 storage calculation with user's bucket
            from studio.app.common.core.cloud.s3_storage_monitor import S3StorageMonitor

            monitor = S3StorageMonitor(user_bucket_name)
            return await monitor.get_user_s3_storage_size(user_id)
        else:
            # Use local storage calculation
            return await _calculate_local_user_storage(user_id)

    except Exception as e:
        logger.error(f"Failed to calculate live storage usage for user {user_id}: {e}")
        return 0


async def _calculate_local_user_storage(user_id: int) -> int:
    """
    Calculate total local storage usage for a user across all their workspaces.

    Args:
        user_id: User ID to check storage for

    Returns:
        Total storage size in bytes
    """
    import os

    # Skip storage calculation during testing
    skip_checks_value = os.environ.get("SKIP_STORAGE_CHECKS", "")
    if skip_checks_value.lower() == "true":
        logger.debug(f"Skipping storage calculation for user {user_id} (test mode)")
        return 0

    try:
        # Get all workspaces the user has access to using shared utility
        from studio.app.common.core.workspace.workspace_services import WorkspaceService

        with session_scope() as db:
            workspace_ids = WorkspaceService.get_user_accessible_workspace_ids(
                db, user_id
            )

        # Calculate total storage from input and output folders
        total_usage = 0
        import os

        from studio.app.common.core.utils.file_reader import get_folder_size
        from studio.app.dir_path import DIRPATH

        for workspace_id in workspace_ids:
            # Add input folder size
            input_path = os.path.join(DIRPATH.INPUT_DIR, str(workspace_id))
            if os.path.exists(input_path):
                input_size = get_folder_size(input_path)
                total_usage += input_size
                logger.info(
                    f"User {user_id} workspace {workspace_id} input: {input_size} bytes"
                )

            # Add output folder size
            output_path = os.path.join(DIRPATH.OUTPUT_DIR, str(workspace_id))
            if os.path.exists(output_path):
                output_size = get_folder_size(output_path)
                total_usage += output_size

        logger.info(
            f"Calculated local storage size for user {user_id}: {total_usage:,} bytes"
        )
        return total_usage

    except Exception as e:
        logger.error(f"Failed to calculate local storage size for user {user_id}: {e}")
        return 0


async def calculate_limit_warning(user_id: int) -> Optional[Dict[str, Any]]:
    """
    Calculate limit warning based on subscription and storage status:

    Cases:
    1. Free user, no storage limit exceeded → No warning
    2. Free user, storage limit exceeded → Storage warning
    3. Premium user, storage limit exceeded → Storage warning
    4. Premium user, subscription expiring only → Subscription warning
    5. Premium user, storage exceeded + subscription expiring → Combined warning
    """
    try:
        FREE_PLAN_LIMIT_BYTES = StorageQuota.FREE * StorageSize.GB
        GRACE_PERIOD_DAYS = SubscriptionPeriods.GRACE_PERIOD_DAYS
        WARNING_PERIOD_DAYS = SubscriptionPeriods.WARNING_PERIOD_DAYS

        logger.info(f"Calculating limit warning for user {user_id}")

        with session_scope() as db:
            # Get user's current storage usage - prioritize fresh data at startup

            # First check if we have cached data (within 20 minutes)
            storage_info = get_user_storage_usage(user_id)
            if storage_info and _is_storage_data_fresh(
                storage_info, max_age_minutes=20
            ):
                current_usage_bytes = storage_info.get("storage_usage_bytes", 0)
                logger.info(
                    f"LimitWarning: Using fresh cached storage data "
                    f"for user {user_id}: {current_usage_bytes}"
                )
            else:
                # Get fresh storage data using async call
                current_usage_bytes = await get_current_user_storage_usage(
                    user_id, force_live=True
                )
                logger.info(
                    f"LimitWarning: Calculated fresh storage data "
                    f"for user {user_id}: {current_usage_bytes}"
                )

            # Use quota from already retrieved storage_info to avoid redundant DB call
            storage_quota_bytes = (
                storage_info.get("storage_quota_bytes", FREE_PLAN_LIMIT_BYTES)
                if storage_info
                else FREE_PLAN_LIMIT_BYTES
            )
            storage_quota_gb = storage_quota_bytes / StorageSize.GB

            # Step 1: Determine subscription status
            query_result = db.execute(
                select(UserSubscription)
                .where(UserSubscription.user_id == user_id)
                .order_by(UserSubscription.expiration.desc())
            )
            result_rows = query_result.all()

            logger.info(
                f"Found {len(result_rows)} subscription records for user {user_id}"
            )

            subscription_status = None
            subscription_end = None
            days_remaining = None

            if result_rows:
                last_subscription_row = result_rows[0]

                if hasattr(last_subscription_row, "__getitem__"):
                    last_subscription = last_subscription_row[0]
                else:
                    last_subscription = last_subscription_row

                # Safely access expiration with error handling
                if hasattr(last_subscription, "expiration"):
                    subscription_end = last_subscription.expiration
                    if subscription_end is None:
                        logger.error(
                            f"User {user_id} subscription has None expiration date"
                        )
                        return None
                else:
                    logger.error(
                        f"User {user_id} subscription object missing "
                        f"expiration attribute: {dir(last_subscription)}"
                    )
                    return None
                grace_end = subscription_end + timedelta(days=GRACE_PERIOD_DAYS)
                deletion_date = grace_end + timedelta(days=WARNING_PERIOD_DAYS)
                now = (
                    datetime.now(subscription_end.tzinfo)
                    if subscription_end.tzinfo
                    else datetime.now()
                )

                logger.info(f"User {user_id} subscription details:")
                logger.info(f"Subscription end: {subscription_end}")
                logger.info(f"Grace end: {grace_end}")
                logger.info(f"Deletion date: {deletion_date}")
                logger.info(f"Current time: {now}")

                if subscription_end > now:
                    subscription_status = SubscriptionLifecycleStatus.ACTIVE.value
                elif now <= grace_end:
                    subscription_status = SubscriptionLifecycleStatus.GRACE.value
                elif now <= deletion_date:
                    subscription_status = SubscriptionLifecycleStatus.WARNING.value
                    days_remaining = (deletion_date - now).days
                else:
                    subscription_status = SubscriptionLifecycleStatus.OVERDUE.value
                    days_remaining = 0

                logger.info(
                    f"Final status: {subscription_status}, "
                    f"days_remaining: {days_remaining}"
                )
            else:
                subscription_status = (
                    SubscriptionLifecycleStatus.FREE.value
                )  # Never had premium

            # Step 2: Determine storage status
            storage_exceeded = current_usage_bytes > storage_quota_bytes
            excess_bytes = max(0, current_usage_bytes - storage_quota_bytes)
            excess_gb = excess_bytes / StorageSize.GB
            current_usage_gb = current_usage_bytes / StorageSize.GB

            # Step 3: Apply the 5 cases
            logger.info(f"User {user_id} warning analysis:")
            logger.info(f"Subscription status: {subscription_status}")
            logger.info(f"Storage exceeded: {storage_exceeded}")
            logger.info(
                f"Current usage: {current_usage_gb:.2f}GB / {storage_quota_gb:.1f}GB"
            )

            # Case 1: Free user, no storage limit exceeded → No warning
            if (
                subscription_status == SubscriptionLifecycleStatus.FREE.value
                and not storage_exceeded
            ):
                logger.info(
                    f"User {user_id}: No warning needed (free plan, within limits)"
                )
                return None

            # Case 2: Free user, storage limit exceeded → Storage warning
            if (
                subscription_status == SubscriptionLifecycleStatus.FREE.value
                and storage_exceeded
            ):
                return {
                    "has_warning": True,
                    "warning_type": "storage",
                    "days_remaining": SubscriptionPeriods.STORAGE_WARNING_DAYS,
                    "excess_data_bytes": excess_bytes,
                    "excess_data_gb": round(excess_gb, 2),
                    "storage_usage_bytes": current_usage_bytes,
                    "storage_usage_gb": round(current_usage_gb, 2),
                    "storage_quota_bytes": storage_quota_bytes,
                    "storage_quota_gb": storage_quota_gb,
                    "deletion_date": (
                        datetime.now()
                        + timedelta(days=SubscriptionPeriods.STORAGE_WARNING_DAYS)
                    ).isoformat(),
                    "message": (
                        f"Your data usage ({round(current_usage_gb, 1)} GB) "
                        f"exceeds the free plan limit ({storage_quota_gb:.1f} GB). "
                        f"Please upgrade or remove {round(excess_gb, 1)} GB of data "
                        f"within {SubscriptionPeriods.STORAGE_WARNING_DAYS} days."
                    ),
                }

            # Case 3: Premium user active, storage limit exceeded → Storage warning only
            if (
                subscription_status == SubscriptionLifecycleStatus.ACTIVE.value
                and storage_exceeded
            ):
                return {
                    "has_warning": True,
                    "warning_type": "storage",
                    "days_remaining": SubscriptionPeriods.STORAGE_WARNING_DAYS,
                    "excess_data_bytes": excess_bytes,
                    "excess_data_gb": round(excess_gb, 2),
                    "storage_usage_bytes": current_usage_bytes,
                    "storage_usage_gb": round(current_usage_gb, 2),
                    "storage_quota_bytes": storage_quota_bytes,
                    "storage_quota_gb": storage_quota_gb,
                    "message": (
                        f"Your storage usage ({round(current_usage_gb, 1)} GB) is over "
                        f"the limit for your plan. You will be unable to run workflows."
                        f" Consider cleaning up unused data."
                    ),
                }

            # Cases 4 & 5: Premium user with subscription issues (warning/overdue)
            if subscription_status in [
                SubscriptionLifecycleStatus.WARNING.value,
                SubscriptionLifecycleStatus.OVERDUE.value,
            ]:
                logger.info(
                    f"User {user_id}: Creating limit warning "
                    f"(status: {subscription_status})"
                )
                warning_type = (
                    "grace"
                    if subscription_status == SubscriptionLifecycleStatus.WARNING.value
                    else "overdue"
                )

                if storage_exceeded:
                    # Case 4: Both storage and subscription issues
                    message = (
                        f"Your premium subscription expired on "
                        f"{subscription_end.strftime('%B %d, %Y')}. "
                        f"You have {days_remaining or 0} days to upgrade or remove "
                        f"{round(excess_gb, 1)} GB of data to stay "
                        f"within the free plan limit."
                    )
                else:
                    # Case 5: Subscription issue only (user within storage limits)
                    message = (
                        f"Your premium subscription expired on "
                        f"{subscription_end.strftime('%B %d, %Y')}. "
                        f"Please upgrade to maintain premium features."
                    )

                return {
                    "has_warning": True,
                    "warning_type": warning_type,
                    "days_remaining": days_remaining or 0,
                    "excess_data_bytes": excess_bytes,
                    "excess_data_gb": round(excess_gb, 2),
                    "storage_usage_bytes": current_usage_bytes,
                    "storage_usage_gb": round(current_usage_gb, 2),
                    "storage_quota_bytes": storage_quota_bytes,
                    "storage_quota_gb": storage_quota_gb,
                    "subscription_end_date": subscription_end.isoformat()
                    if subscription_end
                    else None,
                    "message": message,
                }

            # All other cases: No warning needed
            logger.info(f"User {user_id}: No warning needed (other cases)")
            return None

    except Exception as e:
        logger.error(f"Failed to calculate limit warning for user {user_id}: {e}")
        return None


class CloudDebug:
    """Debug utilities for cloud functionality."""

    @staticmethod
    def test_database_connection() -> bool:
        """
        Test database connectivity and test ORM models.
        """
        try:
            logger.info("Testing database connection with ORM...")

            with session_scope() as db:
                # Test basic connection
                query_result = db.execute(select(UserModel).where(UserModel.id == 1))
                fallback_user_row = query_result.first()

                if fallback_user_row:
                    logger.info("Database connection successful!")
                    # fallback_user_row is a SQLAlchemy Row containing UserModel
                    user_obj = fallback_user_row[0]
                    logger.info(
                        f"Found fallback user: {user_obj.name} ({user_obj.email})"
                    )
                else:
                    logger.info(
                        "Database connection successful, but no fallback user found"
                    )

                # Test subscription models
                try:
                    query_result = db.execute(select(SubscriptionPlans))
                    subscription_count = len(query_result.all())
                    logger.info(f"Subscription plans available: {subscription_count}")
                except Exception as plan_error:
                    logger.warning(
                        f"Subscription plans table not accessible: {plan_error}"
                    )

                try:
                    query_result = db.execute(
                        select(UserSubscription).where(
                            UserSubscription.expiration > datetime.now()
                        )
                    )
                    active_subscriptions = len(query_result.all())
                    logger.info(f"Active subscriptions: {active_subscriptions}")
                except Exception as sub_error:
                    logger.warning(
                        f"User subscriptions table not accessible: {sub_error}"
                    )

                try:
                    query_result = db.execute(select(UserStorageUsage))
                    storage_records = len(query_result.all())
                    logger.info(f"Storage usage records: {storage_records}")
                except Exception as storage_error:
                    logger.warning(
                        f"Storage usage table not accessible: {storage_error}"
                    )

                return True

        except Exception as e:
            logger.error(f"Database connection test failed: {e}")
            return False

    @staticmethod
    def initialize_cloud_utils():
        """
        Initialize cloud utils and test connectivity.
        """
        logger.info("Initializing cloud utilities...")

        # Test database connection
        if CloudDebug.test_database_connection():
            logger.info("Database connection test passed")
            logger.info("Cloud utilities initialized successfully")
            return True
        else:
            logger.error("Cloud utilities initialization failed")
            return False

    @staticmethod
    async def print_user_details(user_id: int = 1) -> None:
        """
        Print details of the admin user for debugging.
        """
        try:
            logger.info("=== ADMIN USER DETAILS ===")

            # Get user context using crud_users
            from studio.app.common.core.users import crud_users
            from studio.app.common.db.database import session_scope

            with session_scope() as db:
                user_with_details = await crud_users.get_user_with_context(db, user_id)
                if user_with_details:
                    logger.info(f"User ID: {user_with_details.id}")
                    logger.info(f"Name: {user_with_details.name}")
                    logger.info(f"Email: {user_with_details.email}")
                    logger.info(f"UID: {user_with_details.uid}")
                    logger.info(
                        f"Subscription Type: {user_with_details.subscription_type}"
                    )
                    logger.info(
                        f"Has Active Subscription: "
                        f"{user_with_details.has_active_subscription}"
                    )
                    subscription_status = (
                        user_with_details.subscription_status
                        or SubscriptionStatus.FREE.value
                    )
                    logger.info(f"Subscription Status: {subscription_status}")
                    logger.info(
                        f"Storage Usage: "
                        f"{user_with_details.storage_usage_bytes or 0} bytes"
                    )
                    logger.info(
                        f"Storage Quota: "
                        f"{user_with_details.storage_quota_bytes or 0} bytes"
                    )
                else:
                    logger.error(
                        f"Failed to retrieve user details for user_id {user_id}"
                    )

                # Get count of all active subscriptions
                try:
                    active_subscriptions = await crud_users.list_user(
                        db,
                        limit=1000,  # Large limit to get all users
                        skip=0,
                        user_id=None,  # Get all users
                        search=None,
                        email=None,
                    )
                    # Count users with active subscriptions
                    active_count = sum(
                        1
                        for user in active_subscriptions
                        if user.subscription_status
                        and user.subscription_status != SubscriptionStatus.FREE.value
                    )
                    logger.info(f"Total active subscriptions: {active_count}")
                except Exception as e:
                    logger.warning(f"Failed to count active subscriptions: {e}")

            logger.info("=== END ADMIN USER DETAILS ===")

        except Exception as e:
            logger.error(f"Failed to print admin user details: {e}")


async def update_user_storage_after_workflow(workspace_id: str) -> None:
    """
    Update user storage usage after workflow completion.
    Gets the user who owns the workspace and updates their live storage usage.
    Args:
        workspace_id: The workspace ID to update storage for
    """
    try:
        from sqlmodel import select

        from studio.app.common import models as common_model
        from studio.app.common.db.database import session_scope

        # Skip storage update for maintenance/setup workspaces (non-integer IDs)
        try:
            workspace_id_int = int(workspace_id)
        except ValueError:
            logger.info(
                f"Skipping storage update for maintenance workspace: {workspace_id}"
            )
            return

        with session_scope() as db:
            query_result = db.execute(
                select(common_model.Workspace.user_id).where(
                    common_model.Workspace.id == workspace_id_int
                )
            )
            result_row = query_result.first()
            user_id = result_row[0] if result_row else None

            if user_id:
                await get_current_user_storage_usage(user_id, force_live=True)
                logger.info(f"Updated live storage usage for user {user_id}")
    except Exception as e:
        logger.warning(
            f"Failed to update user storage usage after workflow completion: {e}"
        )


async def get_user_subscription_plan(user_id: int) -> Dict[str, Any]:
    """
    Get user subscription tier information.
    Returns subscription tier details including plan name and active status.
    """
    try:
        from studio.app.common.core.users import crud_users

        with session_scope() as db:
            user = await crud_users.get_user_with_context(db, user_id)

            if not user:
                logger.warning(f"User {user_id} not found")
                return {
                    "tier": SubscriptionType.FREE.value,
                    "plan_name": PlanName.FREE.value,
                    "is_premium": False,
                    "has_active_subscription": False,
                }

            # Extract subscription information from user context
            plan_name = getattr(user, "subscription_plan_name", PlanName.FREE.value)
            has_active = getattr(user, "has_active_subscription", False)

            # Determine tier - Premium users should get priority even in grace period
            is_premium = (
                plan_name and plan_name.lower() == SubscriptionType.PREMIUM.value
            )
            tier = (
                SubscriptionType.PREMIUM.value
                if is_premium
                else SubscriptionType.FREE.value
            )

            logger.info(f"User {user_id} subscription tier: {tier} (plan: {plan_name})")

            return {
                "tier": tier,
                "plan_name": plan_name or PlanName.FREE.value,
                "is_premium": is_premium,
                "has_active_subscription": has_active,
            }

    except Exception as e:
        logger.warning(f"Failed to get subscription tier for user {user_id}: {e}")
        # Return free tier as fallback
        return {
            "tier": SubscriptionType.FREE.value,
            "plan_name": PlanName.FREE.value,
            "is_premium": False,
            "has_active_subscription": False,
        }
