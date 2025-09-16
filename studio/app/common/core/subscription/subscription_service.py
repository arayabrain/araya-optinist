import os
from datetime import datetime
from enum import Enum
from typing import List

from sqlalchemy import and_
from sqlmodel import Session

from studio.app.common import models as common_model
from studio.app.common.models.subscription import SubscriptionPlans


class SubscriptionStatusType(Enum):
    ACTIVE = "1"
    INACTIVE = "0"


class SubscriptionCurrencyType(Enum):
    USD = 1
    JPY = 2


class SubscriptionService:
    @staticmethod
    def get_active_plans(db: Session) -> List[SubscriptionPlans]:
        return (
            db.query(SubscriptionPlans)
            .filter(SubscriptionPlans.status == SubscriptionStatusType.ACTIVE.value)
            .all()
        )

    @staticmethod
    def get_plan_by_id(db: Session, plan_id: int) -> SubscriptionPlans:
        return (
            db.query(SubscriptionPlans).filter(SubscriptionPlans.id == plan_id).first()
        )

    @staticmethod
    def get_user_subscription_plan(
        db: Session, user_id: int
    ) -> List[SubscriptionPlans]:
        return (
            db.query(common_model.UserSubscription, common_model.SubscriptionPlans)
            .join(
                common_model.SubscriptionPlans,
                common_model.UserSubscription.plan_id
                == common_model.SubscriptionPlans.id,
            )
            .join(
                common_model.User,
                common_model.UserSubscription.user_id == common_model.User.id,
            )
            .filter(
                and_(
                    common_model.UserSubscription.user_id == user_id,
                    common_model.UserSubscription.expiration > datetime.now(),
                    common_model.User.active.is_(True),
                )
            )
            .order_by(common_model.UserSubscription.expiration.desc())
            .first()
        )

    @staticmethod
    def get_user_expired_subscription(
        db: Session, user_id: int
    ) -> common_model.UserSubscription:
        return (
            db.query(
                common_model.UserSubscription,
                common_model.SubscriptionPlans,
                common_model.User,
            )
            .join(
                common_model.SubscriptionPlans,
                common_model.UserSubscription.plan_id
                == common_model.SubscriptionPlans.id,
            )
            .join(
                common_model.User,
                common_model.UserSubscription.user_id == common_model.User.id,
            )
            .filter(
                common_model.UserSubscription.user_id == user_id,
                common_model.User.active.is_(True),
            )
            .order_by(common_model.UserSubscription.expiration.desc())
            .first()
        )

    @staticmethod
    def get_stripe_key() -> str:
        stripe_key = os.getenv("STRIPE_SECRET_KEY")
        if not stripe_key:
            raise ValueError("STRIPE_SECRET_KEY environment variable is not set")
        return stripe_key

    @staticmethod
    def get_base_url() -> str:
        base_url = os.getenv("STRIPE_CALLBACK_URL")
        if not base_url:
            raise ValueError("STRIPE_CALLBACK_URL environment variable is not set")
        return base_url
