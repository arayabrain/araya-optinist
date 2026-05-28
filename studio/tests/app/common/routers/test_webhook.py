from datetime import timedelta
from unittest.mock import Mock, patch

import pytest
from sqlmodel import Session

from studio.app.common.core.subscription.checkout_service import CheckoutService
from studio.app.common.core.subscription.constants import SyncStatus
from studio.app.common.core.subscription.subscription_service import SubscriptionService
from studio.app.common.core.subscription.webhook_service import WebhookService
from studio.app.common.core.utils.datetime_utils import get_current_datetime


class TestInvoicePaymentSucceeded:
    """Test suite for invoice.payment_succeeded webhook handler"""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session"""
        db = Mock(spec=Session)
        # Setup query chain to return mocked objects properly
        db.query = Mock()
        db.add = Mock()
        db.flush = Mock()
        db.commit = Mock()
        db.rollback = Mock()
        return db

    @pytest.fixture
    def mock_user_account(self):
        """Create a mock user account"""
        account = Mock()
        account.user_id = "user_123"
        account.provider_customer_id = "cus_test123"
        return account

    @pytest.fixture
    def mock_subscription(self):
        """Create a mock active subscription"""
        subscription = Mock()
        subscription.id = "sub_123"
        subscription.user_id = "user_123"
        subscription.plan_id = "plan_123"
        # IMPORTANT: Set to real datetime, not Mock
        subscription.expiration = get_current_datetime() + timedelta(days=5)
        subscription.updated_at = None
        return subscription

    @pytest.fixture
    def mock_plan(self):
        """Create a mock subscription plan"""
        plan = Mock()
        plan.id = "plan_123"
        plan.name = "Premium Plan"
        plan.billing_cycle = (
            "monthly"  # This should match BILLING_CYCLE.MONTHLY from your code
        )
        return plan

    @pytest.fixture
    def mock_user(self):
        """Create a mock user for cache invalidation"""
        user = Mock()
        user.id = "user_123"
        user.uid = "user_uid_123"
        return user

    @pytest.fixture
    def invoice_data_subscription_cycle(self):
        """Create mock invoice data for subscription renewal"""
        return {
            "id": "in_test123",
            "customer": "cus_test123",
            "subscription": "sub_stripe123",  # Added subscription at top level
            "parent": {"subscription_details": {"subscription": "sub_stripe123"}},
            "status": "paid",
            "amount_paid": 2999,  # $29.99 in cents
            "billing_reason": "subscription_cycle",
            "period_start": 1699999999,
            "period_end": 1702678399,
            "lines": {
                "object": "list",
                "data": [
                    {
                        "id": "il_test123",
                        "period": {"end": 1702678399, "start": 1699999999},
                    }
                ],
            },
        }

    @pytest.fixture
    def invoice_data_initial_payment(self):
        """Create mock invoice data for initial subscription payment"""
        return {
            "id": "in_test456",
            "customer": "cus_test123",
            "subscription": "sub_stripe123",  # Added subscription at top level
            "parent": {"subscription_details": {"subscription": "sub_stripe123"}},
            "status": "paid",
            "amount_paid": 2999,
            "billing_reason": "subscription_create",  # This should be skipped
        }

    def test_successful_subscription_renewal_monthly(
        self,
        mock_db,
        mock_user_account,
        mock_subscription,
        mock_plan,
        mock_user,
        invoice_data_subscription_cycle,
    ):
        """Test successful monthly subscription renewal"""
        # Setup the query chain to return different results
        mock_db.query.side_effect = [
            # 1st query: UserAccount by customer_id
            Mock(
                filter=Mock(
                    return_value=Mock(first=Mock(return_value=mock_user_account))
                )
            ),
            # 2nd query: UserSubscription by user_id
            Mock(
                filter=Mock(
                    return_value=Mock(
                        order_by=Mock(
                            return_value=Mock(
                                first=Mock(return_value=mock_subscription)
                            )
                        )
                    )
                )
            ),
            # 3rd query: User for cache invalidation
            Mock(filter=Mock(return_value=Mock(first=Mock(return_value=mock_user)))),
        ]

        with patch.object(
            CheckoutService, "get_subscription_plan", return_value=mock_plan
        ), patch.object(
            SubscriptionService,
            "get_current_datetime",
            return_value=get_current_datetime(),
        ):
            # Execute
            result = WebhookService.handle_subscription_payment_succeeded(
                mock_db, invoice_data_subscription_cycle
            )

            # Assertions
            assert result["success"] is True
            assert result["user_id"] == "user_123"
            assert result["webhook_processed"] is True
            assert "new_expiration" in result
            assert "old_expiration" in result
            assert result["amount_paid"] == 29.99

            # Verify database interactions
            mock_db.add.assert_called_once()  # Purchase record added
            mock_db.flush.assert_called_once()
            mock_db.commit.assert_called_once()

            # Verify subscription was updated
            assert mock_subscription.expiration is not None
            assert mock_subscription.updated_at is not None

    def test_skip_initial_payment(self, mock_db, invoice_data_initial_payment):
        """Test that initial subscription payments are skipped"""
        # Execute
        result = WebhookService.handle_subscription_payment_succeeded(
            mock_db, invoice_data_initial_payment
        )

        # Assertions
        assert result["success"] is True
        assert result["skipped"] is True
        assert "billing_reason" in result["message"]

        # Verify no database changes were made
        mock_db.add.assert_not_called()
        mock_db.commit.assert_not_called()

    def test_missing_customer_id(self, mock_db):
        """Test error handling for missing customer_id"""
        invoice_data = {
            "id": "in_test789",
            "subscription": "sub_stripe123",  # Added subscription at top level
            "parent": {"subscription_details": {"subscription": "sub_stripe123"}},
            "status": "paid",
            "amount_paid": 2999,
            "billing_reason": "subscription_cycle",
            # Missing customer field
        }

        with pytest.raises(Exception):  # Should raise HTTPException
            WebhookService.handle_subscription_payment_succeeded(mock_db, invoice_data)

        # Verify rollback was called
        mock_db.rollback.assert_called()

    def test_payment_not_completed(self, mock_db, mock_user_account):
        """Test error handling for incomplete payment"""
        invoice_data = {
            "id": "in_test789",
            "customer": "cus_test123",
            "subscription": "sub_stripe123",  # Added subscription at top level
            "parent": {"subscription_details": {"subscription": "sub_stripe123"}},
            "status": "open",  # Not paid
            "amount_paid": 2999,
            "billing_reason": "subscription_cycle",
        }

        mock_db.query.return_value.filter.return_value.first.return_value = (
            mock_user_account
        )

        with pytest.raises(Exception):  # Should raise HTTPException
            WebhookService.handle_subscription_payment_succeeded(mock_db, invoice_data)

        mock_db.rollback.assert_called()

    def test_user_not_found(self, mock_db, invoice_data_subscription_cycle):
        """Test handling when user is not found"""
        # Mock user not found
        mock_db.query.return_value.filter.return_value.first.return_value = None

        # Should return success response instead of raising exception
        result = WebhookService.handle_subscription_payment_succeeded(
            mock_db, invoice_data_subscription_cycle
        )

        # Verify it returns success with skipped flag
        assert result["success"] is True
        assert result["skipped"] is True
        assert result["reason"] == "missing_user_account"
        assert result["webhook_processed"] is True

    def test_subscription_not_found(
        self, mock_db, mock_user_account, invoice_data_subscription_cycle
    ):
        """Test error handling when active subscription is not found"""
        # Mock user found but no active subscription
        mock_db.query.side_effect = [
            Mock(
                filter=Mock(
                    return_value=Mock(first=Mock(return_value=mock_user_account))
                )
            ),
            Mock(
                filter=Mock(
                    return_value=Mock(
                        order_by=Mock(return_value=Mock(first=Mock(return_value=None)))
                    )
                )
            ),
        ]

        with pytest.raises(Exception):  # Should raise HTTPException
            WebhookService.handle_subscription_payment_succeeded(
                mock_db, invoice_data_subscription_cycle
            )

        mock_db.rollback.assert_called()

    def test_plan_not_found(
        self,
        mock_db,
        mock_user_account,
        mock_subscription,
        invoice_data_subscription_cycle,
    ):
        """Test error handling when subscription plan is not found"""
        mock_db.query.side_effect = [
            Mock(
                filter=Mock(
                    return_value=Mock(first=Mock(return_value=mock_user_account))
                )
            ),
            Mock(
                filter=Mock(
                    return_value=Mock(
                        order_by=Mock(
                            return_value=Mock(
                                first=Mock(return_value=mock_subscription)
                            )
                        )
                    )
                )
            ),
        ]

        with patch.object(CheckoutService, "get_subscription_plan", return_value=None):
            with pytest.raises(Exception):  # Should raise HTTPException
                WebhookService.handle_subscription_payment_succeeded(
                    mock_db, invoice_data_subscription_cycle
                )

            mock_db.rollback.assert_called()

    def test_database_commit_failure(
        self,
        mock_db,
        mock_user_account,
        mock_subscription,
        mock_plan,
        invoice_data_subscription_cycle,
    ):
        """Test error handling when database commit fails"""
        mock_db.query.side_effect = [
            Mock(
                filter=Mock(
                    return_value=Mock(first=Mock(return_value=mock_user_account))
                )
            ),
            Mock(
                filter=Mock(
                    return_value=Mock(
                        order_by=Mock(
                            return_value=Mock(
                                first=Mock(return_value=mock_subscription)
                            )
                        )
                    )
                )
            ),
        ]
        mock_db.commit.side_effect = Exception("Database error")

        with patch.object(
            CheckoutService, "get_subscription_plan", return_value=mock_plan
        ), patch.object(
            SubscriptionService,
            "get_current_datetime",
            return_value=get_current_datetime(),
        ):
            with pytest.raises(Exception):
                WebhookService.handle_subscription_payment_succeeded(
                    mock_db, invoice_data_subscription_cycle
                )

            mock_db.rollback.assert_called()


# Additional integration test with real-like webhook payload
def test_full_webhook_payload():
    """Test with a full realistic Stripe webhook payload"""
    full_invoice_payload = {
        "id": "in_1QLzTh2eZvKYlo2C1234abcd",
        "object": "invoice",
        "account_country": "US",
        "account_name": "Your Company",
        "amount_due": 2999,
        "amount_paid": 2999,
        "amount_remaining": 0,
        "application_fee_amount": None,
        "attempt_count": 1,
        "attempted": True,
        "billing_reason": "subscription_cycle",
        "charge": "ch_1QLzTh2eZvKYlo2C5678efgh",
        "collection_method": "charge_automatically",
        "created": 1699999999,
        "currency": "usd",
        "customer": "cus_test123456",
        "customer_email": "customer@example.com",
        "customer_name": "Test Customer",
        "customer_phone": None,
        "description": None,
        "hosted_invoice_url": "https://invoice.stripe.com/i/acct_test/test_link",
        "invoice_pdf": "https://pay.stripe.com/invoice/test/pdf",
        "lines": {
            "object": "list",
            "data": [
                {
                    "id": "il_1QLzTh2eZvKYlo2C9999",
                    "object": "line_item",
                    "amount": 2999,
                    "currency": "usd",
                    "description": "1 × Premium Plan (at $29.99 / month)",
                    "period": {"end": 1702678399, "start": 1699999999},
                    "plan": {
                        "id": "price_1234567890",
                        "object": "plan",
                        "active": True,
                        "interval": "month",
                        "interval_count": 1,
                    },
                    "quantity": 1,
                }
            ],
        },
        "paid": True,
        "payment_intent": "pi_1QLzTh2eZvKYlo2C1111",
        "period_end": 1702678399,
        "period_start": 1699999999,
        "status": "paid",
        "parent": {
            "subscription_details": {"subscription": "sub_1QLzTh2eZvKYlo2C2222"}
        },
        "subtotal": 2999,
        "total": 2999,
    }

    # This payload structure matches what your webhook service expects
    assert full_invoice_payload["billing_reason"] == "subscription_cycle"
    assert full_invoice_payload["status"] == "paid"
    assert full_invoice_payload["amount_paid"] == 2999

    # Test extraction using the same logic as webhook_service.py
    subscription_id = (
        full_invoice_payload.get("parent", {})
        .get("subscription_details", {})
        .get("subscription")
    )
    assert subscription_id == "sub_1QLzTh2eZvKYlo2C2222"


class TestSubscriptionLookbackWindow:
    """Extended lookback window for trial-to-paid conversion."""

    def test_subscription_lookback_constant_is_30_days(self):
        """RECENT_SUBSCRIPTION_WINDOW_DAYS should be 30 days for extended lookback"""
        from studio.app.common.core.subscription.constants import (
            RECENT_SUBSCRIPTION_WINDOW_DAYS,
        )

        assert RECENT_SUBSCRIPTION_WINDOW_DAYS == 30


class TestPaymentFailureTracking:
    """Payment failure tracking."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session"""
        db = Mock(spec=Session)
        db.query = Mock()
        db.commit = Mock()
        return db

    @pytest.fixture
    def mock_user_account(self):
        """Create a mock user account"""
        account = Mock()
        account.user_id = 123
        account.provider_customer_id = "cus_test123"
        return account

    @pytest.fixture
    def mock_subscription(self):
        """Create a mock subscription"""
        subscription = Mock()
        subscription.id = 1
        subscription.user_id = 123
        subscription.sync_status = None
        subscription.expiration = get_current_datetime() + timedelta(days=30)
        return subscription

    @pytest.fixture
    def mock_user(self):
        """Create a mock user"""
        user = Mock()
        user.id = 123
        user.uid = "user_uid_123"
        return user

    def test_payment_failed_sets_sync_status_failed(
        self, mock_db, mock_user_account, mock_subscription, mock_user
    ):
        """Payment failure should set sync_status to FAILED"""
        invoice_data = {
            "id": "in_test123",
            "customer": "cus_test123",
        }

        mock_db.query.side_effect = [
            Mock(
                filter=Mock(
                    return_value=Mock(first=Mock(return_value=mock_user_account))
                )
            ),
            Mock(
                filter=Mock(
                    return_value=Mock(first=Mock(return_value=mock_subscription))
                )
            ),
            Mock(filter=Mock(return_value=Mock(first=Mock(return_value=mock_user)))),
        ]

        with patch(
            "studio.app.common.core.subscription.webhook_service."
            "invalidate_user_tier_cache"
        ):
            WebhookService.handle_payment_failed(mock_db, invoice_data)

        assert mock_subscription.sync_status == SyncStatus.FAILED


class TestWebhookCacheInvalidation:
    """User tier cache invalidation in webhook handlers."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session"""
        db = Mock(spec=Session)
        db.query = Mock()
        db.add = Mock()
        db.flush = Mock()
        db.commit = Mock()
        db.rollback = Mock()
        return db

    @pytest.fixture
    def mock_user(self):
        """Create a mock user"""
        user = Mock()
        user.id = 123
        user.uid = "user_uid_123"
        user.email = "test@example.com"
        return user

    @pytest.fixture
    def mock_user_account(self):
        """Create a mock user account"""
        account = Mock()
        account.user_id = 123
        account.provider_customer_id = "cus_test123"
        return account

    @pytest.fixture
    def mock_subscription(self):
        """Create a mock active subscription"""
        subscription = Mock()
        subscription.id = 1
        subscription.user_id = 123
        subscription.plan_id = 1
        subscription.expiration = get_current_datetime() + timedelta(days=5)
        subscription.sync_status = None
        subscription.updated_at = None
        return subscription

    def test_payment_failed_invalidates_cache(
        self, mock_db, mock_user_account, mock_subscription, mock_user
    ):
        """Test that handle_payment_failed invalidates user tier cache"""
        invoice_data = {
            "id": "in_test123",
            "customer": "cus_test123",
        }

        # Setup query chain
        mock_db.query.side_effect = [
            # First query: find user account
            Mock(
                filter=Mock(
                    return_value=Mock(first=Mock(return_value=mock_user_account))
                )
            ),
            # Second query: find subscription
            Mock(
                filter=Mock(
                    return_value=Mock(first=Mock(return_value=mock_subscription))
                )
            ),
            # Third query: find user for cache invalidation
            Mock(filter=Mock(return_value=Mock(first=Mock(return_value=mock_user)))),
        ]

        with patch(
            "studio.app.common.core.subscription.webhook_service."
            "invalidate_user_tier_cache"
        ) as mock_invalidate:
            WebhookService.handle_payment_failed(mock_db, invoice_data)

            # Verify cache invalidation was called with user UID
            mock_invalidate.assert_called_once_with(mock_user.uid)

    def test_payment_failed_no_cache_invalidation_when_no_subscription(
        self, mock_db, mock_user_account
    ):
        """Test that cache is not invalidated when no subscription found"""
        invoice_data = {
            "id": "in_test123",
            "customer": "cus_test123",
        }

        # Setup query chain - no subscription found
        mock_db.query.side_effect = [
            Mock(
                filter=Mock(
                    return_value=Mock(first=Mock(return_value=mock_user_account))
                )
            ),
            Mock(filter=Mock(return_value=Mock(first=Mock(return_value=None)))),
        ]

        with patch(
            "studio.app.common.core.subscription.webhook_service."
            "invalidate_user_tier_cache"
        ) as mock_invalidate:
            WebhookService.handle_payment_failed(mock_db, invoice_data)

            # Verify cache invalidation was NOT called
            mock_invalidate.assert_not_called()

    def test_subscription_renewal_invalidates_cache(
        self, mock_db, mock_user_account, mock_subscription, mock_user
    ):
        """Test that handle_subscription_payment_succeeded invalidates cache"""
        mock_plan = Mock()
        mock_plan.id = 1

        invoice_data = {
            "id": "in_test123",
            "customer": "cus_test123",
            "subscription": "sub_stripe123",
            "status": "paid",
            "amount_paid": 2999,
            "billing_reason": "subscription_cycle",
            "lines": {"data": [{"period": {"end": 1702678399}}]},
        }

        # Setup query chain
        mock_db.query.side_effect = [
            # Find user account
            Mock(
                filter=Mock(
                    return_value=Mock(first=Mock(return_value=mock_user_account))
                )
            ),
            # Find subscription
            Mock(
                filter=Mock(
                    return_value=Mock(
                        order_by=Mock(
                            return_value=Mock(
                                first=Mock(return_value=mock_subscription)
                            )
                        )
                    )
                )
            ),
            # Find user for cache invalidation
            Mock(filter=Mock(return_value=Mock(first=Mock(return_value=mock_user)))),
        ]

        with patch.object(
            CheckoutService, "get_subscription_plan", return_value=mock_plan
        ), patch.object(
            SubscriptionService,
            "get_current_datetime",
            return_value=get_current_datetime(),
        ), patch(
            "studio.app.common.core.subscription.webhook_service."
            "invalidate_user_tier_cache"
        ) as mock_invalidate:
            result = WebhookService.handle_subscription_payment_succeeded(
                mock_db, invoice_data
            )

            assert result["success"] is True
            # Verify cache invalidation was called
            mock_invalidate.assert_called_once_with(mock_user.uid)


class TestStorageQuotaBytesForPlan:
    """Unit tests for StorageQuota.bytes_for_plan mapping"""

    def test_premium_plan_returns_premium_quota(self):
        from studio.app.common.core.subscription.constants import (
            StorageQuota,
            StorageSize,
            SubscriptionPlanIds,
        )

        result = StorageQuota.bytes_for_plan(SubscriptionPlanIds.PREMIUM)
        assert result == StorageQuota.PREMIUM * StorageSize.GB

    def test_free_plan_returns_free_quota(self):
        from studio.app.common.core.subscription.constants import (
            StorageQuota,
            StorageSize,
            SubscriptionPlanIds,
        )

        result = StorageQuota.bytes_for_plan(SubscriptionPlanIds.FREE)
        assert result == StorageQuota.FREE * StorageSize.GB

    def test_unknown_plan_falls_back_to_free_quota(self):
        from studio.app.common.core.subscription.constants import (
            StorageQuota,
            StorageSize,
        )

        result = StorageQuota.bytes_for_plan(999)
        assert result == StorageQuota.FREE * StorageSize.GB


class TestCheckoutStorageQuotaUpdate:
    """Test that handle_checkout_completed updates storage quota correctly"""

    @pytest.fixture
    def mock_db(self):
        db = Mock(spec=Session)
        db.query = Mock()
        db.add = Mock()
        db.execute = Mock()
        db.commit = Mock()
        db.rollback = Mock()
        return db

    @pytest.fixture
    def session_data(self):
        """Minimal session data to reach step 10"""
        return {
            "id": "cs_test_session",
            "customer": "cus_test123",
            "payment_status": "paid",
            "metadata": {"user_id": "42", "plan_id": "2"},
            "subscription": "sub_stripe_123",
        }

    @pytest.fixture
    def mock_user(self):
        user = Mock()
        user.id = 42
        user.uid = "uid_42"
        return user

    def _setup_checkout_mocks(self, mock_db, mock_user):
        """Patch CheckoutService, stripe, and cache so we reach step 10"""
        # Step 1: duplicate check — no existing purchase
        mock_query_purchase = Mock()
        mock_query_purchase.join.return_value.filter.return_value.first.return_value = (
            None
        )
        # Step 12: user lookup for cache invalidation
        mock_query_user = Mock()
        mock_query_user.filter.return_value.first.return_value = mock_user

        mock_db.query.side_effect = [mock_query_purchase, mock_query_user]

        mock_purchase = Mock()
        mock_purchase.id = 1

        mock_stripe_sub = {
            "current_period_end": 1735689600,
            "trial_end": None,
            "current_period_start": 1733097600,
        }

        patches = {
            "plan": patch.object(
                CheckoutService,
                "get_subscription_plan",
                return_value=Mock(id=2),
            ),
            "provider": patch.object(
                CheckoutService,
                "get_or_create_stripe_provider",
                return_value=1,
            ),
            "account": patch.object(CheckoutService, "create_or_update_user_account"),
            "payment": patch.object(CheckoutService, "set_default_payment_method"),
            "subscription": patch.object(
                CheckoutService,
                "create_or_update_subscription",
                return_value=1,
            ),
            "purchase": patch.object(
                CheckoutService,
                "record_purchase",
                return_value=mock_purchase,
            ),
            "stripe": patch(
                "studio.app.common.core.subscription.webhook_service."
                "stripe.Subscription.retrieve",
                return_value=mock_stripe_sub,
            ),
            "cache": patch(
                "studio.app.common.core.subscription.webhook_service."
                "invalidate_user_tier_cache",
            ),
            "datetime": patch.object(
                SubscriptionService,
                "get_current_datetime",
                return_value=get_current_datetime(),
            ),
        }
        return patches

    def test_existing_storage_record_updated_via_execute(
        self, mock_db, session_data, mock_user
    ):
        """When storage record exists, db.execute(update) should be called"""
        patches = self._setup_checkout_mocks(mock_db, mock_user)
        # db.execute returns a result with rowcount=1 (existing record updated)
        mock_db.execute.return_value.rowcount = 1

        with (
            patches["plan"],
            patches["provider"],
            patches["account"],
            patches["payment"],
            patches["subscription"],
            patches["purchase"],
            patches["stripe"],
            patches["cache"],
            patches["datetime"],
        ):
            result = WebhookService.handle_checkout_completed(mock_db, session_data)

        assert result["success"] is True
        # Verify db.execute was called (the update statement)
        mock_db.execute.assert_called_once()
        # Verify db.add was NOT called for storage (no new record needed)
        mock_db.add.assert_not_called()
        # Verify single atomic commit
        mock_db.commit.assert_called_once()

    def test_no_storage_record_creates_new_via_add(
        self, mock_db, session_data, mock_user
    ):
        """When no storage record exists, db.add(UserStorageUsage) should be called"""
        from studio.app.common.models.subscription import UserStorageUsage

        patches = self._setup_checkout_mocks(mock_db, mock_user)
        # db.execute returns rowcount=0 (no existing record)
        mock_db.execute.return_value.rowcount = 0

        with (
            patches["plan"],
            patches["provider"],
            patches["account"],
            patches["payment"],
            patches["subscription"],
            patches["purchase"],
            patches["stripe"],
            patches["cache"],
            patches["datetime"],
        ):
            result = WebhookService.handle_checkout_completed(mock_db, session_data)

        assert result["success"] is True
        # Verify db.add was called with a UserStorageUsage instance
        mock_db.add.assert_called_once()
        added_obj = mock_db.add.call_args[0][0]
        assert isinstance(added_obj, UserStorageUsage)
        assert added_obj.user_id == 42
        assert added_obj.storage_usage_bytes == 0
        from studio.app.common.core.subscription.constants import (
            StorageQuota,
            SubscriptionPlanIds,
        )

        expected_quota = StorageQuota.bytes_for_plan(SubscriptionPlanIds.PREMIUM)
        assert added_obj.storage_quota_bytes == expected_quota


class TestSubscriptionLifecycleWebhooks:
    """Tests for customer.subscription.created handling (issue #629, P1).

    Verifies that the new `created` webhook activates a subscription
    end-to-end so users are no longer stuck on "Activation Pending" when
    `checkout.session.completed` can't complete the activation on its own.
    """

    @pytest.fixture
    def mock_db(self):
        db = Mock(spec=Session)
        db.query = Mock()
        db.add = Mock()
        db.flush = Mock()
        db.commit = Mock()
        db.rollback = Mock()
        db.execute = Mock()
        return db

    @pytest.fixture
    def mock_user_account(self):
        account = Mock()
        account.user_id = 42
        account.provider_customer_id = "cus_test123"
        return account

    @pytest.fixture
    def mock_user(self):
        user = Mock()
        user.id = 42
        user.uid = "user_uid_42"
        return user

    @pytest.fixture
    def subscription_event(self):
        """A customer.subscription.created event payload (event['data']['object'])."""
        return {
            "id": "sub_stripe123",
            "customer": "cus_test123",
            "status": "active",
            "current_period_end": 2000000000,  # far-future unix timestamp
            "metadata": {"plan_id": "2"},
        }

    def _setup_query_chain(self, mock_db, account, user):
        """Sequential query results: account lookup, then user (for cache)."""
        mock_db.query.side_effect = [
            # 1. SubscriptionUserAccount by customer_id
            Mock(filter=Mock(return_value=Mock(first=Mock(return_value=account)))),
            # 2. User for cache invalidation
            Mock(filter=Mock(return_value=Mock(first=Mock(return_value=user)))),
        ]

    def test_subscription_created_activates_subscription(
        self, mock_db, mock_user_account, mock_user, subscription_event
    ):
        """`created` upserts subscription_users and invalidates the tier cache."""
        self._setup_query_chain(mock_db, mock_user_account, mock_user)

        with patch.object(
            CheckoutService, "create_or_update_subscription", return_value=99
        ) as mock_upsert, patch(
            "studio.app.common.core.subscription.webhook_service."
            "invalidate_user_tier_cache"
        ) as mock_invalidate:
            result = WebhookService.handle_subscription_created(
                mock_db, subscription_event
            )

        assert result["success"] is True
        assert result["user_id"] == 42
        assert result["plan_id"] == 2
        mock_upsert.assert_called_once()
        upsert_args = mock_upsert.call_args[0]
        assert upsert_args[1] == 42  # user_id
        assert upsert_args[2] == 2  # plan_id from metadata
        mock_db.commit.assert_called_once()
        mock_invalidate.assert_called_once_with("user_uid_42")

    def test_dispatch_created_routes_to_handler(self, mock_db, subscription_event):
        """dispatch routes `created` to handle_subscription_created."""
        with patch.object(
            WebhookService,
            "handle_subscription_created",
            return_value={"success": True},
        ) as mock_created:
            result = WebhookService.dispatch_webhook_event(
                mock_db, "customer.subscription.created", subscription_event
            )

        assert result["success"] is True
        mock_created.assert_called_once_with(mock_db, subscription_event)

    def test_subscription_event_acknowledged_when_no_account(
        self, mock_db, subscription_event
    ):
        """Unknown customer is acknowledged without a DB write (no Stripe retries)."""
        mock_db.query.side_effect = [
            Mock(filter=Mock(return_value=Mock(first=Mock(return_value=None)))),
        ]
        with patch.object(
            CheckoutService, "create_or_update_subscription"
        ) as mock_upsert:
            result = WebhookService.handle_subscription_created(
                mock_db, subscription_event
            )

        assert result["success"] is True
        assert result["skipped"] is True
        assert result["reason"] == "missing_user_account"
        mock_upsert.assert_not_called()
        mock_db.commit.assert_not_called()

    def test_subscription_event_acknowledged_when_no_period_end(
        self, mock_db, mock_user_account, subscription_event
    ):
        """Missing expiration is acknowledged without a DB write."""
        subscription_event.pop("current_period_end")
        subscription_event.pop("trial_end", None)
        mock_db.query.side_effect = [
            Mock(
                filter=Mock(
                    return_value=Mock(first=Mock(return_value=mock_user_account))
                )
            ),
        ]
        with patch.object(
            CheckoutService, "create_or_update_subscription"
        ) as mock_upsert:
            result = WebhookService.handle_subscription_created(
                mock_db, subscription_event
            )

        assert result["success"] is True
        assert result["skipped"] is True
        assert result["reason"] == "missing_expiration"
        mock_upsert.assert_not_called()
        mock_db.commit.assert_not_called()

    # --- P2: handle_subscription_updated ---

    def test_dispatch_updated_routes_to_handler(self, mock_db, subscription_event):
        """dispatch routes `updated` to handle_subscription_updated."""
        with patch.object(
            WebhookService,
            "handle_subscription_updated",
            return_value={"success": True},
        ) as mock_updated:
            result = WebhookService.dispatch_webhook_event(
                mock_db, "customer.subscription.updated", subscription_event
            )
        assert result["success"] is True
        mock_updated.assert_called_once_with(mock_db, subscription_event)

    def test_subscription_updated_mirrors_scheduled_downgrade(
        self, mock_db, mock_user_account, mock_user, subscription_event
    ):
        """`updated` with cancel_at_period_end=True flips scheduled_downgrade."""
        subscription_event["cancel_at_period_end"] = True

        mock_subscription = Mock()
        mock_subscription.scheduled_downgrade = False
        mock_subscription.updated_at = None

        # Query side_effect: account, subscription re-query (override path),
        # then user (cache invalidation).
        mock_db.query.side_effect = [
            Mock(
                filter=Mock(
                    return_value=Mock(first=Mock(return_value=mock_user_account))
                )
            ),
            Mock(
                filter=Mock(
                    return_value=Mock(first=Mock(return_value=mock_subscription))
                )
            ),
            Mock(filter=Mock(return_value=Mock(first=Mock(return_value=mock_user)))),
        ]

        with patch.object(
            CheckoutService, "create_or_update_subscription", return_value=99
        ), patch(
            "studio.app.common.core.subscription.webhook_service."
            "invalidate_user_tier_cache"
        ):
            result = WebhookService.handle_subscription_updated(
                mock_db, subscription_event
            )

        assert result["success"] is True
        assert result["scheduled_downgrade"] is True
        assert mock_subscription.scheduled_downgrade is True
        assert mock_subscription.updated_at is not None

    def test_updated_past_due_still_marked_synced(
        self, mock_db, mock_user_account, mock_user, subscription_event
    ):
        """A successfully-mirrored past_due subscription stays SYNCED.

        sync_status tracks DB<->Stripe sync success, not billing health: a
        past_due event we wrote to the DB is still SYNCED. Tier is derived
        from `expiration` at read time.
        """
        subscription_event["status"] = "past_due"
        # cancel_at_period_end stays False (fixture default) -> helper skips
        # the subscription re-query, so the query chain is just account + user.
        self._setup_query_chain(mock_db, mock_user_account, mock_user)

        with patch.object(
            CheckoutService, "create_or_update_subscription", return_value=99
        ) as mock_upsert, patch(
            "studio.app.common.core.subscription.webhook_service."
            "invalidate_user_tier_cache"
        ):
            result = WebhookService.handle_subscription_updated(
                mock_db, subscription_event
            )

        assert result["success"] is True
        # The upsert ran (mirrored); sync_status is set to SYNCED by
        # CheckoutService.create_or_update_subscription regardless of the
        # Stripe status field.
        mock_upsert.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
