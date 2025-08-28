"""
Cloud utilities for user context and subscription management.
"""
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session
from sqlmodel import select

from studio.app.common.core.logger import AppLogger
from studio.app.common.db.database import session_scope
from studio.app.common.models import SubscriptionPlans
from studio.app.common.models import User as UserModel
from studio.app.common.models import UserStorageUsage, UserSubscription

logger = AppLogger.get_logger()


def _get_fallback_users(db: Session) -> list:
    """
    Get fallback user list (admin user) when subscription tables don't exist.
    """
    try:
        # Get user with ID 1 as fallback for subscription monitoring
        result = db.execute(
            select(UserModel).where(UserModel.id == 1, UserModel.active.is_(True))
        )
        fallback_user_row = result.first()

        if fallback_user_row:
            logger.info("Using fallback user for subscription monitoring")
            # fallback_user_row is a SQLAlchemy Row object containing UserModel
            user_obj = fallback_user_row[0]
            return [
                {
                    "id": user_obj.id,
                    "name": user_obj.name,
                    "email": user_obj.email,
                    "plan_name": "Free",
                    "plan_price": 0,
                    "status": "active",
                    "created_at": user_obj.created_at,
                    "tier": "free",
                }
            ]
        else:
            logger.warning("Admin user (ID 1) not found")
            return []

    except Exception as e:
        logger.error(f"Failed to get fallback users: {e}")
        return []


def _get_fallback_storage_quota(user_id: int) -> Dict[str, Any]:
    """
    Get fallback storage quota when storage usage table doesn't exist.
    Tries to determine quota based on user's subscription tier.
    """
    try:
        # Try to get user's subscription tier to determine appropriate quota
        user_context = get_user_context_by_id(user_id)
        if user_context:
            tier = user_context.get("subscription_tier", "free")
            plan_name = user_context.get("subscription_plan_name", "Free")

            # Set quotas based on subscription tier
            if tier == "paid":
                default_quota_bytes = 100 * 1024 * 1024 * 1024  # 100GB for paid tier
                logger.info(
                    f"Using paid tier quota for user {user_id} ({plan_name}): 100GB"
                )
            else:
                default_quota_bytes = 5 * 1024 * 1024 * 1024  # 5GB for free tier
                logger.info(
                    f"Using free tier quota for user {user_id} ({plan_name}): 5GB"
                )
        else:
            # Fallback to free tier if we can't determine subscription
            default_quota_bytes = 5 * 1024 * 1024 * 1024  # 5GB
            logger.warning(
                f"Could not determine subscription for user {user_id}, "
                "using free tier quota: 5GB"
            )

    except Exception as e:
        logger.warning(
            f"Error determining subscription quota for user {user_id}: {e}, "
            "using free tier"
        )
        default_quota_bytes = 5 * 1024 * 1024 * 1024  # 5GB fallback

    return {
        "user_id": user_id,
        "current_usage_bytes": 0,  # Unknown, will be calculated from S3
        "quota_limit_bytes": default_quota_bytes,
        "usage_percentage": 0.0,
        "last_updated": None,
    }


def get_user_context_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    """
    Get user context including subscription tier from database by user ID.
    Returns user info with subscription details or None if not found.
    """
    try:
        with session_scope() as db:
            # Query user with subscription information using ORM joins
            statement = (
                select(
                    UserModel,
                    SubscriptionPlans.name.label("plan_name"),
                    SubscriptionPlans.price.label("plan_price"),
                    UserSubscription.expiration,
                )
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

            query_result = db.execute(statement)
            result = query_result.first()

            if result:
                user, plan_name, plan_price, expiration = result

                # Determine subscription status and tier
                subscription_plan_name = plan_name or "Free"
                subscription_price = plan_price or 0
                subscription_status = (
                    "active"
                    if expiration and expiration > datetime.now()
                    else "expired"
                )
                subscription_tier = (
                    "paid" if subscription_plan_name == "Premium" else "free"
                )

                # Get downgrade warning information
                downgrade_warning = calculate_downgrade_warning(user_id)

                user_context = {
                    "id": user.id,
                    "uid": user.uid,
                    "name": user.name,
                    "email": user.email,
                    "active": user.active,
                    "attributes": user.attributes,
                    "subscription_plan_name": subscription_plan_name,
                    "subscription_price": subscription_price,
                    "subscription_status": subscription_status,
                    "subscription_tier": subscription_tier,
                    "downgrade_warning": downgrade_warning,
                }

                logger.info(
                    f"Retrieved user context: {user_context['name']} "
                    f"({user_context['email']}) "
                    f"- Tier: {user_context['subscription_tier']}"
                )
                logger.info(f"Complete user context for user {user_id}: {user_context}")
                return user_context
            else:
                logger.warning(f"User {user_id} not found or inactive")
                return None

    except Exception as e:
        logger.error(f"Failed to get user context for user {user_id}: {e}")
        return None


def get_user_subscription_details(user_id: int) -> Optional[Dict[str, Any]]:
    """
    Get detailed subscription information for a specific user.
    """
    try:
        with session_scope() as db:
            # Query using ORM joins
            statement = (
                select(
                    UserModel.id.label("user_id"),
                    UserModel.name,
                    UserModel.email,
                    SubscriptionPlans.id.label("plan_id"),
                    SubscriptionPlans.name.label("plan_name"),
                    SubscriptionPlans.price.label("plan_price"),
                    UserSubscription.expiration,
                    UserSubscription.created_at.label("subscription_start"),
                    UserSubscription.updated_at.label("subscription_updated"),
                    UserStorageUsage.current_usage_bytes,
                    UserStorageUsage.quota_limit_bytes,
                    UserStorageUsage.last_updated.label("usage_last_updated"),
                )
                .outerjoin(
                    UserSubscription,
                    (UserModel.id == UserSubscription.user_id)
                    & (UserSubscription.expiration > datetime.now()),
                )
                .outerjoin(
                    SubscriptionPlans, UserSubscription.plan_id == SubscriptionPlans.id
                )
                .outerjoin(UserStorageUsage, UserModel.id == UserStorageUsage.user_id)
                .where(UserModel.id == user_id, UserModel.active.is_(True))
            )

            query_result = db.execute(statement)
            result = query_result.first()

            if result:
                # Convert to dictionary
                subscription_details = {
                    "user_id": result.user_id,
                    "name": result.name,
                    "email": result.email,
                    "plan_id": result.plan_id,
                    "plan_name": result.plan_name,
                    "plan_price": result.plan_price,
                    "expiration": result.expiration,
                    "status": "active"
                    if result.expiration and result.expiration > datetime.now()
                    else "expired",
                    "subscription_start": result.subscription_start,
                    "subscription_updated": result.subscription_updated,
                    "current_usage_bytes": result.current_usage_bytes,
                    "quota_limit_bytes": result.quota_limit_bytes,
                    "usage_last_updated": result.usage_last_updated,
                }

                logger.debug(f"Retrieved subscription details for user {user_id}")
                return subscription_details
            else:
                logger.warning(f"No subscription details found for user {user_id}")
                return None

    except Exception as e:
        logger.error(f"Failed to get subscription details for user {user_id}: {e}")
        return None


def get_all_active_subscriptions() -> list:
    """
    Get all active subscription users for monitoring and reporting.
    Falls back to admin user if subscription tables don't exist.
    """
    try:
        with session_scope() as db:
            # Try to query using ORM models directly
            try:
                statement = (
                    select(
                        UserModel.id,
                        UserModel.name,
                        UserModel.email,
                        SubscriptionPlans.name.label("plan_name"),
                        SubscriptionPlans.price.label("plan_price"),
                        UserSubscription.created_at,
                    )
                    .join(UserSubscription, UserModel.id == UserSubscription.user_id)
                    .join(
                        SubscriptionPlans,
                        UserSubscription.plan_id == SubscriptionPlans.id,
                    )
                    .where(
                        UserModel.active.is_(True),
                        UserSubscription.expiration > datetime.now(),
                    )
                    .order_by(SubscriptionPlans.price.desc(), UserModel.name)
                )

                query_result = db.execute(statement)
                results = query_result.all()

                results_list = []
                for result in results:
                    subscription_data = {
                        "id": result.id,
                        "name": result.name,
                        "email": result.email,
                        "plan_name": result.plan_name,
                        "plan_price": result.plan_price,
                        "status": "active",  # Already filtered for active subscriptions
                        "created_at": result.created_at,
                        "tier": "paid" if result.plan_name == "Premium" else "free",
                    }
                    results_list.append(subscription_data)

                logger.info(f"Retrieved {len(results_list)} active subscriptions")
                return results_list

            except Exception as orm_error:
                logger.warning(
                    f"ORM query failed: {orm_error}, falling back to admin user"
                )
                return _get_fallback_users(db)

    except Exception as e:
        logger.warning(
            f"Failed to get active subscriptions: {e}, falling back to admin user"
        )
        try:
            with session_scope() as db:
                return _get_fallback_users(db)
        except Exception as fallback_error:
            logger.error(f"Fallback also failed: {fallback_error}")
            return []


def get_user_storage_usage(user_id: int) -> Optional[Dict[str, Any]]:
    """
    Get storage usage information for a user.
    Falls back to default quota if storage table doesn't exist.
    """
    try:
        with session_scope() as db:
            # Try to query using ORM model
            try:
                query_result = db.execute(
                    select(UserStorageUsage).where(UserStorageUsage.user_id == user_id)
                )
                result_row = query_result.first()
                logger.debug(
                    f"get_user_storage_usage: "
                    f"result_row type={type(result_row)}, value={result_row}"
                )
                storage_usage = result_row[0] if result_row else None
                logger.debug(
                    f"get_user_storage_usage: "
                    f"storage_usage type={type(storage_usage)}, value={storage_usage}"
                )

                if storage_usage:
                    result_dict = {
                        "user_id": storage_usage.user_id,
                        "current_usage_bytes": storage_usage.current_usage_bytes,
                        "quota_limit_bytes": storage_usage.quota_limit_bytes,
                        "usage_percentage": storage_usage.usage_percentage,
                        "last_updated": storage_usage.last_updated,
                    }

                    logger.info(
                        f"Retrieved storage usage for user {user_id}: "
                        f"current_usage={result_dict.get('current_usage_bytes')}, "
                        f"quota_limit={result_dict.get('quota_limit_bytes')}, "
                        f"usage_percentage={result_dict.get('usage_percentage')}%"
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
                    existing_usage.current_usage_bytes = new_usage_bytes
                    existing_usage.last_updated = datetime.now()
                    db.add(existing_usage)
                else:
                    # Need to determine quota - try to get from user's subscription
                    user_context = get_user_context_by_id(user_id)
                    if user_context and user_context.get("subscription_tier") == "paid":
                        default_quota = 100 * 1024 * 1024 * 1024  # 100GB for paid
                    else:
                        default_quota = 5 * 1024 * 1024 * 1024  # 5GB for free

                    # Create new record
                    new_storage_usage = UserStorageUsage(
                        user_id=user_id,
                        current_usage_bytes=new_usage_bytes,
                        quota_limit_bytes=default_quota,
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
                storage_info, max_age_minutes=60
            ):
                logger.debug(f"Using cached storage data for user {user_id}")
                return storage_info["current_usage_bytes"]
            else:
                logger.debug(
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
        return storage_info.get("current_usage_bytes", 0) if storage_info else 0


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
        # (Same logic as workspace refresh endpoint)
        import os

        from studio.app.common.core.storage.remote_storage_controller import (
            RemoteStorageType,
        )

        shared_bucket_name = None
        remote_storage_type = RemoteStorageType.get_activated_type()

        if remote_storage_type == RemoteStorageType.S3:
            shared_bucket_name = os.environ.get("S3_DEFAULT_BUCKET_NAME")

        use_s3 = bool(shared_bucket_name)

        if use_s3:
            # Use S3 storage calculation with shared bucket
            from studio.app.common.core.cloud.s3_storage_monitor import S3StorageMonitor

            monitor = S3StorageMonitor(shared_bucket_name)
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
    try:
        # Get all workspaces the user has access to (same logic as workspace refresh)
        from sqlmodel import or_, select

        from studio.app.common import models as common_model

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
            workspace_ids = db.exec(workspaces_query).all()

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
                logger.debug(
                    f"User {user_id} workspace {workspace_id} input: {input_size} bytes"
                )

            # Add output folder size
            output_path = os.path.join(DIRPATH.OUTPUT_DIR, str(workspace_id))
            if os.path.exists(output_path):
                output_size = get_folder_size(output_path)
                total_usage += output_size
                logger.debug(
                    f"User {user_id} workspace {workspace_id} output: "
                    f"{output_size} bytes"
                )

        logger.info(
            f"Calculated local storage size for user {user_id}: {total_usage:,} bytes"
        )
        return total_usage

    except Exception as e:
        logger.error(f"Failed to calculate local storage size for user {user_id}: {e}")
        return 0


def calculate_downgrade_warning(user_id: int) -> Optional[Dict[str, Any]]:
    """
    Calculate downgrade warning based on subscription and storage status:

    Cases:
    1. Free user, no storage limit exceeded → No warning
    2. Free user, storage limit exceeded → Storage warning
    3. Premium user, storage limit exceeded → Storage warning
    4. Premium user, subscription expiring only → Subscription warning
    5. Premium user, storage exceeded + subscription expiring → Combined warning
    """
    try:
        # Default values as fallbacks
        DEFAULT_FREE_TIER_LIMIT_GB = 5
        DEFAULT_FREE_TIER_LIMIT_BYTES = DEFAULT_FREE_TIER_LIMIT_GB * 1024 * 1024 * 1024
        GRACE_PERIOD_DAYS = 30
        WARNING_PERIOD_DAYS = 30

        logger.info(f"Calculating downgrade warning for user {user_id}")

        with session_scope() as db:
            # Get user's current storage usage - prioritize fresh data at startup
            import asyncio

            # First check if we have very fresh cached data (within 5 minutes)
            storage_info = get_user_storage_usage(user_id)
            if storage_info and _is_storage_data_fresh(storage_info, max_age_minutes=5):
                current_usage_bytes = storage_info.get("current_usage_bytes", 0)
                logger.debug(
                    f"DowngradeWarning: Using fresh cached storage data "
                    f"for user {user_id}: {current_usage_bytes}"
                )
            else:
                try:
                    # Try to get current event loop
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # We're in an async context
                        # use database fallback but log that data may be stale
                        current_usage_bytes = (
                            storage_info.get("current_usage_bytes", 0)
                            if storage_info
                            else 0
                        )
                        logger.warning(
                            f"DowngradeWarning: Using potentially "
                            f"stale storage data for user {user_id}: "
                            f"{current_usage_bytes} (in async context)"
                        )
                    else:
                        # We can run async code safely
                        current_usage_bytes = loop.run_until_complete(
                            get_current_user_storage_usage(user_id, force_live=True)
                        )
                        logger.debug(
                            f"DowngradeWarning: Calculated fresh storage data "
                            f"for user {user_id}: {current_usage_bytes}"
                        )
                except RuntimeError:
                    # No event loop, we can create one
                    current_usage_bytes = asyncio.run(
                        get_current_user_storage_usage(user_id, force_live=True)
                    )
                    logger.debug(
                        f"DowngradeWarning: Calculated fresh storage data "
                        f"for user {user_id}: {current_usage_bytes}"
                    )

            # Get quota from database for this user
            storage_usage = get_user_storage_usage(user_id)

            # Use actual quota from database, fallback to default if not available
            quota_limit_bytes = (
                storage_usage.get("quota_limit_bytes", DEFAULT_FREE_TIER_LIMIT_BYTES)
                if storage_usage
                else DEFAULT_FREE_TIER_LIMIT_BYTES
            )
            quota_limit_gb = quota_limit_bytes / (1024 * 1024 * 1024)

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
                logger.info(f"  Subscription end: {subscription_end}")
                logger.info(f"  Grace end: {grace_end}")
                logger.info(f"  Deletion date: {deletion_date}")
                logger.info(f"  Current time: {now}")

                if subscription_end > now:
                    subscription_status = "active"
                elif now <= grace_end:
                    subscription_status = "grace"
                elif now <= deletion_date:
                    subscription_status = "warning"
                    days_remaining = (deletion_date - now).days
                else:
                    subscription_status = "overdue"
                    days_remaining = 0

                logger.info(
                    f"  Final status: {subscription_status}, "
                    f"days_remaining: {days_remaining}"
                )
            else:
                subscription_status = "free"  # Never had premium

            # Step 2: Determine storage status
            storage_exceeded = current_usage_bytes > quota_limit_bytes
            excess_bytes = max(0, current_usage_bytes - quota_limit_bytes)
            excess_gb = excess_bytes / (1024 * 1024 * 1024)
            current_usage_gb = current_usage_bytes / (1024 * 1024 * 1024)

            # Step 3: Apply the 5 cases
            logger.info(f"User {user_id} warning analysis:")
            logger.info(f"  Subscription status: {subscription_status}")
            logger.info(f"  Storage exceeded: {storage_exceeded}")
            logger.info(
                f"  Current usage: {current_usage_gb:.2f}GB / {quota_limit_gb:.1f}GB"
            )

            # Case 1: Free user, no storage limit exceeded → No warning
            if subscription_status == "free" and not storage_exceeded:
                logger.info(
                    f"User {user_id}: No warning needed (free tier, within limits)"
                )
                return None

            # Case 2: Free user, storage limit exceeded → Storage warning
            if subscription_status == "free" and storage_exceeded:
                return {
                    "has_warning": True,
                    "warning_type": "immediate",
                    "days_remaining": 30,
                    "excess_data_bytes": excess_bytes,
                    "excess_data_gb": round(excess_gb, 2),
                    "current_usage_bytes": current_usage_bytes,
                    "current_usage_gb": round(current_usage_gb, 2),
                    "free_tier_limit_bytes": quota_limit_bytes,
                    "free_tier_limit_gb": quota_limit_gb,
                    "deletion_date": (datetime.now() + timedelta(days=30)).isoformat(),
                    "message": (
                        f"Your data usage ({round(current_usage_gb, 1)} GB) "
                        f"exceeds the free tier limit ({quota_limit_gb:.1f} GB). "
                        f"Please upgrade or remove {round(excess_gb, 1)} GB of data "
                        f"within 30 days."
                    ),
                }

            # Case 3: Premium user active, storage limit exceeded → Storage warning only
            if subscription_status == "active" and storage_exceeded:
                return {
                    "has_warning": True,
                    "warning_type": "immediate",
                    "days_remaining": 30,
                    "excess_data_bytes": excess_bytes,
                    "excess_data_gb": round(excess_gb, 2),
                    "current_usage_bytes": current_usage_bytes,
                    "current_usage_gb": round(current_usage_gb, 2),
                    "free_tier_limit_bytes": quota_limit_bytes,
                    "free_tier_limit_gb": quota_limit_gb,
                    "message": (
                        f"Your storage usage ({round(current_usage_gb, 1)} GB) is over "
                        f"the limit for your plan. You will be unable to run workflows."
                        f" Consider cleaning up unused data."
                    ),
                }

            # Cases 4 & 5: Premium user with subscription issues (warning/overdue)
            if subscription_status in ["warning", "overdue"]:
                logger.info(
                    f"User {user_id}: Creating downgrade warning "
                    f"(status: {subscription_status})"
                )
                warning_type = (
                    "downgrade" if subscription_status == "warning" else "overdue"
                )

                if storage_exceeded:
                    # Case 4: Both storage and subscription issues
                    message = (
                        f"Your premium subscription expired on "
                        f"{subscription_end.strftime('%B %d, %Y')}. "
                        f"You have {days_remaining or 0} days to upgrade or remove "
                        f"{round(excess_gb, 1)} GB of data to stay "
                        f"within the free tier limit."
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
                    "current_usage_bytes": current_usage_bytes,
                    "current_usage_gb": round(current_usage_gb, 2),
                    "free_tier_limit_bytes": quota_limit_bytes,
                    "free_tier_limit_gb": quota_limit_gb,
                    "subscription_end_date": subscription_end.isoformat()
                    if subscription_end
                    else None,
                    "message": message,
                }

            # All other cases: No warning needed
            logger.info(f"User {user_id}: No warning needed (other cases)")
            return None

    except Exception as e:
        logger.error(f"Failed to calculate downgrade warning for user {user_id}: {e}")
        return None


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
                logger.info(f"Found fallback user: {user_obj.name} ({user_obj.email})")
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
                logger.warning(f"Subscription plans table not accessible: {plan_error}")

            try:
                query_result = db.execute(
                    select(UserSubscription).where(
                        UserSubscription.expiration > datetime.now()
                    )
                )
                active_subscriptions = len(query_result.all())
                logger.info(f"Active subscriptions: {active_subscriptions}")
            except Exception as sub_error:
                logger.warning(f"User subscriptions table not accessible: {sub_error}")

            try:
                query_result = db.execute(select(UserStorageUsage))
                storage_records = len(query_result.all())
                logger.info(f"Storage usage records: {storage_records}")
            except Exception as storage_error:
                logger.warning(f"Storage usage table not accessible: {storage_error}")

            return True

    except Exception as e:
        logger.error(f"Database connection test failed: {e}")
        return False


def print_user_details(user_id: int = 1) -> None:
    """
    Print details of the admin user for debugging.
    """
    try:
        logger.info("=== ADMIN USER DETAILS ===")

        # Get user context
        user_context = get_user_context_by_id(user_id)
        if user_context:
            logger.info(f"User ID: {user_context['id']}")
            logger.info(f"Name: {user_context['name']}")
            logger.info(f"Email: {user_context['email']}")
            logger.info(f"UID: {user_context['uid']}")
            logger.info(f"Subscription Plan: {user_context['subscription_plan_name']}")
            logger.info(f"Subscription Tier: {user_context['subscription_tier']}")
            logger.info(f"Subscription Status: {user_context['subscription_status']}")
            logger.info(f"Plan Price: {user_context['subscription_price']} cents")
        else:
            logger.error("Failed to retrieve admin user context")

        # Get subscription details
        subscription_details = get_user_subscription_details(user_id)
        if subscription_details:
            logger.info(
                f"Storage Usage: {subscription_details['current_usage_bytes']} bytes"
            )
            logger.info(
                f"Storage Quota: {subscription_details['quota_limit_bytes']} bytes"
            )

        # Get all active subscriptions
        active_subs = get_all_active_subscriptions()
        logger.info(f"Total active subscriptions: {len(active_subs)}")

        logger.info("=== END ADMIN USER DETAILS ===")

    except Exception as e:
        logger.error(f"Failed to print admin user details: {e}")


# Test function to be called during initialization
def initialize_cloud_utils():
    """
    Initialize cloud utils and test connectivity.
    """
    logger.info("Initializing cloud utilities...")

    # Test database connection
    if test_database_connection():
        logger.info("Database connection test passed")
        logger.info("Cloud utilities initialized successfully")
        return True
    else:
        logger.error("Cloud utilities initialization failed")
        return False


# if __name__ == "__main__":
#     # For testing purposes
#     initialize_cloud_utils()
