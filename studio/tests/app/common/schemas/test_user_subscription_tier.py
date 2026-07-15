"""
Tests for User.has_active_subscription and User.subscription_type properties.

Verifies that Limit Grace users are treated as free tier (no premium instance
access), while retaining Premium plan name for data retention purposes.
"""

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from studio.app.common.core.subscription.constants import (
    PlanName,
    SubscriptionStatus,
    SubscriptionType,
)
from studio.app.common.schemas.users import User


def _make_user(plan_name: str, status: str) -> User:
    return User(
        id=1,
        uid="test-uid",
        name="Test",
        email="test@example.com",
        organization={"id": 1, "name": "org"},
        role_id=20,
        data_usage=0,
        attributes={},
        subscription_plan_name=plan_name,
        subscription_status=status,
    )


class TestHasActiveSubscription:
    def test_premium_active(self):
        user = _make_user(PlanName.PREMIUM.value, SubscriptionStatus.PREMIUM.value)
        assert user.has_active_subscription is True
        assert user.subscription_type == SubscriptionType.PREMIUM.value

    def test_limit_grace_is_not_active(self):
        user = _make_user(PlanName.PREMIUM.value, SubscriptionStatus.LIMIT_GRACE.value)
        assert user.has_active_subscription is False
        assert user.subscription_type == SubscriptionType.FREE.value

    def test_expired_is_not_active(self):
        user = _make_user(PlanName.PREMIUM.value, SubscriptionStatus.EXPIRED.value)
        assert user.has_active_subscription is False
        assert user.subscription_type == SubscriptionType.FREE.value

    def test_free_plan_is_not_active(self):
        user = _make_user(PlanName.FREE.value, SubscriptionStatus.FREE.value)
        assert user.has_active_subscription is False
        assert user.subscription_type == SubscriptionType.FREE.value


class TestGetUserSubscriptionPlan:
    """get_user_subscription_plan() must treat Limit Grace as free tier."""

    @staticmethod
    def _patch_user(user):
        @contextmanager
        def _scope():
            yield MagicMock()

        return (
            patch(
                "studio.app.common.core.cloud.cloud_utils.session_scope",
                _scope,
            ),
            patch(
                "studio.app.common.core.users.crud_users.get_user_with_context",
                new=AsyncMock(return_value=user),
            ),
        )

    @pytest.mark.asyncio
    async def test_limit_grace_user_is_not_premium(self):
        from studio.app.common.core.cloud.cloud_utils import get_user_subscription_plan

        user = _make_user(PlanName.PREMIUM.value, SubscriptionStatus.LIMIT_GRACE.value)
        scope_p, crud_p = self._patch_user(user)
        with scope_p, crud_p:
            result = await get_user_subscription_plan(user_id=1)

        assert result["is_premium"] is False
        assert result["tier"] == SubscriptionType.FREE
        assert result["has_active_subscription"] is False

    @pytest.mark.asyncio
    async def test_active_premium_user_is_premium(self):
        from studio.app.common.core.cloud.cloud_utils import get_user_subscription_plan

        user = _make_user(PlanName.PREMIUM.value, SubscriptionStatus.PREMIUM.value)
        scope_p, crud_p = self._patch_user(user)
        with scope_p, crud_p:
            result = await get_user_subscription_plan(user_id=1)

        assert result["is_premium"] is True
        assert result["tier"] == SubscriptionType.PREMIUM
