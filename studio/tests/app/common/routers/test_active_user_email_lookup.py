"""Tests for the active=True filter on email-based user lookups (issue #629 P5).

After a user soft-deletes their account (``users.active = False``) and
re-registers with the same email, two rows share the email — one inactive,
one active. ``validate_checkout_session`` and ``handle_subscription_schedule_released``
must resolve the email to the *active* user; otherwise the webhook's subscription
(written to the new active user) would be invisible to the old inactive row,
and the UI would stay on "Activation Pending" indefinitely.

These tests verify the SQL filter is constructed with both ``email`` AND
``active`` conditions by inspecting the ``filter(...)`` call args.
"""

from unittest.mock import Mock, patch

import pytest


def _capture_user_filter(mock_db):
    """Configure mock_db so we can inspect the User-by-email .filter() call args.

    The chain `db.query(User).filter(...).first()` is captured at the .filter()
    boundary. .first() is wired to return None so callers go down the
    "no user found" branch (we only care about how filter was constructed).
    """
    mock_db.query.return_value.filter.return_value.first.return_value = None
    return mock_db.query.return_value.filter


def _assert_email_and_active_filters(filter_call):
    """Assert .filter(...) received BOTH an email condition and an ``active IS true``.

    Uses exact SQLAlchemy repr matching (``users.email = :email_1`` and
    ``users.active IS true``) rather than loose substring checks, so we
    don't get false positives from unrelated columns that happen to
    contain "email" or "active", and we verify the condition is
    ``IS true`` specifically (not ``IS false`` or ``== something_else``).
    """
    assert filter_call.called, "User lookup did not reach the filter call"
    args = filter_call.call_args.args
    assert (
        len(args) == 2
    ), f"Expected 2 filter conditions (email + active), got {len(args)}: {args}"
    arg_strs = [str(a).lower() for a in args]
    assert any(
        "email" in s and "= :email" in s for s in arg_strs
    ), f"No email equality filter present: {arg_strs}"
    assert any(
        s == "users.active is true" for s in arg_strs
    ), f"No 'active IS true' filter present: {arg_strs}"


class TestValidateCheckoutSessionActiveFilter:
    """validate_checkout_session must resolve the checkout email to an ACTIVE user."""

    @pytest.mark.asyncio
    async def test_filters_active_users(self):
        from studio.app.common.routers.subscriptions import validate_checkout_session
        from studio.app.common.schemas.checkouts import (
            CheckoutSessionRequest,
            CheckoutValidationStatus,
        )

        # Stripe session: paid + complete + has a customer email.
        mock_session = Mock(payment_status="paid", status="complete")
        mock_session.customer_details = Mock(email="repeat@example.com")

        mock_db = Mock()
        filter_call = _capture_user_filter(mock_db)

        request = CheckoutSessionRequest(session_id="cs_test")
        with patch(
            "studio.app.common.routers.subscriptions.stripe.checkout.Session.retrieve",
            return_value=mock_session,
        ):
            result = await validate_checkout_session(request, db=mock_db)

        _assert_email_and_active_filters(filter_call)
        # No active user matched → WEBHOOK_FAILED (the symptom this fix prevents
        # when an inactive row would otherwise have been matched).
        assert result.status == CheckoutValidationStatus.WEBHOOK_FAILED


class TestScheduleReleasedActiveFilter:
    """handle_subscription_schedule_released resolves the email to ACTIVE only."""

    def test_filters_active_users(self):
        from fastapi import HTTPException

        from studio.app.common.core.subscription.webhook_service import WebhookService

        # Stripe.Subscription.retrieve: dict-like with items.data[0].current_period_end.
        stripe_subscription = {"items": {"data": [{"current_period_end": 2000000000}]}}
        stripe_customer = {"email": "repeat@example.com"}

        mock_db = Mock()
        filter_call = _capture_user_filter(mock_db)

        event_data = {"subscription": "sub_123", "customer": "cus_123"}

        with patch(
            "studio.app.common.core.subscription.subscription_service."
            "SubscriptionService._ensure_stripe_initialized"
        ), patch(
            "studio.app.common.core.subscription.webhook_service."
            "stripe.Subscription.retrieve",
            return_value=stripe_subscription,
        ), patch(
            "studio.app.common.core.subscription.webhook_service."
            "stripe.Customer.retrieve",
            return_value=stripe_customer,
        ):
            # No active user → handler raises HTTPException 404 (or wraps it).
            # We only care that the filter was constructed correctly before
            # the lookup returned None.
            with pytest.raises(HTTPException):
                WebhookService.handle_subscription_schedule_released(
                    mock_db, event_data
                )

        _assert_email_and_active_filters(filter_call)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
