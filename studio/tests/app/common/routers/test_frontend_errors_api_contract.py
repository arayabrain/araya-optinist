"""
Contract Tests for Frontend Error Reporting API

Verifies that the POST /users/me/frontend-errors endpoint response shape
matches the frontend ErrorEntry interface in:
  frontend/src/utils/errorReporter.ts

Tested endpoint:
  - POST /users/me/frontend-errors -> { count: number }

Request body contract (FrontendErrorBatch):
  { errors: FrontendErrorItem[] }

FrontendErrorItem:
  - level: "error" | "warn"       (required)
  - message: string               (required, max 2100 chars)
  - source?: string               (optional)
  - url?: string                  (optional)
  - timestamp?: string            (optional)
"""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from studio.app.common.core.auth.auth_dependencies import get_current_user
from studio.app.common.routers.users_me import _frontend_error_timestamps
from studio.app.main import app

# ============================================================================
# Frontend Contract Definitions
# ============================================================================

RESPONSE_REQUIRED_FIELDS = {
    "count": int,
}

VALID_REQUEST_BODY = {
    "errors": [
        {
            "level": "error",
            "message": "Uncaught TypeError: Cannot read properties of undefined",
            "url": "http://localhost:3000/workflow",
            "source": "http://localhost:3000/static/js/main.js",
            "timestamp": "2026-03-18T10:00:00.000Z",
        },
        {
            "level": "warn",
            "message": "Deprecation warning: componentWillMount",
        },
    ]
}


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def mock_user():
    user = MagicMock()
    user.id = 99
    user.uid = "contract-test-user"
    return user


@pytest.fixture(autouse=True)
def clear_rate_limiter():
    _frontend_error_timestamps.clear()
    yield
    _frontend_error_timestamps.clear()


@pytest.fixture(autouse=True)
def cleanup_overrides():
    original_overrides = app.dependency_overrides.copy()
    yield
    app.dependency_overrides.clear()
    app.dependency_overrides.update(original_overrides)


@pytest.fixture
def client(mock_user):
    app.dependency_overrides[get_current_user] = lambda: mock_user
    return TestClient(app)


# ============================================================================
# Contract Tests
# ============================================================================


class TestFrontendErrorsContract:
    """Verify response shape matches frontend expectations."""

    def test_response_has_count_field(self, client):
        response = client.post("/users/me/frontend-errors", json=VALID_REQUEST_BODY)
        assert response.status_code == 200
        data = response.json()
        for field, expected_type in RESPONSE_REQUIRED_FIELDS.items():
            assert field in data, f"Missing required field: {field}"
            assert isinstance(data[field], expected_type), (
                f"Field '{field}' type mismatch: "
                f"expected {expected_type}, got {type(data[field])}"
            )

    def test_count_matches_batch_size(self, client):
        response = client.post("/users/me/frontend-errors", json=VALID_REQUEST_BODY)
        data = response.json()
        assert data["count"] == len(VALID_REQUEST_BODY["errors"])

    def test_accepts_minimal_error_item(self, client):
        """Minimal payload: only required fields."""
        response = client.post(
            "/users/me/frontend-errors",
            json={"errors": [{"level": "error", "message": "test"}]},
        )
        assert response.status_code == 200
        assert response.json()["count"] == 1

    def test_accepts_warn_level(self, client):
        response = client.post(
            "/users/me/frontend-errors",
            json={"errors": [{"level": "warn", "message": "warning"}]},
        )
        assert response.status_code == 200

    def test_rejects_invalid_level(self, client):
        response = client.post(
            "/users/me/frontend-errors",
            json={"errors": [{"level": "info", "message": "test"}]},
        )
        assert response.status_code == 422

    def test_rejects_empty_errors_list(self, client):
        """Backend should accept empty list (no errors to log)."""
        response = client.post("/users/me/frontend-errors", json={"errors": []})
        assert response.status_code == 200
        assert response.json()["count"] == 0

    def test_rejects_missing_message(self, client):
        response = client.post(
            "/users/me/frontend-errors",
            json={"errors": [{"level": "error"}]},
        )
        assert response.status_code == 422

    def test_rejects_oversized_batch(self, client):
        """Batch exceeding max_items=20 should be rejected."""
        response = client.post(
            "/users/me/frontend-errors",
            json={
                "errors": [{"level": "error", "message": f"msg {i}"} for i in range(21)]
            },
        )
        assert response.status_code == 422

    def test_rate_limit_returns_429(self, client, mock_user):
        """Rate-limited requests return 429."""
        import time

        _frontend_error_timestamps[mock_user.id] = [time.time()] * 10
        response = client.post(
            "/users/me/frontend-errors",
            json={"errors": [{"level": "error", "message": "test"}]},
        )
        assert response.status_code == 429
