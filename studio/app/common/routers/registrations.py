# studio/app/common/core/users/registration_router.py

from fastapi import APIRouter, Depends, HTTPException
from firebase_admin import auth as firebase_auth
from pydantic import BaseModel, EmailStr, validator
from sqlmodel import Session

from studio.app.common.core.logger import AppLogger

from studio.app.common.core.users import crud_users
from studio.app.common.db.database import get_db
from studio.app.common.schemas.users import UserCreate, UserCreateResponse

router = APIRouter(prefix="/api/register", tags=["registration"])
logger = AppLogger.get_logger()

# Constants
DEFAULT_ORGANIZATION_ID = 1
MIN_NAME_LENGTH = 2


class CompleteRegistrationRequest(BaseModel):
    """フロントエンドからの登録完了リクエスト"""

    firebase_uid: str
    email: EmailStr
    name: str
    organization_id: int = DEFAULT_ORGANIZATION_ID
    role_id: int = None

    @validator("name")
    def validate_name(cls, v):
        if len(v.strip()) < MIN_NAME_LENGTH:
            raise ValueError("名前は2文字以上で入力してください")
        return v.strip()


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
    """メール確認状態をチェック"""
    try:
        firebase_user = firebase_auth.get_user_by_email(email)
        return {
            "email_verified": firebase_user.email_verified,
            "uid": firebase_user.uid,
        }
    except firebase_auth.UserNotFoundError:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません")
    except Exception as e:
        logger.error(f"確認状態チェックエラー: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="確認状態のチェックに失敗しました")


class ResendVerificationRequest(BaseModel):
    """メール確認再送信リクエスト"""

    email: EmailStr


@router.post("/resend-verification")
async def resend_verification_email(request: ResendVerificationRequest):
    """確認メール再送信エンドポイント"""
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

        # Generate email verification link using Firebase Admin SDK
        # This works without requiring the user to be authenticated
        action_code_settings = firebase_auth.ActionCodeSettings(
            # URL to redirect to after verification
            # Frontend should handle this redirect appropriately
            url=f"{request.email}",  # This will be replaced by the frontend URL
        )

        # Generate custom token for temporary authentication
        custom_token = firebase_auth.create_custom_token(firebase_user.uid)

        return {
            "success": True,
            "message": "Verification email will be sent by client",
            "custom_token": custom_token.decode("utf-8"),
            "already_verified": False,
        }

    except firebase_auth.UserNotFoundError:
        raise HTTPException(status_code=404, detail="User not found")
    except Exception as e:
        logger.error(f"Failed to resend verification email: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail="Failed to resend verification email"
        )
