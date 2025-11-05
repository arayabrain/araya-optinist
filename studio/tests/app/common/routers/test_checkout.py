import requests

from studio.app.common.core.subscription.subscription_service import SubscriptionService

STRIPE_CALLBACK_URL = SubscriptionService.get_base_url()


def test_checkout_session_validation():
    """Test checkout session validation endpoint"""
    print("Testing Checkout Session Validation...")

    # Test 1: Valid checkout session structure
    payload = {
        "session_id": "cs_test_fake_session_for_testing",
    }

    response = requests.post(
        f"{STRIPE_CALLBACK_URL}/api/subsc/checkout/validate-checkout-session",
        json=payload,
        headers={"Content-Type": "application/json"},
    )

    print(f"Validation API Response: {response.status_code}")

    if response.status_code == 400:
        data = response.json()
        print(f"Expected error: {data.get('detail', 'Unknown error')}")
        print("Checkout validation properly rejects invalid sessions")
    else:
        print(f"Response: {response.json()}")


def test_failed_checkout_validation():
    """Test failed checkout session validation"""
    print("Testing Failed Checkout Validation...")

    payload = {
        "session_id": "cs_test_fake_expired_session",
    }

    response = requests.post(
        f"{STRIPE_CALLBACK_URL}/api/subsc/checkout/failed-checkout-session",
        json=payload,
        headers={"Content-Type": "application/json"},
    )

    print(f"Failed validation response: {response.status_code}")
    if response.status_code == 200:
        result = response.json()
        print(f"Session is failed/expired: {result}")
        print("Failed checkout validation endpoint works")
    else:
        print(f"Response: {response.json()}")


def test_webhook_api():
    """Test webhook API with Stripe signature verification"""
    print("Testing Webhook API...")
    print("Note: Real webhooks require valid Stripe signatures")
    print("    This test will likely fail signature verification")

    # Stripe webhooks require signature verification
    # This test demonstrates the endpoint but won't work without real Stripe data
    payload = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_fake_webhook_session",
                "customer": "cus_fake_webhook_customer",
                "payment_status": "paid",
                "metadata": {"user_id": "999", "plan_id": "2"},
            }
        },
    }

    response = requests.post(
        f"{STRIPE_CALLBACK_URL}/api/subsc/webhooks/stripe",
        json=payload,
        headers={"Content-Type": "application/json"},
    )

    if response.status_code == 400:
        print("Webhook properly requires signature verification")
    elif response.status_code == 200:
        data = response.json()
        print(f"Webhook response: {data}")
    else:
        print(f"Webhook error: {response.status_code} - {response.text}")


def test_get_subscription_plans():
    """Test getting available subscription plans"""
    print("Testing Get Subscription Plans...")

    response = requests.get(f"{STRIPE_CALLBACK_URL}/api/subsc/mgmts/plans")

    if response.status_code == 200:
        data = response.json()
        print(f"Found {len(data)} subscription plans")
        if data:
            print(f"   Sample plan: {data[0].get('name', 'Unknown')}")
    else:
        print(f"Plans error: {response.status_code}")


def test_get_user_subscription():
    """Test getting user subscription (requires authentication)"""
    print("Testing Get User Subscription...")
    print("Note: This endpoint requires authentication")

    response = requests.get(f"{STRIPE_CALLBACK_URL}/api/subsc/mgmts")

    if response.status_code == 401 or response.status_code == 403:
        print("Subscription endpoint properly requires authentication")
    elif response.status_code == 200:
        data = response.json()
        print(f"Subscription data: {data}")
    else:
        print(f"Subscription error: {response.status_code}")


def run_checkout_tests():
    """Run all checkout-related tests"""
    print("=" * 50)
    print("SUBSCRIPTION API INTEGRATION TESTS")
    print("=" * 50)

    # Check API connectivity first - try docs endpoint
    try:
        response = requests.get(f"{STRIPE_CALLBACK_URL}/docs", timeout=3)
        if response.status_code == 200:
            print("API is reachable")
        else:
            # Try plans endpoint instead
            response = requests.get(
                f"{STRIPE_CALLBACK_URL}/api/subsc/mgmts/plans", timeout=3
            )
            if response.status_code in [200, 401, 403]:
                print("API is reachable")
            else:
                print(f"API responded with status {response.status_code}")
    except Exception as e:
        print(f"Cannot connect to API: {e}")
        return

    print()
    test_get_subscription_plans()
    print()
    test_checkout_session_validation()
    print()
    test_failed_checkout_validation()
    print()
    test_get_user_subscription()
    print()
    test_webhook_api()
    print()
    print("=" * 50)
    print("TESTS COMPLETE")


if __name__ == "__main__":
    run_checkout_tests()
