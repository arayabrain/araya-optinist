"""`GET /api/subsc/mgmts` reports a running free trial from the stored trial end."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from studio.__main_unit__ import app
from studio.app.common.core.auth.auth_dependencies import get_current_user
from studio.app.common.core.subscription.constants import (
    SubscriptionPlanIds,
    SyncStatus,
)
from studio.app.common.db.database import get_db
from studio.app.common.models.subscription import UserSubscription

URL = "/api/subsc/mgmts"
SERVICE = "studio.app.common.routers.subscriptions.SubscriptionService"
# Pinned so the trial boundary does not depend on when the suite runs
NOW = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)


def naive(days_from_now):
    """A stored DATETIME: naive UTC, as MySQL returns it."""
    return (NOW + timedelta(days=days_from_now)).replace(tzinfo=None)


@pytest.fixture
def client():
    original = app.dependency_overrides.copy()
    app.dependency_overrides[get_current_user] = lambda: MagicMock(id=1)
    app.dependency_overrides[get_db] = lambda: MagicMock()
    yield TestClient(app)
    app.dependency_overrides.clear()
    app.dependency_overrides.update(original)


def get_subscription(client, expiration, trial_end):
    row = UserSubscription(
        id=5,
        plan_id=SubscriptionPlanIds.PREMIUM,
        user_id=1,
        expiration=expiration,
        trial_end=trial_end,
        scheduled_downgrade=False,
        sync_status=SyncStatus.SYNCED,
        created_at=naive(-1),
        updated_at=naive(-1),
    )
    plan = SimpleNamespace(id=SubscriptionPlanIds.PREMIUM, name="Premium", price=5000)
    with patch(f"{SERVICE}.get_user_subscription", return_value=(row, plan)), patch(
        f"{SERVICE}.get_user_subscription_purchase", return_value=MagicMock()
    ), patch(f"{SERVICE}.is_subscription_cancelled", return_value=False), patch(
        f"{SERVICE}.get_current_datetime", return_value=NOW
    ):
        response = client.get(URL)

    assert response.status_code == 200
    body = response.json()
    # The handler answers null when it cannot build the response
    assert body is not None
    return body


def test_a_trial_ending_ahead_is_reported_with_its_end(client):
    body = get_subscription(client, expiration=naive(10), trial_end=naive(10))

    assert body["is_trial"] is True
    assert body["trial_end"].startswith(naive(10).date().isoformat())


@pytest.mark.parametrize(
    "trial_end",
    [
        # Converted to paid: the stored trial end has passed
        pytest.param(naive(-5), id="trial-ended"),
        pytest.param(None, id="no-trial"),
    ],
)
def test_a_paid_period_is_not_a_trial(client, trial_end):
    body = get_subscription(client, expiration=naive(25), trial_end=trial_end)

    assert body["is_trial"] is False
