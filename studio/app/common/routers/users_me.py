from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session

from studio.app.common.core.auth.auth_dependencies import get_current_user
from studio.app.common.core.cloud.cloud_utils import (
    CloudDebug,
    get_user_context_with_warnings,
)
from studio.app.common.core.cloud.storage_tracking import get_user_storage_usage
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.middleware.user_activity_middleware import (
    increment_heartbeat_failures,
    invalidate_activity_cache,
    mark_user_logged_out,
)
from studio.app.common.core.premium.premium_assignment_service import (
    premium_assignment_service,
)
from studio.app.common.core.subscription.constants import (
    PlanName,
    SubscriptionStatus,
    SubscriptionType,
)
from studio.app.common.core.users import crud_users
from studio.app.common.core.utils.datetime_utils import get_current_datetime
from studio.app.common.db.database import get_db
from studio.app.common.models import FreeUserAssignment
from studio.app.common.schemas.users import SelfUserUpdate, User, UserPasswordUpdate

router = APIRouter(prefix="/users/me", tags=["users/me"])

beacon_router = APIRouter(prefix="/users/me", tags=["users/me"])
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
        "user_id": current_user.uid,
        "user_tier": current_user.subscription_type,
        "requires_premium_routing": is_premium,
        "routing_headers": {},
    }

    # Note: Routing headers are no longer returned here.
    # The secure_routing_middleware automatically adds X-Routing-ID (HMAC hash)
    # to response headers for premium users. The frontend captures this from
    # the response header, not from this endpoint's response body.

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
        result = await premium_assignment_service.assign_premium_user(
            current_user.id, current_user.uid
        )

        logger.debug(f"Assignment service result: {result}")
        logger.debug(f"is_shared from service: {result.get('is_shared')}")
        logger.debug(
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
            logger.debug(f"API response: {response}")
            return response
        elif result.get("requires_retry"):
            # Return 202 for scaling in progress
            return {
                "message": result["message"],
                "assigned": False,
                # Default retry interval: 180 seconds (3 minutes)
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
        invalidate_activity_cache(current_user.id)
        mark_user_logged_out(current_user.id)

        # Call the premium assignment service
        result = await premium_assignment_service.release_premium_user(
            current_user.id, current_user.uid
        )

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


@router.get("/premium/beacon-token", response_model=Dict)
async def get_beacon_token(
    current_user: User = Depends(get_current_user),
):
    from studio.app.common.core.auth.security import create_beacon_token

    token = create_beacon_token(current_user.uid)
    return {"token": token}


@beacon_router.post("/premium/release-beacon", response_model=Dict)
async def release_premium_beacon(request: Request, db: Session = Depends(get_db)):
    """
    Beacon endpoint for reliable cleanup on browser close.
    Authenticated via HMAC-signed token (sendBeacon cannot
    carry Authorization headers).
    """
    from studio.app.common.core.auth.security import validate_beacon_token
    from studio.app.common.models.user import User as UserModel

    try:
        body = await request.json()
        token = body.get("token")
        if not token:
            return {"success": False, "message": "Missing token"}

        user_uid = validate_beacon_token(token)
        if not user_uid:
            return {"success": False, "message": "Invalid token"}

        user = db.query(UserModel).filter(UserModel.uid == user_uid).first()
        if not user:
            return {
                "success": False,
                "message": "User not found",
            }

        invalidate_activity_cache(user.id)
        mark_user_logged_out(user.id)

        result = await premium_assignment_service.release_premium_user(
            user_id=user.id, user_uid=user_uid
        )

        logger.info(f"Beacon release for user {user.id}: " f"{result.get('message')}")
        return {
            "success": True,
            "message": result.get("message", "Release processed"),
        }

    except Exception as e:
        logger.warning(f"Beacon release failed: {e}")
        return {"success": False, "message": str(e)}


@router.post("/free/logout", response_model=Dict)
async def logout_free_user(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """
    Explicitly log out a free tier user.
    Updates logged_out_at timestamp in free_user_assignments table.
    Data cleanup will occur 1 hour after logout.
    """
    # Only for free tier users
    if current_user.subscription_type != SubscriptionType.FREE.value:
        return {
            "message": "Logout tracking only applies to free tier users",
            "user_tier": current_user.subscription_type,
            "logged_out": False,
        }

    try:
        invalidate_activity_cache(current_user.id)
        mark_user_logged_out(current_user.id)

        # Update logged_out_at timestamp via direct SQL
        from sqlalchemy import update

        stmt = (
            update(FreeUserAssignment)
            .where(FreeUserAssignment.user_id == current_user.id)
            .values(logged_out_at=get_current_datetime())
        )
        result = db.execute(stmt)
        db.commit()

        if result.rowcount > 0:
            logger.info(
                f"Free user {current_user.id} logged out, data cleanup scheduled"
            )
        else:
            logger.warning(
                f"No assignment found for free user {current_user.id}, "
                f"may not be assigned yet"
            )

        return {
            "message": "Logout recorded successfully",
            "user_id": current_user.uid,
            "user_tier": SubscriptionType.FREE.value,
            "logged_out": True,
            "cleanup_after_minutes": 60,  # Data cleanup occurs after 1 hour
        }

    except Exception as e:
        logger.error(f"Error logging out free user {current_user.id}: {e}")
        return {
            "message": f"Logout processed with warnings: {str(e)}",
            "user_id": current_user.uid,
            "logged_out": False,
            "error": str(e),
        }


@router.get("/premium/status", response_model=Dict)
async def get_premium_assignment_status(current_user: User = Depends(get_current_user)):
    """
    Get the current premium instance assignment status for the user.
    """
    try:
        # Get assignment status
        status_info = await premium_assignment_service.get_premium_user_status(
            current_user.id, current_user.uid
        )

        logger.debug(
            "Premium status for user %s: " "assigned=%s, is_shared=%s, instance=%s",
            current_user.id,
            status_info is not None,
            status_info.get("is_shared") if status_info else None,
            status_info.get("instance_id") if status_info else None,
        )

        return {
            "user_id": current_user.uid,
            "subscription_type": current_user.subscription_type,
            "is_premium": current_user.subscription_type
            == SubscriptionType.PREMIUM.value,
            "assignment": status_info,
        }

    except Exception as e:
        logger.error(f"Error getting premium status for user {current_user.id}: {e}")
        return {
            "user_id": current_user.uid,
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
            "user_id": current_user.uid,
            "user_tier": SubscriptionType.FREE.value,
            "assignment_active": False,
            "updated": False,
        }

    try:
        # Call the premium assignment service to update activity
        result = await premium_assignment_service.update_user_activity(
            current_user.id, current_user.uid
        )

        if result["success"]:
            return {
                "message": "Activity updated successfully",
                "updated": True,
                "user_id": current_user.uid,
                "user_tier": SubscriptionType.PREMIUM.value,
                "assignment_active": True,
                "activity_update": result.get("timestamp"),
            }
        else:
            failure_count = increment_heartbeat_failures(current_user.id)
            return {
                "message": "No active assignment found",
                "updated": False,
                "user_id": current_user.uid,
                "user_tier": SubscriptionType.PREMIUM.value,
                "assignment_active": False,
                "heartbeat_failures": failure_count,
            }

    except Exception as e:
        logger.error(f"Error processing heartbeat for user " f"{current_user.id}: {e}")
        failure_count = increment_heartbeat_failures(current_user.id)
        return {
            "message": f"Heartbeat processed with warnings: {str(e)}",
            "updated": False,
            "user_id": current_user.uid,
            "user_tier": SubscriptionType.PREMIUM.value,
            "assignment_active": False,
            "heartbeat_failures": failure_count,
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
            "user_id": current_user.uid,
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
            "user_id": current_user.uid,
        }
