"""Tests for deletion priority API endpoints."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from studio.__main_unit__ import app
from studio.app.common.core.auth.auth_dependencies import get_current_user
from studio.app.common.db.database import get_db


@pytest.fixture
def mock_user():
    user = MagicMock()
    user.id = 1
    user.email = "test@example.com"
    return user


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture(autouse=True)
def cleanup_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client(mock_user, mock_db):
    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_db] = lambda: mock_db
    return TestClient(app)


class TestGetDeletionPriority:
    @patch(
        "studio.app.common.routers.subscriptions"
        ".SubscriptionService.get_deletion_priority",
        return_value="preserve_outputs",
    )
    def test_returns_default_priority(self, mock_get, client):
        with patch(
            "studio.app.common.routers.subscriptions.stripe_dependency",
        ):
            response = client.get("/api/subsc/deletion-priority")

        assert response.status_code == 200
        assert response.json()["priority"] == "preserve_outputs"

    @patch(
        "studio.app.common.routers.subscriptions"
        ".SubscriptionService.get_deletion_priority",
        return_value="preserve_inputs",
    )
    def test_returns_user_set_priority(self, mock_get, client):
        with patch(
            "studio.app.common.routers.subscriptions.stripe_dependency",
        ):
            response = client.get("/api/subsc/deletion-priority")

        assert response.status_code == 200
        assert response.json()["priority"] == "preserve_inputs"


class TestUpdateDeletionPriority:
    @patch(
        "studio.app.common.routers.subscriptions"
        ".SubscriptionService.update_deletion_priority",
    )
    def test_updates_priority(self, mock_update, client):
        with patch(
            "studio.app.common.routers.subscriptions.stripe_dependency",
        ):
            response = client.put(
                "/api/subsc/deletion-priority",
                json={"priority": "preserve_inputs"},
            )

        assert response.status_code == 200
        assert response.json()["priority"] == "preserve_inputs"

    def test_rejects_invalid_priority(self, client):
        with patch(
            "studio.app.common.routers.subscriptions.stripe_dependency",
        ):
            response = client.put(
                "/api/subsc/deletion-priority",
                json={"priority": "invalid_value"},
            )

        assert response.status_code == 422

    @patch(
        "studio.app.common.routers.subscriptions"
        ".SubscriptionService.update_deletion_priority",
    )
    def test_user_without_subscription_can_set_preference(self, mock_update, client):
        """Users without a subscription can still set deletion preference."""
        with patch(
            "studio.app.common.routers.subscriptions.stripe_dependency",
        ):
            response = client.put(
                "/api/subsc/deletion-priority",
                json={"priority": "preserve_outputs"},
            )

        assert response.status_code == 200
        assert response.json()["priority"] == "preserve_outputs"
        mock_update.assert_called_once()
