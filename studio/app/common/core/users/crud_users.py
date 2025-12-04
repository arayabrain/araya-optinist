from datetime import datetime, timezone

from fastapi import HTTPException
from fastapi_pagination.ext.sqlmodel import paginate
from firebase_admin import auth as firebase_auth
from firebase_admin.auth import UserRecord
from firebase_admin.exceptions import FirebaseError
from sqlalchemy import func
from sqlalchemy.orm import aliased
from sqlmodel import Session, select

from studio.app.common.core.auth.auth import authenticate_user
from studio.app.common.core.auth.auth_email_service import AuthEmailService
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.storage.remote_storage_controller import (
    RemoteStorageController,
    RemoteStorageSimpleWriter,
)
from studio.app.common.core.subscription.stripe_service import StripeService
from studio.app.common.core.subscription.subscription_service import (
    SubscriptionService,
    SubscriptionUserStatus,
)
from studio.app.common.core.workspace.workspace_services import WorkspaceService
from studio.app.common.models import Role as RoleModel
from studio.app.common.models import User as UserModel
from studio.app.common.models import UserRole as UserRoleModel
from studio.app.common.models.experiment import ExperimentRecord
from studio.app.common.models.subscription import (
    PlanName,
    SubscriptionPeriods,
    SubscriptionPlanIds,
    SubscriptionPlans,
    SubscriptionStatus,
    UserStorageUsage,
    UserSubscription,
)
from studio.app.common.models.workspace import Workspace
from studio.app.common.schemas.auth import UserAuth
from studio.app.common.schemas.base import SortOptions
from studio.app.common.schemas.users import (
    User,
    UserCreate,
    UserPasswordUpdate,
    UserRole,
    UserSearchOptions,
    UserUpdate,
)

logger = AppLogger.get_logger()


async def set_role(db: Session, user_id: int, role_id: int, auto_commit=True):
    db.query(UserRoleModel).filter_by(user_id=user_id).delete(synchronize_session=False)
    role_user = UserRoleModel(user_id=user_id, role_id=role_id)
    db.add(role_user)
    db.flush()
    if auto_commit:
        db.commit()


async def get_user(db: Session, user_id: int, organization_id: int) -> User:
    try:
        data = (
            db.query(UserModel, UserRoleModel.role_id)
            .outerjoin(UserRoleModel, UserModel.id == UserRoleModel.user_id)
            .filter(
                UserModel.id == user_id,
                UserModel.active.is_(True),
                UserModel.organization_id == organization_id,
            )
            .first()
        )
        assert data is not None, "User not found"
        user, role_id = data
        user.__dict__["role_id"] = role_id
        return User.from_orm(user)
    except AssertionError as e:
        logger.error(e, exc_info=True)
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(e, exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


async def get_user_with_context(db: Session, user_id: int) -> User:
    """
    Get user with full context including subscription and storage information.
    Similar to list_user but for a single user by ID.
    """
    try:
        # Use the same transformer logic as list_user for consistency
        def user_transformer(items):
            users = []
            for item in items:
                (
                    user,
                    role_id,
                    data_usage,
                    subscription_plan_name,
                    storage_usage_bytes,
                    storage_quota_bytes,
                    subscription_expiration,
                    subscription_plan_id,
                ) = item
                user.__dict__["role_id"] = role_id
                user.__dict__["data_usage"] = data_usage
                user.__dict__["subscription_plan_name"] = (
                    subscription_plan_name or PlanName.FREE.value
                )
                user.__dict__["storage_usage_bytes"] = storage_usage_bytes or 0
                user.__dict__["storage_quota_bytes"] = storage_quota_bytes or 0
                user.__dict__["storage_usage_percent"] = round(
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

                    if subscription_plan_id == SubscriptionPlanIds.FREE:
                        user.__dict__[
                            "subscription_status"
                        ] = SubscriptionStatus.FREE.value
                        user.__dict__["subscription_days_remaining"] = None
                    elif subscription_plan_id == SubscriptionPlanIds.PREMIUM:
                        if days_remaining > 0:
                            user.__dict__[
                                "subscription_status"
                            ] = SubscriptionStatus.PREMIUM.value
                            user.__dict__[
                                "subscription_days_remaining"
                            ] = days_remaining
                        elif days_remaining >= -SubscriptionPeriods.GRACE_PERIOD_DAYS:
                            user.__dict__[
                                "subscription_status"
                            ] = SubscriptionStatus.LIMIT_GRACE.value
                            user.__dict__["subscription_days_remaining"] = (
                                SubscriptionPeriods.GRACE_PERIOD_DAYS + days_remaining
                            )  # Days left in grace period
                        else:
                            user.__dict__[
                                "subscription_status"
                            ] = SubscriptionStatus.EXPIRED.value
                            user.__dict__["subscription_days_remaining"] = None
                    else:
                        user.__dict__["subscription_status"] = (
                            subscription_plan_name or PlanName.UNKNOWN.value
                        )
                        user.__dict__["subscription_days_remaining"] = (
                            days_remaining if days_remaining > 0 else None
                        )
                else:
                    user.__dict__["subscription_status"] = SubscriptionStatus.FREE.value
                    user.__dict__["subscription_days_remaining"] = None

                users.append(user)
            return users

        # Query with the same joins as list_user but filter for single user
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

        WorkspaceCapacity = aliased(workspace_capacity_subq)
        ExperimentCapacity = aliased(experiment_capacity_subq)

        query_result = db.execute(
            select(
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
            .join(UserRoleModel, UserRoleModel.user_id == UserModel.id, isouter=True)
            .join(RoleModel, RoleModel.id == UserRoleModel.role_id, isouter=True)
            .outerjoin(UserSubscription, UserSubscription.user_id == UserModel.id)
            .outerjoin(
                SubscriptionPlans, SubscriptionPlans.id == UserSubscription.plan_id
            )
            .outerjoin(UserStorageUsage, UserStorageUsage.user_id == UserModel.id)
            .filter(
                UserModel.active.is_(True),
                UserModel.id == user_id,
            )
            .group_by(UserModel.id)
        )

        result = query_result.first()
        if not result:
            raise HTTPException(status_code=404, detail="User not found")

        # Transform the single result using the same logic as list_user
        transformed_users = user_transformer([result])
        return User.from_orm(transformed_users[0])

    except HTTPException:
        raise
    except Exception as e:
        logger.error(e, exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


async def list_user(
    db: Session,
    organization_id: int,
    options: UserSearchOptions,
    sortOptions: SortOptions,
):
    def user_transformer(items):
        users = []
        for item in items:
            (
                user,
                role_id,
                data_usage,
                subscription_plan_name,
                storage_usage_bytes,
                storage_quota_bytes,
                subscription_expiration,
                subscription_plan_id,
            ) = item
            user.__dict__["role_id"] = role_id
            user.__dict__["data_usage"] = data_usage
            user.__dict__["subscription_plan_name"] = (
                subscription_plan_name or PlanName.FREE.value
            )
            user.__dict__["storage_usage_bytes"] = storage_usage_bytes or 0
            user.__dict__["storage_quota_bytes"] = storage_quota_bytes or 0
            user.__dict__["storage_usage_percent"] = round(
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

                if subscription_plan_id == SubscriptionPlanIds.FREE:
                    user.__dict__["subscription_status"] = SubscriptionStatus.FREE.value
                    user.__dict__["subscription_days_remaining"] = None
                elif subscription_plan_id == SubscriptionPlanIds.PREMIUM:
                    if days_remaining > 0:
                        user.__dict__[
                            "subscription_status"
                        ] = SubscriptionStatus.PREMIUM.value
                        user.__dict__["subscription_days_remaining"] = days_remaining
                    elif days_remaining >= -SubscriptionPeriods.GRACE_PERIOD_DAYS:
                        user.__dict__[
                            "subscription_status"
                        ] = SubscriptionStatus.LIMIT_GRACE.value
                        user.__dict__["subscription_days_remaining"] = (
                            SubscriptionPeriods.GRACE_PERIOD_DAYS + days_remaining
                        )  # Days left in grace period
                    else:
                        user.__dict__[
                            "subscription_status"
                        ] = SubscriptionStatus.EXPIRED.value
                        user.__dict__["subscription_days_remaining"] = None
                else:
                    user.__dict__["subscription_status"] = (
                        subscription_plan_name or PlanName.UNKNOWN.value
                    )
                    user.__dict__["subscription_days_remaining"] = (
                        days_remaining if days_remaining > 0 else None
                    )
            else:
                user.__dict__["subscription_status"] = SubscriptionStatus.FREE.value
                user.__dict__["subscription_days_remaining"] = None

            users.append(user)
        return users

    try:
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

        WorkspaceCapacity = aliased(workspace_capacity_subq)
        ExperimentCapacity = aliased(experiment_capacity_subq)

        sa_sort_list = sortOptions.get_sa_sort_list(
            sa_table=UserModel,
            mapping={"role_id": UserRoleModel.role_id, "role": RoleModel.role},
        )
        users = paginate(
            db,
            query=select(
                UserModel,
                func.min(UserRoleModel.role_id),
                (
                    func.coalesce(WorkspaceCapacity.c.input_workspace_capacity, 0)
                    + func.coalesce(ExperimentCapacity.c.experiment_capacity, 0)
                ).label("data_usage"),
                SubscriptionPlans.name,
                UserStorageUsage.storage_usage_bytes,
                UserStorageUsage.storage_quota_bytes,
                UserSubscription.expiration,
                UserSubscription.plan_id,
            )
            .outerjoin(WorkspaceCapacity, WorkspaceCapacity.c.user_id == UserModel.id)
            .outerjoin(ExperimentCapacity, ExperimentCapacity.c.user_id == UserModel.id)
            .join(UserRoleModel, UserRoleModel.user_id == UserModel.id, isouter=True)
            .join(RoleModel, RoleModel.id == UserRoleModel.role_id, isouter=True)
            .outerjoin(UserSubscription, UserSubscription.user_id == UserModel.id)
            .outerjoin(
                SubscriptionPlans, SubscriptionPlans.id == UserSubscription.plan_id
            )
            .outerjoin(UserStorageUsage, UserStorageUsage.user_id == UserModel.id)
            .filter(
                UserModel.active.is_(True),
                UserModel.organization_id == organization_id,
            )
            .filter(
                UserModel.name.like("%{0}%".format(options.name)),
                UserModel.email.like("%{0}%".format(options.email)),
            )
            .group_by(
                UserModel.id,
                SubscriptionPlans.name,
                UserStorageUsage.storage_usage_bytes,
                UserStorageUsage.storage_quota_bytes,
                UserSubscription.expiration,
                UserSubscription.plan_id,
            )
            .order_by(*sa_sort_list),
            transformer=user_transformer,
            unique=False,
        )
        return users
    except Exception as e:
        logger.error(e, exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


async def create_user(
    db: Session, data: UserCreate, organization_id: int, verified=False
):
    firebase_user = None

    try:
        if not verified:
            data.role_id = UserRole.operator.value

        # Create Firebase user with email NOT verified
        try:
            firebase_user: UserRecord = firebase_auth.create_user(
                email=data.email,
                password=data.password,
                email_verified=verified,
            )
        except FirebaseError as firebase_error:
            # Handle specific Firebase authentication errors
            error_code = (
                firebase_error.code if hasattr(firebase_error, "code") else None
            )
            error_message = str(firebase_error)

            logger.error(
                f"Firebase error during user creation: {error_code} - {error_message}"
            )

            # Map Firebase error codes to user-friendly messages
            if (
                error_code == "EMAIL_ALREADY_EXISTS"
                or "email-already-exists" in error_message.lower()
            ):
                raise HTTPException(
                    status_code=400,
                    detail="This email address is already registered. "
                    "Please use a different email or try logging in.",
                )
            elif (
                error_code == "INVALID_EMAIL"
                or "invalid-email" in error_message.lower()
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Invalid email address format. "
                    "Please provide a valid email.",
                )
            elif (
                error_code == "WEAK_PASSWORD"
                or "weak-password" in error_message.lower()
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Password is too weak. "
                    "It must be at least 6 characters long.",
                )
            elif (
                error_code == "OPERATION_NOT_ALLOWED"
                or "operation-not-allowed" in error_message.lower()
            ):
                raise HTTPException(
                    status_code=403,
                    detail="User registration is currently disabled. "
                    "Please contact support.",
                )
            else:
                # Generic Firebase error
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to create user account: {error_message}",
                )

        # Create application DB user
        user_db = UserModel(
            uid=firebase_user.uid,
            email=firebase_user.email,
            name=data.name,
            organization_id=organization_id,
            active=True,
        )
        db.add(user_db)
        db.flush()  # Get user_db.id

        await set_role(db, user_id=user_db.id, role_id=data.role_id, auto_commit=False)

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

        # Create subscription user record
        # expiration is set to current time for free plan.
        # Since its non nullable and must have a value
        subscription = UserSubscription(
            plan_id=SubscriptionUserStatus.FREE,
            user_id=user_db.id,
            expiration=SubscriptionService.get_current_datetime(),
        )
        db.add(subscription)

        # Commit all changes
        db.commit()

        # Refresh user_db to load relationships (especially organization)
        db.refresh(user_db)

        # Add role_id for response (if needed)
        user_db.__dict__["role_id"] = data.role_id

        # Send verification email if user is not verified
        if not verified:
            try:
                AuthEmailService.send_verification_email(data.email)
                logger.info(f"Verification email sent to {data.email}")
            except Exception as email_error:
                logger.error(
                    f"Failed to send verification email: {email_error}", exc_info=True
                )
                # Don't fail user creation if email fails
                # The user can request a resend later

        return {
            "user": User.from_orm(user_db),
        }

    except HTTPException:
        # Re-raise HTTPException as-is (from Firebase error handling)
        db.rollback()
        if firebase_user:
            try:
                firebase_auth.delete_user(firebase_user.uid)
                logger.info(f"Cleaned up Firebase user: {firebase_user.uid}")
            except Exception as cleanup_error:
                logger.error(f"Failed to cleanup Firebase user: {cleanup_error}")
        raise
    except Exception as e:
        logger.error(f"Failed to create user: {e}", exc_info=True)

        # Rollback database
        db.rollback()

        # Cleanup Firebase user if created
        if firebase_user:
            try:
                firebase_auth.delete_user(firebase_user.uid)
                logger.info(f"Cleaned up Firebase user: {firebase_user.uid}")
            except Exception as cleanup_error:
                logger.error(f"Failed to cleanup Firebase user: {cleanup_error}")

        # Return appropriate error
        if isinstance(e, ValueError):
            raise HTTPException(status_code=400, detail=str(e))
        raise HTTPException(status_code=500, detail="Failed to create user")


async def update_user(
    db: Session, user_id: int, data: UserUpdate, organization_id: int
):
    try:
        # update application db user
        user_db = (
            db.query(UserModel)
            .filter(
                UserModel.active.is_(True),
                UserModel.id == user_id,
                UserModel.organization_id == organization_id,
            )
            .first()
        )
        assert user_db is not None, "User not found"
        user_data = data.dict(exclude_unset=True)
        role_id = user_data.pop("role_id", None)
        for key, value in user_data.items():
            setattr(user_db, key, value)
        if role_id is not None:
            await set_role(db, user_id=user_db.id, role_id=role_id, auto_commit=False)
            user_db.__dict__["role_id"] = role_id

        # create firebase user
        firebase_auth.update_user(user_db.uid, email=data.email)

        db.commit()

        # Refresh user_db to ensure relationships are loaded
        db.refresh(user_db)

        return User.from_orm(user_db)
    except AssertionError as e:
        logger.error(e, exc_info=True)
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(e, exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


async def update_password(
    db: Session,
    user_id: int,
    data: UserPasswordUpdate,
    organization_id: int,
):
    user = await get_user(db, user_id, organization_id)
    await authenticate_user(
        db, data=UserAuth(email=user.email, password=data.old_password)
    )
    try:
        user = firebase_auth.update_user(user.uid, password=data.new_password)
        return True
    except Exception as e:
        logger.error(e, exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


async def delete_user(db: Session, user_id: int, organization_id: int) -> bool:
    try:
        # delete application db user
        user_db: User = (
            db.query(UserModel)
            .filter(
                UserModel.active.is_(True),
                UserModel.id == user_id,
                UserModel.organization_id == organization_id,
            )
            .first()
        )
        assert user_db is not None, "User not found"

        # ----------------------------------------
        # Delete a User workspace contents
        # ----------------------------------------

        workspaces = (
            db.query(Workspace)
            .filter(
                Workspace.user_id == user_id,
                Workspace.deleted.is_(False),
            )
            .all()
        )
        workspace_ids = [ws.id for ws in workspaces]

        # Delete owned workspaces
        for workspace_id in workspace_ids:
            await WorkspaceService.process_workspace_deletion(
                db, user_db.remote_bucket_name, workspace_id, user_id
            )

        # ----------------------------------------
        # Delete a User remote storage data
        # ----------------------------------------

        # delete remote_storage bucket
        if RemoteStorageController.is_available():
            async with RemoteStorageSimpleWriter(
                user_db.remote_bucket_name
            ) as remote_storage_controller:
                await remote_storage_controller.delete_bucket(force_delete=True)

        # ----------------------------------------
        # Cancel a User subscription
        # ----------------------------------------

        await StripeService.handle_cancel_user_subscription(db, user_db)

        # ----------------------------------------
        # Delete a User database record
        # ----------------------------------------

        user_db.active = False

        # ----------------------------------------
        # Delete a User firebase account
        # ----------------------------------------

        firebase_auth.delete_user(user_db.uid)

        # The transaction is committed at this point
        # ATTENTION:
        #   - If an exception occurs when deleting a Firebase account,
        #     this commit may not be executed and the account may become undeletable.
        #   - One possible solution to this issue is to add a status
        #     when an error occurs (such as "Account suspended").
        db.commit()

        return True

    except AssertionError as e:
        logger.error(e, exc_info=True)
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(e, exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
