from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from studio.app.common.core.auth.auth_dependencies import get_current_user
from studio.app.common.core.cloud.cloud_utils import (
    CloudDebug,
    get_user_context_with_warnings,
    get_user_storage_usage,
)
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.premium.premium_assignment_service import (
    premium_assignment_service,
)
from studio.app.common.core.subscription.constants import (
    PlanName,
    SubscriptionStatus,
    SubscriptionType,
)
from studio.app.common.core.users import crud_users
from studio.app.common.db.database import get_db
from studio.app.common.schemas.users import SelfUserUpdate, User, UserPasswordUpdate

router = APIRouter(prefix="/users/me", tags=["users/me"])
logger = AppLogger.get_logger()


@router.get("", response_model=User)
async def me(current_user: User = Depends(get_current_user)):
    """
    Get current user information including subscription tier for premium routing.
    This endpoint is enhanced to support ALB header-based routing for premium users.
    """
    # The current_user already includes subscription_plan_name and subscription_type
    # from auth_dependencies.get_current_user(), so we can return it directly
    return current_user


@router.get("/routing-info", response_model=Dict)
async def get_routing_info(current_user: User = Depends(get_current_user)):
    """
    Get routing information for ALB header-based routing.
    Returns the headers that should be sent with requests for premium users.
    """
    is_premium = current_user.subscription_type == SubscriptionType.PREMIUM.value

    routing_info = {
        "user_id": str(current_user.id),
        "user_tier": current_user.subscription_type,
        "requires_premium_routing": is_premium,
        "routing_headers": {},
    }

    # Add routing headers if user is premium
    if is_premium:
        routing_info["routing_headers"] = {
            "X-User-Tier": SubscriptionType.PREMIUM.value,
            "X-User-ID": str(current_user.id),
        }

    logger.info(
        f"Routing info for user {current_user.id}: "
        f"tier={current_user.subscription_type}, premium={is_premium}"
    )

    return routing_info


@router.post("/premium/assign", response_model=Dict)
async def assign_premium_instance(current_user: User = Depends(get_current_user)):
    """
    Assign current user to a premium instance if they have an active subscription.
    This endpoint triggers the premium assignment process.
    """
    # Check if user is premium
    if current_user.subscription_type != SubscriptionType.PREMIUM.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Premium subscription required for dedicated instance assignment",
        )

    try:
        # Call the premium assignment service
        result = await premium_assignment_service.assign_premium_user(current_user.id)

        logger.info(f"Assignment service result: {result}")
        logger.info(f"is_shared from service: {result.get('is_shared')}")
        logger.info(
            f"assignment_source from service: {result.get('assignment_source')}"
        )

        if result["success"]:
            response = {
                "message": result["message"],
                "instance_id": result.get("instance_id"),
                "assigned": True,
                "is_shared": result.get("is_shared", False),
                "assignment_source": result.get("assignment_source"),
            }
            logger.info(f"API response: {response}")
            return response
        elif result.get("requires_retry"):
            # Return 202 for scaling in progress
            return {
                "message": result["message"],
                "assigned": False,
                "retry_after": result.get("retry_after", 180),
                "scaling_in_progress": True,
            }
        else:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=result["message"],
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to assign premium instance for user {current_user.id}: {e}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to assign premium instance",
        )


@router.delete("/premium/assign", response_model=Dict)
async def release_premium_instance(current_user: User = Depends(get_current_user)):
    """
    Release current user from their assigned premium instance.
    This endpoint should be called on logout for premium users.
    """
    try:
        # Call the premium assignment service
        result = await premium_assignment_service.release_premium_user(current_user.id)

        if result["success"]:
            return {
                "message": result["message"],
                "released_instance": result.get("released_instance"),
                "released": True,
            }
        else:
            # Log the error but don't fail completely - user may not have been assigned
            logger.warning(
                f"Failed to release premium instance for user "
                f"{current_user.id}: {result['message']}"
            )
            return {
                "message": "Release completed (user may not have been assigned)",
                "released": True,
            }

    except Exception as e:
        logger.error(
            f"Error releasing premium instance for user {current_user.id}: {e}"
        )
        # Don't fail on release errors - just log them
        return {"message": "Release completed with warnings", "released": True}


@router.get("/premium/status", response_model=Dict)
async def get_premium_assignment_status(current_user: User = Depends(get_current_user)):
    """
    Get the current premium instance assignment status for the user.
    """
    try:
        # Get assignment status
        status_info = await premium_assignment_service.get_premium_user_status(
            current_user.id
        )

        return {
            "user_id": current_user.id,
            "subscription_type": current_user.subscription_type,
            "is_premium": current_user.subscription_type
            == SubscriptionType.PREMIUM.value,
            "assignment": status_info,
        }

    except Exception as e:
        logger.error(f"Error getting premium status for user {current_user.id}: {e}")
        return {
            "user_id": current_user.id,
            "subscription_type": current_user.subscription_type,
            "is_premium": current_user.subscription_type
            == SubscriptionType.PREMIUM.value,
            "assignment": None,
            "error": str(e),
        }


@router.post("/premium/heartbeat", response_model=Dict)
async def send_premium_heartbeat(current_user: User = Depends(get_current_user)):
    """
    Send heartbeat to update activity timestamp for premium users.
    Prevents stale assignment cleanup for active users.
    """
    # Check if user is premium
    is_premium = current_user.subscription_type == SubscriptionType.PREMIUM.value

    if not is_premium:
        return {
            "message": "Heartbeat received (non-premium user)",
            "user_id": current_user.id,
            "user_tier": SubscriptionType.FREE.value,
            "assignment_active": False,
            "updated": False,
        }

    try:
        # Call the premium assignment service to update activity
        result = await premium_assignment_service.update_user_activity(current_user.id)

        return {
            "message": "Activity updated successfully"
            if result["success"]
            else "No active assignment found",
            "updated": result["success"],
            "user_id": current_user.id,
            "user_tier": SubscriptionType.PREMIUM.value,
            "assignment_active": result["success"],
            "activity_update": result.get("timestamp"),
        }

    except Exception as e:
        logger.error(f"Error processing heartbeat for user {current_user.id}: {e}")
        # Don't fail heartbeats - they should always succeed
        return {
            "message": f"Heartbeat processed with warnings: {str(e)}",
            "updated": False,
            "user_id": current_user.id,
            "user_tier": SubscriptionType.PREMIUM.value,
            "assignment_active": False,
            "error": str(e),
        }


@router.put("", response_model=User)
async def update_me(
    data: SelfUserUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await crud_users.update_user(
        db, current_user.id, data, organization_id=current_user.organization.id
    )


@router.put("/password", response_model=bool)
async def update_password(
    data: UserPasswordUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await crud_users.update_password(
        db, current_user.id, data, organization_id=current_user.organization.id
    )


@router.post("/premium/heartbeat", response_model=Dict)
async def premium_heartbeat(current_user: User = Depends(get_current_user)):
    """
    Update activity timestamp for premium users to prevent stale assignment cleanup.
    Should be called every 2-5 minutes by frontend for premium users.
    """
    # Only premium users need heartbeat tracking
    if current_user.subscription_type != SubscriptionType.PREMIUM.value:
        return {
            "message": "Heartbeat not needed for non-premium users",
            "updated": False,
            "user_tier": current_user.subscription_type,
        }

    try:
        logger.info(f"Processing heartbeat for premium user {current_user.id}")

        activity_result = await premium_assignment_service.update_user_activity(
            current_user.id
        )

        # Check if the update was successful
        if activity_result.get("success", False):
            return {
                "message": "Premium user heartbeat recorded",
                "updated": True,
                "user_id": current_user.id,
                "user_tier": current_user.subscription_type,
                "assignment_active": True,
                "activity_update": activity_result,
            }
        else:
            # User might not have an active assignment - not an error
            return {
                "message": "No active premium assignment found",
                "updated": False,
                "user_id": current_user.id,
                "user_tier": current_user.subscription_type,
                "assignment_active": False,
                "activity_update": activity_result,
            }

    except Exception as e:
        logger.error(f"Error processing heartbeat for user {current_user.id}: {e}")
        # Don't fail the heartbeat - return success to keep frontend happy
        return {
            "message": "Heartbeat processed with warnings",
            "updated": True,
            "user_id": current_user.id,
            "user_tier": current_user.subscription_type,
            "error": str(e),
        }


@router.delete("", response_model=bool)
async def delete_me(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await crud_users.delete_user(
        db, current_user.id, organization_id=current_user.organization.id
    )


@router.get("/cloud-details", response_model=Dict)
async def get_my_cloud_details(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get cloud-related details for the current user including subscription and
    storage info. This endpoint properly uses the authenticated user's ID for
    testing cloud functionality.
    """
    try:
        logger.info(f"Getting cloud details for user {current_user.id}")

        # Call the print_user_details function with the current user's ID
        # This will log the details and test the cloud functionality
        await CloudDebug.print_user_details(user_id=current_user.id)

        result = {
            "user_id": current_user.id,
            "user_name": current_user.name,
            "user_email": current_user.email,
        }

        # Get user context
        user_context = await get_user_context_with_warnings(current_user.id)
        if user_context:
            result["user_context"] = {
                "subscription_plan_name": user_context["subscription_plan_name"],
                "subscription_plan": user_context["subscription_plan"],
                "subscription_status": user_context["subscription_status"],
            }
        else:
            result["user_context"] = None

        # Get subscription details using crud_users
        user_with_details = await crud_users.get_user_with_context(db, current_user.id)
        if user_with_details:
            result["subscription_details"] = {
                "plan_name": user_with_details.subscription_plan_name
                or PlanName.FREE.value,
                "status": user_with_details.subscription_status
                or SubscriptionStatus.FREE.value,
                "storage_usage_bytes": user_with_details.storage_usage_bytes or 0,
                "storage_quota_bytes": user_with_details.storage_quota_bytes or 0,
            }
        else:
            result["subscription_details"] = None

        # Get storage usage
        storage_usage = get_user_storage_usage(current_user.id)
        if storage_usage:
            result["storage_usage"] = storage_usage
        else:
            result["storage_usage"] = None

        return result

    except Exception as e:
        logger.error(f"Failed to get cloud details for user {current_user.id}: {e}")
        return {
            "error": str(e),
            "user_id": current_user.id,
        }
