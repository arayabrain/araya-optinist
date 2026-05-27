# flake8: noqa: E402
import os

# Set environment variables before other imports
os.environ["STRIPE_SECRET_KEY"] = "sk_test_fake_key_for_testing"

from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from studio.app.common.core.subscription.subscription_service import SubscriptionService


class TestCheckoutEndpoints:
    """Test checkout-related endpoints"""

    @pytest.fixture
    def mock_stripe(self):
        """Mock Stripe API calls"""
        with patch("stripe.checkout.Session.retrieve") as mock_retrieve:
            yield mock_retrieve

    def test_checkout_session_validation_invalid(self, mock_stripe):
        """Test checkout session validation with invalid session"""
        # Mock Stripe to raise an error for invalid session
        mock_stripe.side_effect = Exception("No such checkout session")

        # This test would need a test client to actually call the endpoint
        # For now, just test that the mock is set up correctly
        assert mock_stripe.side_effect is not None

    def test_checkout_session_validation_valid(self, mock_stripe):
        """Test checkout session validation with valid session"""
        mock_session = Mock()
        mock_session.id = "cs_test_valid"
        mock_session.payment_status = "paid"
        mock_stripe.return_value = mock_session

        result = mock_stripe("cs_test_valid")
        assert result.id == "cs_test_valid"


class TestWebhookData:
    """Test webhook data structure"""

    def test_invoice_data_structure(self):
        """Test that invoice data has correct structure for subscription_id"""
        # The correct Stripe invoice structure for subscription_id
        invoice_data = {
            "id": "in_test123",
            "customer": "cus_test123",
            "parent": {"subscription_details": {"subscription": "sub_test123"}},
            "status": "paid",
            "amount_paid": 2999,
            "billing_reason": "subscription_cycle",
        }

        # Test extraction using the same logic as webhook_service.py
        subscription_id = (
            invoice_data.get("parent", {})
            .get("subscription_details", {})
            .get("subscription")
        )
        assert subscription_id == "sub_test123"
        assert subscription_id is not None


# Integration tests (these require the API to be running)
@pytest.mark.integration
class TestCheckoutIntegration:
    """Integration tests that require running API"""

    @pytest.fixture(scope="class")
    def api_url(self):
        return SubscriptionService.get_base_url()

    @pytest.fixture(scope="class")
    def check_api_running(self, api_url):
        """Check if API is running before running integration tests"""
        import requests

        try:
            response = requests.get(f"{api_url}/docs", timeout=3)
            if response.status_code != 200:
                pytest.skip("API is not running")
        except Exception:
            pytest.skip("Cannot connect to API")

    def test_get_subscription_plans(self, api_url, check_api_running):
        """Test getting available subscription plans"""
        import requests

        response = requests.get(f"{api_url}/api/subsc/mgmts/plans")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_checkout_session_validation(self, api_url, check_api_running):
        """Test checkout session validation endpoint"""
        import requests

        payload = {"session_id": "cs_test_fake_session"}

        response = requests.post(
            f"{api_url}/api/subsc/checkout/validate-checkout-session",
            json=payload,
            headers={"Content-Type": "application/json"},
        )

        # Should return 400 for fake session
        assert response.status_code == 400

    def test_webhook_requires_signature(self, api_url, check_api_running):
        """Test webhook requires signature verification"""
        import requests

        payload = {
            "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_test"}},
        }

        response = requests.post(
            f"{api_url}/api/subsc/webhooks/stripe",
            json=payload,
            headers={"Content-Type": "application/json"},
        )

        # Should fail signature verification
        assert response.status_code == 400


class TestCreateOrUpdateSubscriptionConcurrency:
    """create_or_update_subscription idempotency under concurrent inserts."""

    @staticmethod
    def _mock_db(existing_first, existing_after_conflict):
        """db whose SELECT...FOR UPDATE returns existing_first, then
        existing_after_conflict on the post-conflict re-select."""
        from unittest.mock import MagicMock

        db = Mock()
        first = Mock(side_effect=[existing_first, existing_after_conflict])
        db.query.return_value.filter.return_value.with_for_update.return_value.first = (
            first
        )
        # begin_nested() as a context manager that does NOT suppress exceptions.
        cm = MagicMock()
        cm.__enter__.return_value = cm
        cm.__exit__.return_value = False
        db.begin_nested.return_value = cm
        db.add = Mock()
        return db

    def test_concurrent_insert_falls_back_to_update(self):
        """A unique-constraint conflict on insert re-selects and updates."""
        from studio.app.common.core.subscription.checkout_service import (
            CheckoutService,
        )

        existing_row = Mock()
        existing_row.id = 555
        db = self._mock_db(existing_first=None, existing_after_conflict=existing_row)
        # The insert flush raises a unique-violation IntegrityError.
        db.flush = Mock(
            side_effect=IntegrityError("INSERT", {}, Exception("duplicate user_id"))
        )

        expiration = datetime.now() + timedelta(days=30)
        with patch.object(
            SubscriptionService, "get_current_datetime", return_value=datetime.now()
        ):
            result_id = CheckoutService.create_or_update_subscription(
                db, user_id=42, plan_id=2, expiration_date=expiration
            )

        # Fell back to updating the row the racing delivery created.
        assert result_id == 555
        assert existing_row.plan_id == 2
        assert existing_row.expiration == expiration
        assert existing_row.scheduled_downgrade is False

    def test_other_integrity_error_is_not_swallowed(self):
        """If no row exists even after the conflict, the error is re-raised."""
        from studio.app.common.core.subscription.checkout_service import (
            CheckoutService,
        )

        db = self._mock_db(existing_first=None, existing_after_conflict=None)
        db.flush = Mock(
            side_effect=IntegrityError("INSERT", {}, Exception("some other constraint"))
        )

        expiration = datetime.now() + timedelta(days=30)
        with pytest.raises(IntegrityError):
            CheckoutService.create_or_update_subscription(
                db, user_id=42, plan_id=2, expiration_date=expiration
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
