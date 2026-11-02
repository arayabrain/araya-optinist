from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlmodel import Session

from studio.app.common.core.auth import auth
from studio.app.common.core.auth.auth_config import AUTH_CONFIG
from studio.app.common.core.auth.auth_dependencies import get_admin_user
from studio.app.common.core.cloud.cloud_utils import (
    calculate_limit_warning,
    ensure_user_bucket_exists,
)
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.middleware.user_activity_middleware import (
    clear_free_user_logged_out_at,
    clear_logged_out_status,
)
from studio.app.common.db.database import get_db
from studio.app.common.schemas.auth import AccessToken, RefreshToken, Token, UserAuth
from studio.app.const import FRONTEND_URL

router = APIRouter(prefix="/auth", tags=["auth"])

logger = AppLogger.get_logger()

REFRESH_COOKIE = "refresh_token"
# Sent only by an SPA that also calls /auth/logout, so no cookie outlives a logout.
REFRESH_COOKIE_OPT_IN = "x-refresh-cookie"


def _is_own_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    return (
        origin is None
        or origin == FRONTEND_URL
        or urlsplit(origin).netloc == request.headers.get("host")
    )


def _cookie_attrs(request: Request) -> dict:
    # TLS ends at the ALB, so the scheme uvicorn sees is http.
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return dict(
        path=router.prefix, secure=proto == "https", httponly=True, samesite="strict"
    )


def _set_refresh_cookie(request: Request, response: Response, token: str) -> None:
    if request.headers.get(REFRESH_COOKIE_OPT_IN) == "1" and _is_own_origin(request):
        response.set_cookie(
            REFRESH_COOKIE,
            token,
            max_age=AUTH_CONFIG.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
            **_cookie_attrs(request),
        )


@router.post("/login", response_model=Token)
async def login(
    user_data: UserAuth,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    try:
        token, user = await auth.authenticate_user(db, user_data)

        # Clear logged_out_at for free users to prevent cleanup job from
        # deleting their data after re-login
        try:
            clear_logged_out_status(user.id)
            clear_free_user_logged_out_at(user.id)
        except Exception as e:
            logger.warning(f"Failed to clear logout status for user {user.id}: {e}")

        # Ensure user's S3 bucket exists on sign-in
        try:
            bucket = await ensure_user_bucket_exists(user.id)
            if bucket:
                logger.info(f"Bucket recovery on login for user {user.id}: {bucket}")
        except Exception as bucket_error:
            logger.warning(f"Bucket check failed for user {user.id}: {bucket_error}")

        # Check for limit warnings after successful login
        try:
            limit_warning = await calculate_limit_warning(user.id)
            if limit_warning:
                logger.warning(
                    f"User {user.id} ({user.email}) has limit warning: "
                    f"{limit_warning.alert_type} - "
                    f"{limit_warning.days_remaining} days remaining, "
                    f"{limit_warning.excess_data_gb} GB over limit"
                )
            else:
                logger.debug(f"No limit warning for user {user.id}")
        except Exception as warning_error:
            # Don't fail login due to warning check failure
            logger.warning(
                f"Failed to check limit warning for user " f"{user.id}: {warning_error}"
            )

    except HTTPException:
        # The auth layer already logged the cause; a 4xx (e.g. a mistyped
        # password) is routine, so re-raise without a second ERROR traceback.
        raise

    except Exception as e:
        logger.error(e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Some error occurred during authentication.",
        )

    _set_refresh_cookie(request, response, token.refresh_token)
    return token


@router.post("/refresh", response_model=AccessToken)
async def refresh(request: Request, refresh_token: RefreshToken = RefreshToken()):
    # A body token is never copied into the cookie: only login mints one.
    token = refresh_token.refresh_token
    if not token and _is_own_origin(request):
        token = request.cookies.get(REFRESH_COOKIE)
    return await auth.refresh_current_user_token(token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request):
    if not _is_own_origin(request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(REFRESH_COOKIE, **_cookie_attrs(request))
    return response


@router.post("/send_reset_password_mail")
async def send_reset_password_mail(email: str, db: Session = Depends(get_db)):
    return await auth.send_reset_password_mail(db, email)


@router.post("/proxy-login/{uid}", response_model=Token)
async def login_with_uid(
    uid: str,
    request: Request,
    response: Response,
    admin_user=Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    try:
        token = await auth.login_with_uid(db, uid, admin_user)
    except HTTPException:
        # The auth layer already logged the cause; a 4xx (e.g. a mistyped
        # password) is routine, so re-raise without a second ERROR traceback.
        raise

    except Exception as e:
        logger.error(e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Some error occurred during authentication.",
        )

    _set_refresh_cookie(request, response, token.refresh_token)
    return token
