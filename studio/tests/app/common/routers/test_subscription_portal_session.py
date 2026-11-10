import asyncio
from unittest.mock import MagicMock, patch

import pytest
import stripe
from fastapi.testclient import TestClient

from studio.__main_unit__ import app
from studio.app.common.core.auth.auth_dependencies import get_current_user
from studio.app.common.db.database import get_db

PORTAL_URL = "/api/subsc/mgmts/portal-session"
ROUTER = "studio.app.common.routers.subscriptions"
GET_ACCOUNT = f"{ROUTER}.CheckoutService.get_subscription_account"
SESSION_CREATE = f"{ROUTER}.stripe.billing_portal.Session.create"
GET_BASE_URL = f"{ROUTER}.SubscriptionService.get_base_url"


@pytest.fixture
def client():
    user = MagicMock(id=1, email="test@example.com")
    original = app.dependency_overrides.copy()
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: MagicMock()
    yield TestClient(app)
    app.dependency_overrides.clear()
    app.dependency_overrides.update(original)


def test_returns_portal_url_for_the_users_customer(client):
    with (
        patch(GET_ACCOUNT, return_value=MagicMock(provider_customer_id="cus_1")),
        patch(GET_BASE_URL, return_value="https://app.example"),
        patch(
            SESSION_CREATE,
            return_value=MagicMock(url="https://billing.stripe.com/p/session/x"),
        ) as create,
    ):
        response = client.post(PORTAL_URL)

    assert response.status_code == 200
    assert response.json() == {"url": "https://billing.stripe.com/p/session/x"}
    create.assert_called_once_with(
        customer="cus_1", return_url="https://app.example/subscription/manage"
    )


def test_no_binding_returns_404_without_email_lookup(client):
    stranger = MagicMock(id="cus_other", email="test@example.com")
    with (
        patch(GET_ACCOUNT, return_value=None),
        patch("stripe.Customer.list", return_value=MagicMock(data=[stranger])) as lst,
        patch(SESSION_CREATE) as create,
    ):
        response = client.post(PORTAL_URL)

    assert response.status_code == 404
    lst.assert_not_called()
    create.assert_not_called()


def test_stripe_call_runs_off_the_event_loop(client):
    def create(**_):
        with pytest.raises(RuntimeError):
            asyncio.get_running_loop()
        return MagicMock(url="https://billing.stripe.com/p/session/x")

    with (
        patch(GET_ACCOUNT, return_value=MagicMock(provider_customer_id="cus_1")),
        patch(GET_BASE_URL, return_value="https://app.example"),
        patch(SESSION_CREATE, side_effect=create),
    ):
        response = client.post(PORTAL_URL)

    assert response.status_code == 200


def test_stripe_error_returns_400(client):
    with (
        patch(GET_ACCOUNT, return_value=MagicMock(provider_customer_id="cus_1")),
        patch(GET_BASE_URL, return_value="https://app.example"),
        patch(SESSION_CREATE, side_effect=stripe.error.InvalidRequestError("x", None)),
    ):
        response = client.post(PORTAL_URL)

    assert response.status_code == 400
    assert response.json()["detail"] == "Failed to open billing portal"
