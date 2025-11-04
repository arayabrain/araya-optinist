# studio/app/common/core/users/registration_router.py

from fastapi import APIRouter, Depends, Header, HTTPException
from firebase_admin import auth as firebase_auth
from pydantic import BaseModel, EmailStr, validator
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


def verify_firebase_token(authorization: str = Header(None)) -> dict:
    """Firebase IDトークンを検証"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="認証トークンが必要です")

    token = authorization.split("Bearer ")[1]

    try:
        decoded_token = firebase_auth.verify_id_token(token)
        logger.info(f"✓ Token verified for UID: {decoded_token.get('uid')}")
        return decoded_token
    except Exception as e:
        logger.error(f"トークン検証エラー: {e}")
        raise HTTPException(status_code=401, detail="無効なトークンです")


@router.post("/complete")
async def complete_registration(
    request: CompleteRegistrationRequest,
    db: Session = Depends(get_db),
    authorization: str = Header(None),
):
    logger.info(f"Registration request received for: {request.email}")

    # トークン検証
    decoded_token = verify_firebase_token(authorization)

    # UIDが一致するか確認
    if decoded_token.get("uid") != request.firebase_uid:
        logger.info("UID mismatch in registration request")
        logger.info(
            f"Token UID: {decoded_token.get('uid')},Request UID: {request.firebase_uid}"
        )
        raise HTTPException(status_code=403, detail="UIDが一致しません")

    try:
        # メールアドレスの重複チェック
        existing_user = (
            db.query(UserModel)
            .filter(UserModel.email == request.email.lower(), UserModel.active)
            .first()
        )

        if existing_user:
            logger.info(f"User already exists in DB: {request.email}")
            return {
                "success": True,
                "message": "ユーザーは既に登録されています",
                "user": {
                    "id": existing_user.id,
                    "email": existing_user.email,
                    "name": existing_user.name,
                    "uid": existing_user.uid,
                    "master_key": existing_user.master_key,
                    "email_verified": False,
                },
            }

        # DBにユーザーを作成
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

        # master_keyを生成
        master_key = f"{user_db.id:010d}"
        user_db.master_key = master_key

        # ロール設定
        if request.role_id:
            from studio.app.common.core.users.crud_users import set_role

            await set_role(
                db, user_id=user_db.id, role_id=request.role_id, auto_commit=False
            )

        # リモートストレージバケット作成
        if RemoteStorageController.is_available():
            new_bucket_name = RemoteStorageController.create_user_bucket_name(
                id=user_db.id
            )
            async with RemoteStorageSimpleWriter(
                new_bucket_name
            ) as remote_storage_controller:
                await remote_storage_controller.create_bucket()
            user_db.attributes = {"remote_bucket_name": new_bucket_name}

        # サブスクリプション作成
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
            "message": "ユーザーを作成しました",
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
        raise HTTPException(status_code=500, detail=f"登録に失敗しました: {str(e)}")


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
