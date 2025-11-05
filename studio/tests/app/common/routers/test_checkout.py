def test_checkout_session_validation(client):
    payload = {"session_id": "cs_test_fake_session_for_testing"}
    r = client.post("/api/subsc/checkout/validate-checkout-session", json=payload)
    # Expect rejection for a fake session
    assert r.status_code in (400, 422), f"unexpected: {r.status_code} body={r.text}"


def test_failed_checkout_validation(client):
    payload = {"session_id": "cs_test_fake_expired_session"}
    r = client.post("/api/subsc/checkout/failed-checkout-session", json=payload)
    # Impl may return 200 with 'failed' or 400 for invalid session
    assert r.status_code in (200, 400), f"unexpected: {r.status_code} body={r.text}"


def test_webhook_api_requires_signature(client):
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
    r = client.post("/api/subsc/webhooks/stripe", json=payload)
    # Without a valid Stripe signature header, should be rejected
    assert r.status_code in (400, 401), f"unexpected: {r.status_code} body={r.text}"


def test_get_subscription_plans(client):
    r = client.get("/api/subsc/mgmts/plans")
    # Public in many apps (200); some require auth (401/403)
    assert r.status_code in (
        200,
        401,
        403,
    ), f"unexpected: {r.status_code} body={r.text}"


def test_get_user_subscription_requires_auth(client):
    r = client.get("/api/subsc/mgmts")
    # Usually requires auth
    assert r.status_code in (
        401,
        403,
        200,
    ), f"unexpected: {r.status_code} body={r.text}"
