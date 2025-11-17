# studio/app/common/core/users/registration_router.py

from fastapi import APIRouter, Depends, Header, HTTPException
from firebase_admin import auth as firebase_auth
from sqlmodel import Session

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.storage.remote_storage_controller import (
    RemoteStorageController,
    RemoteStorageSimpleWriter,
)
from studio.app.common.core.subscription.checkout_service import CheckoutService
from studio.app.common.core.subscription.subscription_service import (
    SubscriptionUserStatus,
)
from studio.app.common.db.database import get_db
from studio.app.common.models import User as UserModel
from studio.app.common.models.subscription import UserSubscription
from studio.app.common.schemas.registrations import CompleteRegistrationRequest

router = APIRouter(prefix="/api/register", tags=["registration"])
logger = AppLogger.get_logger()


def verify_firebase_token(authorization: str = Header(None)) -> dict:
    """Verify Firebase ID token"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication token is required")

    token = authorization.split("Bearer ")[1]

    try:
        decoded_token = firebase_auth.verify_id_token(token)
        logger.info(f"✓ Token verified for UID: {decoded_token.get('uid')}")
        return decoded_token
    except Exception as e:
        logger.error(f"Token verification error: {e}")
        raise HTTPException(status_code=401, detail="Invalid token")


@router.post("/complete")
async def complete_registration(
    request: CompleteRegistrationRequest,
    db: Session = Depends(get_db),
    authorization: str = Header(None),
):
    logger.info(f"Registration request received for: {request.email}")

    # Verify token
    decoded_token = verify_firebase_token(authorization)

    # Verify UID matches
    if decoded_token.get("uid") != request.firebase_uid:
        logger.info("UID mismatch in registration request")
        logger.info(
            f"Token UID: {decoded_token.get('uid')},Request UID: {request.firebase_uid}"
        )
        raise HTTPException(status_code=403, detail="UID does not match")

    try:
        # Check for duplicate email address
        existing_user = (
            db.query(UserModel)
            .filter(UserModel.email == request.email.lower(), UserModel.active)
            .first()
        )

        if existing_user:
            logger.info(f"User already exists in DB: {request.email}")
            return {
                "success": True,
                "message": "User is already registered",
                "user": {
                    "id": existing_user.id,
                    "email": existing_user.email,
                    "name": existing_user.name,
                    "uid": existing_user.uid,
                    "master_key": existing_user.master_key,
                    "email_verified": False,
                },
            }

        # Create user in DB
        logger.info(f"Creating user in DB: {request.email}")
        user_db = UserModel(
            uid=request.firebase_uid,
            email=request.email,
            name=request.name,
            organization_id=request.organization_id,
            active=True,
            registration_source="firebase_client_sdk",
            master_key="",
        )
        db.add(user_db)
        db.flush()

        # Generate master_key
        master_key = f"{user_db.id:010d}"
        user_db.master_key = master_key

        # Set role
        if request.role_id:
            from studio.app.common.core.users.crud_users import set_role

            await set_role(
                db, user_id=user_db.id, role_id=request.role_id, auto_commit=False
            )

        # Create remote storage bucket
        if RemoteStorageController.is_available():
            new_bucket_name = RemoteStorageController.create_user_bucket_name(
                id=user_db.id
            )
            async with RemoteStorageSimpleWriter(
                new_bucket_name
            ) as remote_storage_controller:
                await remote_storage_controller.create_bucket()
            user_db.attributes = {"remote_bucket_name": new_bucket_name}

        # Create subscription
        subscription = UserSubscription(
            plan_id=SubscriptionUserStatus.FREE,
            user_id=user_db.id,
            expiration=CheckoutService.calculate_expiration_date(),
        )
        db.add(subscription)

        db.commit()
        db.refresh(user_db)

        logger.info(f"User created successfully: {user_db.email} (ID: {user_db.id})")

        return {
            "success": True,
            "message": "User created successfully",
            "user": {
                "id": user_db.id,
                "email": user_db.email,
                "name": user_db.name,
                "uid": user_db.uid,
                "master_key": user_db.master_key,
                "email_verified": False,
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Registration error: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Registration failed: {str(e)}")


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
