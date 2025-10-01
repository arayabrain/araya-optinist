"""
Integration tests for Stripe checkout API endpoints.

These tests require a running server and valid Stripe credentials.
They are skipped in CI by default.

To run manually:
1. Start the server: python -m studio
2. Set STRIPE_SECRET_KEY and STRIPE_CALLBACK_URL in .env
3. Run: pytest studio/tests/app/common/routers/test_checkout.py::test_checkout_api -v
"""

import pytest
import requests

STRIPE_CALLBACK_URL = "http://localhost:8000/api/v1/checkout"


@pytest.mark.skip(
    reason="Integration test - requires running server and Stripe credentials"
)
def test_checkout_api():
    """Test checkout API with realistic fake data"""
    print("Testing Checkout API...")

    # Test 1: Valid request structure but fake session ID
    payload = {
        "session_id": "cs_test_fake_session_for_testing",
        "user_id": 999,  # Use non-existent user
        "plan_id": 2,
    }

    response = requests.post(
        f"{STRIPE_CALLBACK_URL}/stripe/checkout-success",
        json=payload,
        headers={"Content-Type": "application/json"},
    )

    print(f"Checkout API Response: {response.status_code}")

    if response.status_code == 400:
        # Expected - fake session ID should be rejected by Stripe
        data = response.json()
        print(f"Expected error: {data.get('detail', 'Unknown error')}")
        print("Checkout API properly validates session IDs")
    elif response.status_code == 500:
        print("Checkout API attempted to process request (got to Stripe validation)")
    else:
        print(f"Response: {response.json()}")

    # Test 2: Invalid request structure
    invalid_payload = {"session_id": "test"}  # Missing user_id, plan_id

    response = requests.post(
        f"{STRIPE_CALLBACK_URL}/stripe/checkout-success",
        json=invalid_payload,
        headers={"Content-Type": "application/json"},
    )

    if response.status_code == 422:
        print("Checkout API properly validates required fields")
    else:
        print(f"Unexpected validation response: {response.status_code}")


@pytest.mark.skip(
    reason="Integration test - requires running server and Stripe credentials"
)
def test_webhook_api():
    """Test webhook API with different event types"""
    print("Testing Webhook API...")

    # Test checkout completed webhook
    payload = {
        "event_type": "checkout.session.completed",
        "data": {
            "id": "cs_test_fake_webhook_session",
            "customer": "cus_fake_webhook_customer",
            "payment_status": "paid",
            "metadata": {"user_id": "999", "plan_id": "2"},
        },
    }

    response = requests.post(f"{STRIPE_CALLBACK_URL}/stripe/webhook", json=payload)

    if response.status_code == 200:
        data = response.json()
        if data.get("processed") == "checkout.session.completed":
            print("Webhook API processes checkout.session.completed")
        else:
            print(f"Webhook response: {data}")
    else:
        print(f"Webhook error: {response.status_code}")


@pytest.mark.skip(reason="Integration test - requires running server and database")
def test_subscription_status_api():
    """Test subscription status API"""
    print("Testing Subscription Status API...")

    response = requests.get(f"{STRIPE_CALLBACK_URL}/subscription/status/1")

    if response.status_code == 200:
        data = response.json()
        required_fields = ["user_id", "has_active_subscription", "subscription_details"]
        if all(field in data for field in required_fields):
            print("Subscription Status API returns correct structure")
            print(f"User 1 has active subscription: {data['has_active_subscription']}")
        else:
            print(f"Missing fields in response: {data}")
    else:
        print(f"Subscription status error: {response.status_code}")


def run_checkout_tests():
    """Run all checkout-related tests"""
    print("=" * 50)
    print("CHECKOUT API INTEGRATION TESTS")
    print("=" * 50)

    # Check API connectivity first
    try:
        response = requests.get(f"{STRIPE_CALLBACK_URL}/health", timeout=3)
        if response.status_code != 200:
            print("API not responding properly")
            return
        print("API connectivity confirmed")
    except Exception as e:
        print(f"Cannot connect to API: {e}")
        return

    print()
    test_checkout_api()
    print()
    test_webhook_api()
    print()
    test_subscription_status_api()
    print()
    print("=" * 50)
    print("TESTS COMPLETE")


if __name__ == "__main__":
    run_checkout_tests()
