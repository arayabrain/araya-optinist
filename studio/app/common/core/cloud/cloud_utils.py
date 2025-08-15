"""
Cloud utilities for user context and subscription management.
"""
from datetime import datetime
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
        # Get admin user (ID 1) as fallback using ORM
        result = db.execute(
            select(UserModel).where(UserModel.id == 1, UserModel.active.is_(True))
        )
        admin_user = result.first()

        if admin_user:
            logger.info("Using admin user as fallback for subscription monitoring")
            # admin_user is a SQLAlchemy Row object containing a tuple with UserModel
            user_obj = admin_user[0]
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
                }

                logger.info(
                    f"Retrieved user context: {user_context['name']} "
                    f"({user_context['email']}) "
                    f"- Tier: {user_context['subscription_tier']}"
                )
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
                storage_usage = query_result.first()

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
                existing_usage = query_result.first()

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


def test_database_connection() -> bool:
    """
    Test database connectivity and test ORM models.
    """
    try:
        logger.info("Testing database connection with ORM...")

        with session_scope() as db:
            # Test basic connection
            query_result = db.execute(select(UserModel).where(UserModel.id == 1))
            admin_user = query_result.first()

            if admin_user:
                logger.info("Database connection successful!")
                # admin_user is a SQLAlchemy Row containing a tuple with UserModel
                user_obj = admin_user[0]
                logger.info(f"Found admin user: {user_obj.name} ({user_obj.email})")
            else:
                logger.info("Database connection successful, but no admin user found")

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
