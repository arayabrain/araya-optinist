# studio/app/common/core/users/registration_router.py

from fastapi import APIRouter, Depends, HTTPException
from firebase_admin import auth as firebase_auth
from sqlmodel import Session

from studio.app.common.core.auth.auth_email_service import AuthEmailService
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.users import crud_users
from studio.app.common.db.database import get_db
from studio.app.common.schemas.registrations import ResendVerificationRequest
from studio.app.common.schemas.users import UserCreate, UserCreateResponse
from studio.app.const import DEFAULT_ORGANIZATION_ID

router = APIRouter(prefix="/api/register", tags=["registration"])
logger = AppLogger.get_logger()


@router.post("", response_model=UserCreateResponse)
async def complete_registration(
    request: UserCreate,
    db: Session = Depends(get_db),
):
    return await crud_users.create_user(
        db, request, organization_id=DEFAULT_ORGANIZATION_ID, verified=False
    )


@router.get("/verify-status/{email}")
async def check_verification_status(email: str):
    """Check email verification status"""
    try:
        firebase_user = firebase_auth.get_user_by_email(email)
        return {
            "email_verified": firebase_user.email_verified,
            "uid": firebase_user.uid,
        }
    except firebase_auth.UserNotFoundError:
        raise HTTPException(status_code=404, detail="User not found")
    except Exception as e:
        logger.error(f"Verification status check error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail="Failed to check verification status"
        )


@router.post("/resend-verification")
async def resend_verification_email(request: ResendVerificationRequest):
    """Resend verification email endpoint"""
    try:
        # Get Firebase user by email
        firebase_user = firebase_auth.get_user_by_email(request.email)

        # Check if email is already verified
        if firebase_user.email_verified:
            return {
                "success": True,
                "message": "Email is already verified",
                "already_verified": True,
            }

        # Send verification email directly from backend
        AuthEmailService.send_verification_email(request.email)

        return {
            "success": True,
            "message": "Verification email has been sent",
            "already_verified": False,
        }

    except firebase_auth.UserNotFoundError:
        raise HTTPException(status_code=404, detail="User not found")
    except Exception as e:
        logger.error(f"Failed to resend verification email: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail="Failed to resend verification email"
        )
