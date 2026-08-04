"""Which columns of `subscription_users` a transition changes, and which it must not.

Run against a real session rather than a `Mock(spec=Session)`, where every
column reads back whatever the test assigned.

What this harness cannot show, because SQLite is not MySQL: `SELECT ... FOR
UPDATE` compiles away, so the row lock guarding concurrent upserts is not
exercised; `SQLEnum` emits no CHECK constraint, so an out-of-range `sync_status`
would be accepted; and foreign keys are not enforced, because the parent tables
carry MySQL-only column types SQLite cannot create. Referential integrity
therefore stays a production check. What does hold is column-level: the
ORM-level `onupdate` on `updated_at` fires, so "only these columns moved" is a
real assertion.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import BIGINT as GENERIC_BIGINT
from sqlalchemy import BigInteger
from sqlalchemy.dialects.mysql import BIGINT as MYSQL_BIGINT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from studio.app.common.core.subscription.checkout_service import CheckoutService
from studio.app.common.core.subscription.constants import (
    SubscriptionPeriods,
    SubscriptionPlanIds,
    SubscriptionStatus,
    SyncStatus,
)
from studio.app.common.core.subscription.subscription_service import SubscriptionService
from studio.app.common.core.subscription.webhook_service import WebhookService
from studio.app.common.core.users import crud_users
from studio.app.common.models import User as UserModel
from studio.app.common.models.subscription import (
    SubscriptionUserAccount,
    SubscriptionUserPurchase,
    UserSubscription,
)
from studio.app.common.routers.subscriptions import reactivate_user_subscription


# SQLite only autoincrements an INTEGER PRIMARY KEY, not BIGINT.
# NOTE: @compiles mutates SQLAlchemy's process-global dialect-compiler registry
# on import, not just this module - every SQLite-backed test in the same process
# that builds DDL from a BigInteger/BIGINT column will emit INTEGER too.
@compiles(BigInteger, "sqlite")
@compiles(GENERIC_BIGINT, "sqlite")
@compiles(MYSQL_BIGINT, "sqlite")
def _bigint_as_integer_sqlite(type_, compiler, **kw):
    return "INTEGER"


STRIPE_CUSTOMER = "cus_state_transitions"
# Every column the transitions could touch except the one they are expected to:
# derived from the model so a new column is covered without editing this list
TRACKED_COLUMNS = [
    c.name for c in UserSubscription.__table__.columns if c.name != "updated_at"
]

_CACHE_PATCH = (
    "studio.app.common.core.subscription.webhook_service.invalidate_user_tier_cache"
)


@pytest.fixture()
def db():
    """In-memory SQLite session with the subscription tables created."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Only the tables these transitions write. The parents they reference
    # (subscription_plans, subscription_providers) use MySQL column types SQLite
    # cannot compile, which is also why foreign keys stay unenforced here.
    tables = [
        UserModel.__table__,
        UserSubscription.__table__,
        SubscriptionUserAccount.__table__,
        SubscriptionUserPurchase.__table__,
    ]
    # MySQL's "ON UPDATE CURRENT_TIMESTAMP" server default is invalid SQLite
    # DDL. These Table objects are process-global, so restore them afterwards.
    stripped = []
    for table in tables:
        for col in table.columns:
            arg = getattr(col.server_default, "arg", None)
            if arg is not None and "ON UPDATE" in str(arg):
                stripped.append((col, col.server_default))
                col.server_default = None
    try:
        SQLModel.metadata.create_all(engine, tables=tables)
        with Session(engine) as session:
            yield session
    finally:
        for col, default in stripped:
            col.server_default = default


def make_user(db, uid, email):
    user = UserModel(
        organization_id=1,
        uid=uid,
        name=uid,
        email=email,
        attributes={},
        active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user.id


@pytest.fixture()
def premium_user(db):
    """An active user with a Stripe account row and a live premium subscription.

    `updated_at` is seeded a minute back rather than years back, so an assertion
    that it moved cannot be satisfied by an unrelated stale value.
    """
    user_id = make_user(db, "uid-premium", "premium@example.com")
    db.add(
        SubscriptionUserAccount(
            user_id=user_id,
            provider_id=1,
            provider_customer_id=STRIPE_CUSTOMER,
        )
    )
    db.add(
        UserSubscription(
            plan_id=SubscriptionPlanIds.PREMIUM,
            user_id=user_id,
            expiration=datetime.now(timezone.utc) + timedelta(days=20),
            sync_status=SyncStatus.SYNCED,
            scheduled_downgrade=False,
            updated_at=datetime.now(timezone.utc).replace(tzinfo=None)
            - timedelta(minutes=1),
        )
    )
    db.commit()
    return user_id


@pytest.fixture()
def unsubscribed_user(db):
    """An active user with no `subscription_users` row at all."""
    return make_user(db, "uid-free", "free@example.com")


def row_for(db, user_id):
    return (
        db.query(UserSubscription).filter(UserSubscription.user_id == user_id).first()
    )


def snapshot(db, user_id):
    row = row_for(db, user_id)
    return {name: getattr(row, name) for name in TRACKED_COLUMNS}


def updated_at_of(db, user_id):
    return row_for(db, user_id).updated_at


def checkout_session(user_id, payment_status="paid"):
    return {
        "id": "cs_test_session",
        "customer": STRIPE_CUSTOMER,
        "payment_status": payment_status,
        "metadata": {
            "user_id": str(user_id),
            "plan_id": str(SubscriptionPlanIds.PREMIUM),
        },
        "subscription": "sub_stripe_123",
    }


class TestDeclinedCheckoutWritesNoSubscription:
    """A declined checkout must not leave the user on a paid plan."""

    def test_an_unpaid_session_is_rejected_before_any_write(
        self, db, unsubscribed_user
    ):
        with patch(_CACHE_PATCH), pytest.raises(HTTPException) as raised:
            WebhookService.handle_checkout_completed(
                db, checkout_session(unsubscribed_user, payment_status="unpaid")
            )

        assert raised.value.status_code == 400
        # The user was never subscribed, and a declined card must not change that
        assert row_for(db, unsubscribed_user) is None

    def test_an_unpaid_session_does_not_upgrade_an_existing_free_row(self, db):
        user_id = make_user(db, "uid-free-row", "free-row@example.com")
        db.add(
            UserSubscription(
                plan_id=SubscriptionPlanIds.FREE,
                user_id=user_id,
                expiration=datetime.now(timezone.utc) + timedelta(days=365),
                sync_status=SyncStatus.SYNCED,
            )
        )
        db.commit()
        before = snapshot(db, user_id)

        with patch(_CACHE_PATCH), pytest.raises(HTTPException) as raised:
            WebhookService.handle_checkout_completed(
                db, checkout_session(user_id, payment_status="unpaid")
            )

        assert raised.value.status_code == 400
        assert snapshot(db, user_id) == before
        assert before["plan_id"] == SubscriptionPlanIds.FREE

    @pytest.mark.asyncio
    async def test_an_async_payment_failure_never_reaches_the_database(self):
        # `checkout.session.async_payment_failed` is not a handled event type, so
        # a declined delayed payment is acknowledged without touching any row.
        # This is the tripwire for that becoming handled without a test.
        mock_db = Mock(spec=Session)
        result = await WebhookService.dispatch_webhook_event(
            mock_db,
            "checkout.session.async_payment_failed",
            {"customer": STRIPE_CUSTOMER, "payment_status": "unpaid"},
        )

        assert result["success"] is True
        assert "Unhandled event type" in result["message"]
        mock_db.query.assert_not_called()
        mock_db.commit.assert_not_called()


class TestSuccessfulCheckoutWritesPremium:
    """A paid checkout writes the premium plan and its expiration, once."""

    def test_a_first_purchase_inserts_the_row(self, db, unsubscribed_user):
        expiration = datetime(2027, 3, 14, 12, 34, 56)
        assert row_for(db, unsubscribed_user) is None

        CheckoutService.create_or_update_subscription(
            db, unsubscribed_user, SubscriptionPlanIds.PREMIUM, expiration
        )
        db.commit()

        row = snapshot(db, unsubscribed_user)
        assert row["plan_id"] == SubscriptionPlanIds.PREMIUM
        assert row["expiration"] == expiration
        assert row["sync_status"] == SyncStatus.SYNCED
        assert row["scheduled_downgrade"] is False

    def test_upgrading_a_free_row_flips_the_plan_and_clears_the_downgrade(self, db):
        # The pre-purchase state has to differ from the expected one in every
        # field asserted below, or the assertions just read the seed back
        user_id = make_user(db, "uid-upgrading", "upgrading@example.com")
        db.add(
            UserSubscription(
                plan_id=SubscriptionPlanIds.FREE,
                user_id=user_id,
                expiration=datetime(2020, 1, 1),
                sync_status=SyncStatus.PENDING,
                scheduled_downgrade=True,
            )
        )
        db.commit()
        expiration = datetime(2027, 3, 14, 12, 34, 56)

        CheckoutService.create_or_update_subscription(
            db, user_id, SubscriptionPlanIds.PREMIUM, expiration
        )
        db.commit()

        row = snapshot(db, user_id)
        assert row["plan_id"] == SubscriptionPlanIds.PREMIUM
        assert row["expiration"] == expiration
        assert row["sync_status"] == SyncStatus.SYNCED
        # A fresh purchase clears the downgrade the previous period scheduled
        assert row["scheduled_downgrade"] is False

    def test_a_second_checkout_updates_the_same_row(self, db, premium_user):
        first = datetime(2027, 3, 14, 12, 0, 0)
        second = datetime(2027, 4, 14, 12, 0, 0)

        CheckoutService.create_or_update_subscription(
            db, premium_user, SubscriptionPlanIds.PREMIUM, first
        )
        db.commit()
        row_id = snapshot(db, premium_user)["id"]

        CheckoutService.create_or_update_subscription(
            db, premium_user, SubscriptionPlanIds.PREMIUM, second
        )
        db.commit()

        after = snapshot(db, premium_user)
        # idx_user_id_unique means the renewal has to land on the same row
        assert after["id"] == row_id
        assert after["expiration"] == second
        assert (
            db.query(UserSubscription)
            .filter(UserSubscription.user_id == premium_user)
            .count()
            == 1
        )

    def test_expiration_comes_from_stripe_not_from_a_local_month(self):
        # The sheet's note is explicit that the expiration is Stripe's
        # current_period_end, not now + 1 month. A local calculation would drift
        # from what the customer was actually billed for.
        period_end = 1795000000  # far-future unix timestamp
        session_data = checkout_session(42)
        mock_db = Mock(spec=Session)
        mock_db.query.return_value.join.return_value.filter.return_value.first.return_value = (  # noqa: E501
            None
        )

        with patch.object(
            CheckoutService, "get_subscription_plan", return_value=Mock(id=2)
        ), patch.object(
            CheckoutService, "get_or_create_stripe_provider", return_value=1
        ), patch.object(
            CheckoutService, "create_or_update_user_account"
        ), patch.object(
            CheckoutService, "set_default_payment_method"
        ), patch.object(
            CheckoutService, "create_or_update_subscription", return_value=1
        ) as mock_upsert, patch.object(
            CheckoutService, "record_purchase", return_value=Mock(id=1)
        ), patch(
            "studio.app.common.core.subscription.webhook_service."
            "stripe.Subscription.retrieve",
            return_value={
                "current_period_end": period_end,
                "trial_end": None,
                "current_period_start": 1792000000,
            },
        ), patch(
            _CACHE_PATCH
        ):
            WebhookService.handle_checkout_completed(mock_db, session_data)

        _, user_id, plan_id, expiration = mock_upsert.call_args[0]
        assert user_id == 42  # the string metadata is converted, not passed through
        assert plan_id == SubscriptionPlanIds.PREMIUM
        assert int(expiration.timestamp()) == period_end


class TestCancelTouchesOnlyTheDowngradeFlag:
    """Field integrity after a scheduled cancellation."""

    def test_only_scheduled_downgrade_and_updated_at_change(self, db, premium_user):
        before = snapshot(db, premium_user)
        before_updated_at = updated_at_of(db, premium_user)

        SubscriptionService.update_scheduled_downgrade(db, premium_user, True)

        after = snapshot(db, premium_user)
        assert after["scheduled_downgrade"] is True
        # plan_id, expiration and sync_status are what a cancelled-but-not-yet-
        # expired user keeps their paid access from
        assert {k: v for k, v in after.items() if k != "scheduled_downgrade"} == {
            k: v for k, v in before.items() if k != "scheduled_downgrade"
        }
        after_updated_at = updated_at_of(db, premium_user)
        assert after_updated_at > before_updated_at
        assert after["created_at"] <= after_updated_at

    def test_the_stripe_customer_id_is_untouched(self, db, premium_user):
        account = (
            db.query(SubscriptionUserAccount)
            .filter(SubscriptionUserAccount.user_id == premium_user)
            .first()
        )
        before = (account.id, account.provider_id, account.provider_customer_id)

        SubscriptionService.update_scheduled_downgrade(db, premium_user, True)

        account = (
            db.query(SubscriptionUserAccount)
            .filter(SubscriptionUserAccount.user_id == premium_user)
            .first()
        )
        assert (
            account.id,
            account.provider_id,
            account.provider_customer_id,
        ) == before

    def test_reactivation_clears_the_flag_and_nothing_else(self, db, premium_user):
        SubscriptionService.update_scheduled_downgrade(db, premium_user, True)
        cancelled = snapshot(db, premium_user)

        SubscriptionService.update_scheduled_downgrade(db, premium_user, False)

        reactivated = snapshot(db, premium_user)
        assert reactivated["scheduled_downgrade"] is False
        assert {k: v for k, v in reactivated.items() if k != "scheduled_downgrade"} == {
            k: v for k, v in cancelled.items() if k != "scheduled_downgrade"
        }


class TestFallbackToFreeIsDerivedNotWritten:
    """Row 291: nothing downgrades the row; the tier comes from the expiration."""

    def test_a_failed_renewal_leaves_the_row_on_premium(self, db, premium_user):
        with patch(_CACHE_PATCH):
            WebhookService.handle_payment_failed(db, {"customer": STRIPE_CUSTOMER})

        row = snapshot(db, premium_user)
        # sync_status is the witness that the handler ran at all: everything else
        # asserted here is also the fixture's seeded state
        assert row["sync_status"] == SyncStatus.FAILED
        # plan_id stays PREMIUM and no Free row is written. A user who stops
        # paying is recognised by their expiration, not by a downgrade.
        assert row["plan_id"] == SubscriptionPlanIds.PREMIUM
        assert (
            db.query(UserSubscription)
            .filter(UserSubscription.user_id == premium_user)
            .count()
            == 1
        )

    def test_a_failed_renewal_changes_nothing_else(self, db, premium_user):
        before = snapshot(db, premium_user)
        before_updated_at = updated_at_of(db, premium_user)

        with patch(_CACHE_PATCH):
            WebhookService.handle_payment_failed(db, {"customer": STRIPE_CUSTOMER})

        after = snapshot(db, premium_user)
        assert {k: v for k, v in after.items() if k != "sync_status"} == {
            k: v for k, v in before.items() if k != "sync_status"
        }
        assert updated_at_of(db, premium_user) > before_updated_at

    def test_a_failed_renewal_for_an_unknown_customer_writes_nothing(
        self, db, premium_user
    ):
        before = snapshot(db, premium_user)

        with patch(_CACHE_PATCH):
            WebhookService.handle_payment_failed(db, {"customer": "cus_not_ours"})

        assert snapshot(db, premium_user) == before

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "days_past_expiry,expected",
        [
            (1, SubscriptionStatus.LIMIT_GRACE.value),
            (
                SubscriptionPeriods.GRACE_PERIOD_DAYS + 1,
                SubscriptionStatus.EXPIRED.value,
            ),
        ],
    )
    async def test_status_is_derived_from_the_expiration(
        self, days_past_expiry, expected
    ):
        # Same derivation `get_user_with_context` runs, with `now` pinned so the
        # boundary does not depend on when the suite runs
        now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
        organization = SimpleNamespace(id=1, name="Test Organization")
        user = Mock()
        # `User.from_orm` reads __dict__, so it has to be a real dict of fields
        user.__dict__ = {
            "id": 7,
            "uid": "uid-premium",
            "email": "premium@example.com",
            "name": "Premium User",
            "active": True,
            "organization_id": 1,
            "organization": organization,
        }

        mock_result = Mock()
        mock_result.first.return_value = (
            user,
            1,
            0,
            "Premium",
            0,
            5_000_000_000,
            now - timedelta(days=days_past_expiry),
            SubscriptionPlanIds.PREMIUM,
        )
        mock_db = Mock()
        mock_db.execute = Mock(return_value=mock_result)

        with patch.object(crud_users, "get_current_datetime", return_value=now):
            result = await crud_users.get_user_with_context(mock_db, 7)

        assert result.subscription_status == expected


class TestReactivateRejectsAnotherUsersSubscription:
    """The reactivate route is the one server-side write the UI cannot be trusted
    for: without its ownership check any signed-in user could un-cancel someone
    else's subscription."""

    @pytest.mark.asyncio
    async def test_a_mismatched_user_id_is_refused(self, db, premium_user):
        before = snapshot(db, premium_user)
        SubscriptionService.update_scheduled_downgrade(db, premium_user, True)
        attacker = SimpleNamespace(id=premium_user + 1)

        with pytest.raises(HTTPException) as raised:
            await reactivate_user_subscription(
                user_id=premium_user, db=db, current_user=attacker
            )

        assert raised.value.status_code == 403
        # Still cancelled: the refusal happened before the flag was cleared
        assert snapshot(db, premium_user)["scheduled_downgrade"] is True
        assert before["plan_id"] == snapshot(db, premium_user)["plan_id"]
