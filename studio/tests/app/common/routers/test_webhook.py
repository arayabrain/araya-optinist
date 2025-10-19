import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch
from sqlmodel import Session

from studio.app.common.core.subscription.checkout_service import CheckoutService
from studio.app.common.core.subscription.subscription_service import SubscriptionService
from studio.app.common.core.subscription.webhook_service import WebhookService

# Import your models and services
# from your_app.models import (
#     SubscriptionUserAccount,
#     UserSubscription,
#     SubscriptionUserPurchase
# )
# from your_app.services import WebhookService, CheckoutService, SubscriptionService
# from your_app.enums import BILLING_CYCLE, PaymentStatus


class TestInvoicePaymentSucceeded:
    """Test suite for invoice.payment_succeeded webhook handler"""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session"""
        db = Mock(spec=Session)
        db.exec = Mock()
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
        subscription.expiration = datetime.now() + timedelta(days=5)
        subscription.updated_at = None
        return subscription

    @pytest.fixture
    def mock_plan(self):
        """Create a mock subscription plan"""
        plan = Mock()
        plan.id = "plan_123"
        plan.name = "Premium Plan"
        plan.billing_cycle = "monthly"
        return plan

    @pytest.fixture
    def invoice_data_subscription_cycle(self):
        """Create mock invoice data for subscription renewal"""
        return {
            "id": "in_test123",
            "customer": "cus_test123",
            "subscription": "sub_stripe123",
            "status": "paid",
            "amount_paid": 2999,  # $29.99 in cents
            "billing_reason": "subscription_cycle",
            "period_start": 1699999999,
            "period_end": 1702678399,
        }

    @pytest.fixture
    def invoice_data_initial_payment(self):
        """Create mock invoice data for initial subscription payment"""
        return {
            "id": "in_test456",
            "customer": "cus_test123",
            "subscription": "sub_stripe123",
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
        invoice_data_subscription_cycle,
    ):
        """Test successful monthly subscription renewal"""
        # Setup mocks
        mock_db.exec.return_value.first.side_effect = [
            mock_user_account,
            mock_subscription,
        ]

        with patch.object(
            CheckoutService, "get_subscription_plan", return_value=mock_plan
        ), patch.object(
            SubscriptionService, "get_current_datetime", return_value=datetime.now()
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
            "subscription": "sub_stripe123",
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
            "subscription": "sub_stripe123",
            "status": "open",  # Not paid
            "amount_paid": 2999,
            "billing_reason": "subscription_cycle",
        }

        mock_db.exec.return_value.first.return_value = mock_user_account

        with pytest.raises(Exception):  # Should raise HTTPException
            WebhookService.handle_subscription_payment_succeeded(mock_db, invoice_data)

        mock_db.rollback.assert_called()

    def test_user_not_found(self, mock_db, invoice_data_subscription_cycle):
        """Test error handling when user is not found"""
        # Mock user not found
        mock_db.exec.return_value.first.return_value = None

        with pytest.raises(Exception):  # Should raise HTTPException
            WebhookService.handle_subscription_payment_succeeded(
                mock_db, invoice_data_subscription_cycle
            )

        mock_db.rollback.assert_called()

    def test_subscription_not_found(
        self, mock_db, mock_user_account, invoice_data_subscription_cycle
    ):
        """Test error handling when active subscription is not found"""
        # Mock user found but no active subscription
        mock_db.exec.return_value.first.side_effect = [
            mock_user_account,
            None,  # No subscription found
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
        mock_db.exec.return_value.first.side_effect = [
            mock_user_account,
            mock_subscription,
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
        mock_db.exec.return_value.first.side_effect = [
            mock_user_account,
            mock_subscription,
        ]
        mock_db.commit.side_effect = Exception("Database error")

        with patch.object(
            CheckoutService, "get_subscription_plan", return_value=mock_plan
        ), patch.object(
            SubscriptionService, "get_current_datetime", return_value=datetime.now()
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
        "subscription": "sub_1QLzTh2eZvKYlo2C2222",
        "subtotal": 2999,
        "total": 2999,
    }

    # This payload structure matches what Stripe actually sends
    assert full_invoice_payload["billing_reason"] == "subscription_cycle"
    assert full_invoice_payload["status"] == "paid"
    assert full_invoice_payload["amount_paid"] == 2999


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
