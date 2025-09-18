import json
import logging
from typing import Tuple

from fastapi import HTTPException, status
from fastapi.responses import JSONResponse
from firebase_admin import auth
from requests.exceptions import HTTPError
from sqlmodel import Session

from studio.app.common.core.auth import pyrebase_app
from studio.app.common.core.auth.auth_dependencies import get_admin_user
from studio.app.common.core.auth.security import (
    create_access_token,
    create_refresh_token,
    validate_refresh_token,
)
from studio.app.common.core.logger import AppLogger
from studio.app.common.models.user import User as UserModel
from studio.app.common.schemas.auth import AccessToken, Token, UserAuth
from studio.app.common.schemas.users import User


async def authenticate_user(db: Session, data: UserAuth) -> Tuple[Token, UserModel]:
    try:
        user = pyrebase_app.auth().sign_in_with_email_and_password(
            data.email, data.password
        )
        logger = AppLogger.get_logger()
        user_db: UserModel = (
            db.query(UserModel)
            .filter(UserModel.uid == user["localId"], UserModel.active.is_(True))
            .first()
        )
        if user_db is None:
            logger.error(f"No database user found with uid: {user['localId']}")
        else:
            logger.debug(
                f"Found database user: {user_db.email} with uid: {user_db.uid}"
            )

        assert user_db is not None, "Invalid user uid"

        # Auto-assign premium users to dedicated instances
        try:
            from studio.app.common.core.premium.premium_assignment_service import (
                premium_assignment_service,
            )
            from studio.app.common.core.users import crud_users

            # Get user with subscription details
            user_with_context = await crud_users.get_user_with_context(db, user_db.id)

            # Check if user has active premium subscription
            if (
                user_with_context
                and user_with_context.subscription_plan_name == "Premium"
                and user_with_context.subscription_status in ["Premium", "Limit Grace"]
            ):
                logger.info(
                    f"Auto-assigning premium user {user_db.id} to "
                    f"dedicated instance"
                )

                # Try to assign premium user (async, non-blocking)
                assignment_result = (
                    await premium_assignment_service.assign_premium_user(user_db.id)
                )

                if assignment_result["success"]:
                    logger.info(
                        f"Successfully auto-assigned premium user {user_db.id} to "
                        f"instance {assignment_result.get('instance_id')}"
                    )
                elif assignment_result.get("requires_retry"):
                    logger.info(
                        f"Premium instance starting for user {user_db.id}, "
                        f"will be available in "
                        f"{assignment_result.get('retry_after', 120)} seconds"
                    )
                else:
                    logger.warning(
                        f"Failed to auto-assign premium user {user_db.id}: "
                        f"{assignment_result.get('message')}"
                    )

        except Exception as e:
            # Don't fail login if premium assignment fails
            logger.warning(
                f"Error during premium auto-assignment for user "
                f"{user_db.id}: {str(e)}"
            )

        ex_token = create_access_token(subject=user_db.uid)
        token = Token(
            access_token=user["idToken"],
            refresh_token=create_refresh_token(subject=user["refreshToken"]),
            token_type="bearer",
            ex_token=ex_token,
        )
        return token, user_db

    except (HTTPError, AssertionError) as e:
        logging.getLogger().error(e)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    except Exception as e:
        logging.getLogger().error(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )


async def refresh_current_user_token(refresh_token: str):
    token, err = validate_refresh_token(refresh_token)

    if err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)
    try:
        user = pyrebase_app.auth().refresh(refresh_token=token["sub"])
        return AccessToken(access_token=user["idToken"])
    except Exception as e:
        logging.getLogger().error(e)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)


async def send_reset_password_mail(db: Session, email: str):
    try:
        db.query(UserModel).filter(
            UserModel.email == email, UserModel.active.is_(True)
        ).one()
        pyrebase_app.auth().send_password_reset_email(email)
        return JSONResponse(content=None, status_code=status.HTTP_200_OK)
    except HTTPError as e:
        logging.getLogger().error(e)
        err = json.loads(e.strerror)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=err.get("error").get("message"),
        )
    except Exception as e:
        logging.getLogger().error(e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


async def login_with_uid(db: Session, uid: str, current_user: User) -> Token:
    _ = get_admin_user(current_user)
    try:
        user_db = (
            db.query(UserModel)
            .filter(UserModel.uid == uid, UserModel.active.is_(True))
            .first()
        )
        assert user_db is not None, "Invalid user uid"

        token = auth.create_custom_token(uid)
        user = pyrebase_app.auth().sign_in_with_custom_token(token.decode())

        ex_token = create_access_token(uid)
        token = Token(
            access_token=user["idToken"],
            refresh_token=create_refresh_token(subject=user["refreshToken"]),
            token_type="bearer",
            ex_token=ex_token,
        )

        return token

    except (HTTPError, AssertionError) as e:
        logging.getLogger().error(e)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    except Exception as e:
        logging.getLogger().error(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )
