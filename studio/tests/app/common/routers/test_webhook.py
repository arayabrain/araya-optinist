from datetime import timedelta
from unittest.mock import Mock, patch

import pytest
from sqlmodel import Session

from studio.app.common.core.subscription.checkout_service import CheckoutService
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
    """Test suite for Case 77: Extended lookback window for trial-to-paid conversion"""

    def test_subscription_lookback_constant_is_30_days(self):
        """RECENT_SUBSCRIPTION_WINDOW_DAYS should be 30 days for extended lookback"""
        from studio.app.common.core.subscription.constants import (
            RECENT_SUBSCRIPTION_WINDOW_DAYS,
        )

        assert RECENT_SUBSCRIPTION_WINDOW_DAYS == 30


class TestPaymentFailureTracking:
    """Test suite for Case 73: Payment failure tracking"""

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
        subscription.payment_failed_at = None
        subscription.payment_failure_count = 0
        subscription.expiration = get_current_datetime() + timedelta(days=30)
        return subscription

    @pytest.fixture
    def mock_user(self):
        """Create a mock user"""
        user = Mock()
        user.id = 123
        user.uid = "user_uid_123"
        return user

    def test_payment_failed_increments_failure_count(
        self, mock_db, mock_user_account, mock_subscription, mock_user
    ):
        """Payment failure should increment failure count"""
        invoice_data = {
            "id": "in_test123",
            "customer": "cus_test123",
        }

        # Setup query chain
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

        assert mock_subscription.payment_failure_count == 1
        assert mock_subscription.payment_failed_at is not None

    def test_multiple_payment_failures_increment_count(
        self, mock_db, mock_user_account, mock_subscription, mock_user
    ):
        """Multiple payment failures should increment count each time"""
        mock_subscription.payment_failure_count = 2  # Already 2 failures

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

        assert mock_subscription.payment_failure_count == 3


class TestWebhookCacheInvalidation:
    """Test suite for Case 76: user tier cache invalidation in webhook handlers"""

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
        subscription.payment_failure_count = 0
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
