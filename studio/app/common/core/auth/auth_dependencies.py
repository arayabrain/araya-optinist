import logging
import os
from datetime import datetime, timezone
from typing import Optional

import sqlalchemy
from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError
from sqlalchemy import func
from sqlalchemy.orm import aliased
from sqlmodel import Session, select

from studio.app.common.core.auth.auth_config import AUTH_CONFIG
from studio.app.common.core.auth.auth_helper import (
    extract_uid_from_firebase_credential,
    extract_uid_from_jwt_token,
)
from studio.app.common.core.dataview.dataview_services import DataviewService
from studio.app.common.core.mode import MODE
from studio.app.common.core.storage.remote_storage_controller import RemoteStorageType
from studio.app.common.db.database import get_db
from studio.app.common.models import User as UserModel
from studio.app.common.models import UserRole as UserRoleModel
from studio.app.common.models.experiment import ExperimentRecord
from studio.app.common.models.subscription import (
    SubscriptionPlans,
    UserStorageUsage,
    UserSubscription,
)
from studio.app.common.models.workspace import Workspace
from studio.app.common.schemas.users import User


async def get_current_user_with_dataview_outputs_check(
    req: Request,
    res: Response,
    ex_token: Optional[str] = Depends(APIKeyHeader(name="ExToken", auto_error=False)),
    credential: HTTPAuthorizationCredentials = Depends(HTTPBearer(auto_error=False)),
    db: Session = Depends(get_db),
) -> User:
    """
    Authentication process specialized for outputs requests from the dataview screen
    """

    # Checks whether public outputs are accessed from a dataview
    if DataviewService.is_dataview_public_outputs_request(req):
        is_allowed_access = DataviewService.validate_dataview_public_outputs_request(
            req, db
        )

        if is_allowed_access:
            # To access public resources, skip authentication (return)
            return
        else:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This resource is not publicly available.",
            )

    # Fallback to get_current_user()
    return await get_current_user(res, ex_token, credential, db)


async def get_current_user(
    res: Response,
    ex_token: Optional[str] = Depends(APIKeyHeader(name="ExToken", auto_error=False)),
    credential: HTTPAuthorizationCredentials = Depends(HTTPBearer(auto_error=False)),
    db: Session = Depends(get_db),
) -> User:
    use_firebase_auth = AUTH_CONFIG.USE_FIREBASE_TOKEN
    try:
        assert credential is not None if use_firebase_auth else True
        assert ex_token is not None if not use_firebase_auth else True

        # Extract uid using helper functions
        uid = None
        err = None
        if use_firebase_auth:
            uid, err = extract_uid_from_firebase_credential(credential)
        else:
            uid, err = extract_uid_from_jwt_token(ex_token)

        assert err is None, str(err)
        assert uid is not None, "Failed to extract user ID"

        # Query user record
        user_data = __get_current_user_record(db, uid)
        assert user_data is not None, "Invalid user data"
        (
            authed_user,
            role_id,
            data_usage,
            subscription_plan_name,
            storage_usage_bytes,
            storage_quota_bytes,
            subscription_expiration,
            subscription_plan_id,
        ) = user_data

        authed_user.__dict__["role_id"] = role_id
        authed_user.__dict__["data_usage"] = data_usage
        authed_user.__dict__["subscription_plan_name"] = (
            subscription_plan_name or "Free"
        )
        authed_user.__dict__["storage_usage_bytes"] = storage_usage_bytes or 0
        authed_user.__dict__["storage_quota_bytes"] = storage_quota_bytes or 0
        authed_user.__dict__["storage_usage_percent"] = round(
            (storage_usage_bytes or 0) / (storage_quota_bytes or 1) * 100, 2
        )

        # Calculate subscription status and days remaining
        now = datetime.now(timezone.utc)
        if subscription_expiration and subscription_plan_id:
            # Make sure expiration is timezone-aware
            if subscription_expiration.tzinfo is None:
                subscription_expiration = subscription_expiration.replace(
                    tzinfo=timezone.utc
                )

            days_remaining = (subscription_expiration - now).days

            if subscription_plan_id == 1:  # Free plan
                authed_user.__dict__["subscription_status"] = "Free"
                authed_user.__dict__["subscription_days_remaining"] = None
            elif subscription_plan_id == 2:  # Premium plan
                if days_remaining > 0:
                    authed_user.__dict__["subscription_status"] = "Premium"
                    authed_user.__dict__["subscription_days_remaining"] = days_remaining
                elif days_remaining >= -30:  # Grace period (30 days after expiration)
                    authed_user.__dict__["subscription_status"] = "Limit Grace"
                    authed_user.__dict__["subscription_days_remaining"] = (
                        30 + days_remaining
                    )  # Days left in grace period
                else:
                    authed_user.__dict__["subscription_status"] = "Expired"
                    authed_user.__dict__["subscription_days_remaining"] = None
            else:
                authed_user.__dict__["subscription_status"] = (
                    subscription_plan_name or "Unknown"
                )
                authed_user.__dict__["subscription_days_remaining"] = (
                    days_remaining if days_remaining > 0 else None
                )
        else:
            authed_user.__dict__["subscription_status"] = "Free"
            authed_user.__dict__["subscription_days_remaining"] = None

        return User.from_orm(authed_user)

    except ValidationError as e:
        logging.getLogger().error(e)
        raise HTTPException(status_code=422, detail=f"Validator Error: {e}")
    except Exception as e:
        logging.getLogger().error(e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": 'Bearer realm="auth_required"'},
            detail=str(e) or "Could not validate credentials",
        )


def __get_current_user_record(db: Session, uid: str) -> sqlalchemy.engine.row.Row:
    # Make query (calc workspace capacity)
    workspace_capacity_subq = (
        select(
            Workspace.user_id,
            func.coalesce(func.sum(Workspace.input_data_usage), 0).label(
                "input_workspace_capacity"
            ),
        )
        .where(Workspace.deleted.is_(False))
        .group_by(Workspace.user_id)
        .subquery()
    )
    WorkspaceCapacity = aliased(workspace_capacity_subq)

    # Make query (calc experient capacity)
    experiment_capacity_subq = (
        select(
            Workspace.user_id,
            func.coalesce(func.sum(ExperimentRecord.data_usage), 0).label(
                "experiment_capacity"
            ),
        )
        .join(ExperimentRecord, ExperimentRecord.workspace_id == Workspace.id)
        .where(Workspace.deleted.is_(False))
        .group_by(Workspace.user_id)
        .subquery()
    )
    ExperimentCapacity = aliased(experiment_capacity_subq)

    user_data = (
        db.query(
            UserModel,
            func.min(UserRoleModel.role_id),
            func.coalesce(WorkspaceCapacity.c.input_workspace_capacity, 0)
            + func.coalesce(ExperimentCapacity.c.experiment_capacity, 0).label(
                "data_usage"
            ),
            func.max(SubscriptionPlans.name).label("subscription_plan_name"),
            UserStorageUsage.storage_usage_bytes,
            UserStorageUsage.storage_quota_bytes,
            func.max(UserSubscription.expiration).label("subscription_expiration"),
            func.max(UserSubscription.plan_id).label("subscription_plan_id"),
        )
        .outerjoin(WorkspaceCapacity, WorkspaceCapacity.c.user_id == UserModel.id)
        .outerjoin(ExperimentCapacity, ExperimentCapacity.c.user_id == UserModel.id)
        .outerjoin(UserRoleModel, UserRoleModel.user_id == UserModel.id)
        .outerjoin(UserSubscription, UserSubscription.user_id == UserModel.id)
        .outerjoin(SubscriptionPlans, SubscriptionPlans.id == UserSubscription.plan_id)
        .outerjoin(UserStorageUsage, UserStorageUsage.user_id == UserModel.id)
        .filter(UserModel.uid == uid)
        .group_by(UserModel.id)
        .first()
    )

    return user_data


async def get_admin_user(current_user: User = Depends(get_current_user)) -> User:
    if current_user.is_admin:
        return current_user
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient privileges",
        )


async def get_user_remote_bucket_name(
    current_user: User = Depends(get_current_user),
) -> str:
    """
    get user remote_bucket_name from users.attributes
    """
    return _get_user_remote_bucket_name(current_user)


def _get_user_remote_bucket_name(
    current_user: User = None,
) -> str:
    """
    get user remote_bucket_name from users.attributes
    """

    if current_user:
        remote_bucket_name = current_user.remote_bucket_name
    else:
        remote_bucket_name = None

    if not remote_bucket_name:
        remote_storage_type = RemoteStorageType.get_activated_type()

        if MODE.IS_TEST:
            remote_bucket_name = os.environ.get(
                "S3_DEFAULT_BUCKET_NAME", "TEST_DUMMY_BUCKET_NAME"
            )
        elif remote_storage_type == RemoteStorageType.S3:
            remote_bucket_name = os.environ.get("S3_DEFAULT_BUCKET_NAME")
        else:
            remote_bucket_name = "MOCK_DUMMY_BUCKET_NAME"

    assert remote_bucket_name, f"Invalid remote_bucket_name: {remote_bucket_name}"

    return remote_bucket_name
