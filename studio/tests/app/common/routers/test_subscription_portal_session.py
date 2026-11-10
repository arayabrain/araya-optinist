from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import stripe
from fastapi.testclient import TestClient

from studio.__main_unit__ import app
from studio.app.common.core.auth.auth_dependencies import get_current_user
from studio.app.common.db.database import get_db

PORTAL_URL = "/api/subsc/mgmts/portal-session"
ROUTER = "studio.app.common.routers.subscriptions"
GET_CUSTOMER = f"{ROUTER}.get_stripe_customer"
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
        patch(GET_CUSTOMER, new_callable=AsyncMock, return_value=MagicMock(id="cus_1")),
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


def test_no_customer_returns_404_without_calling_stripe(client):
    with (
        patch(GET_CUSTOMER, new_callable=AsyncMock, return_value=None),
        patch(SESSION_CREATE) as create,
    ):
        response = client.post(PORTAL_URL)

    assert response.status_code == 404
    create.assert_not_called()


def test_stripe_error_returns_400(client):
    with (
        patch(GET_CUSTOMER, new_callable=AsyncMock, return_value=MagicMock(id="cus_1")),
        patch(GET_BASE_URL, return_value="https://app.example"),
        patch(SESSION_CREATE, side_effect=stripe.error.InvalidRequestError("x", None)),
    ):
        response = client.post(PORTAL_URL)

    assert response.status_code == 400
    assert response.json()["detail"] == "Failed to open billing portal"
