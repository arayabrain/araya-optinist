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


class CompleteRegistrationRequest(BaseModel):
    """フロントエンドからの登録完了リクエスト"""

    firebase_uid: str
    email: EmailStr
    name: str
    organization_id: int = 1
    role_id: int = None

    @validator("name")
    def validate_name(cls, v):
        if len(v.strip()) < 2:
            raise ValueError("名前は2文字以上で入力してください")
        return v.strip()


@router.post("", response_model=UserCreateResponse)
async def complete_registration(
    request: UserCreate,
    db: Session = Depends(get_db),
):
    return await crud_users.create_user(db, request, organization_id=1, verified=True)


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
