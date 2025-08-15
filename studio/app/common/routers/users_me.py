from typing import Dict

from fastapi import APIRouter, Depends
from sqlmodel import Session

from studio.app.common.core.auth.auth_dependencies import get_current_user
from studio.app.common.core.cloud.cloud_utils import (
    get_user_context_by_id,
    get_user_storage_usage,
    get_user_subscription_details,
    print_user_details,
)
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.users import crud_users
from studio.app.common.db.database import get_db
from studio.app.common.schemas.users import SelfUserUpdate, User, UserPasswordUpdate

router = APIRouter(prefix="/users/me", tags=["users/me"])
logger = AppLogger.get_logger()


@router.get("", response_model=User)
async def me(current_user: User = Depends(get_current_user)):
    return current_user


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
        print_user_details(user_id=current_user.id)

        result = {
            "user_id": current_user.id,
            "user_name": current_user.name,
            "user_email": current_user.email,
        }

        # Get user context
        user_context = get_user_context_by_id(current_user.id)
        if user_context:
            result["user_context"] = {
                "subscription_plan": user_context["subscription_plan_name"],
                "subscription_tier": user_context["subscription_tier"],
                "subscription_status": user_context["subscription_status"],
                "plan_price_cents": user_context["subscription_price"],
            }
        else:
            result["user_context"] = None

        # Get subscription details
        subscription_details = get_user_subscription_details(current_user.id)
        if subscription_details:
            result["subscription_details"] = {
                "plan_name": subscription_details["plan_name"],
                "plan_price_cents": subscription_details["plan_price"],
                "status": subscription_details["status"],
                "current_usage_bytes": subscription_details["current_usage_bytes"],
                "quota_limit_bytes": subscription_details["quota_limit_bytes"],
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
