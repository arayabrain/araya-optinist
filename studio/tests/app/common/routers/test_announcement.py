import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from studio.__main_unit__ import app
from studio.app.common.routers import announcement

PARAMETER = "/test/optinist/announcement"
NOW = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def reset_cache():
    announcement._cache.update(expires_at=0.0, value={})
    yield
    announcement._cache.update(expires_at=0.0, value={})


@pytest.fixture
def ssm(monkeypatch):
    monkeypatch.setenv(announcement.ANNOUNCEMENT_SSM_PARAMETER_ENV, PARAMETER)
    client = MagicMock()
    with patch.object(announcement, "_ssm_client", return_value=client):
        yield client


def ssm_value(client, value):
    client.get_parameter.return_value = {"Parameter": {"Value": value}}


def live(message="Maintenance Wed 13:00-14:00 JST"):
    until = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    return json.dumps({"message": message, "until": until})


class TestParseAnnouncement:
    def test_live_announcement_returns_id_and_message(self):
        raw = json.dumps({"message": " Down soon ", "until": "2026-11-02T00:00:00Z"})
        result = announcement.parse_announcement(raw, NOW)
        assert result["message"] == "Down soon"
        assert len(result["id"]) == 8

    def test_id_changes_with_message(self):
        a = json.dumps({"message": "one", "until": "2026-11-02T00:00:00Z"})
        b = json.dumps({"message": "two", "until": "2026-11-02T00:00:00Z"})
        assert (
            announcement.parse_announcement(a, NOW)["id"]
            != announcement.parse_announcement(b, NOW)["id"]
        )

    @pytest.mark.parametrize(
        "raw",
        [
            "{}",
            "not json",
            "[]",
            json.dumps({"message": "x"}),
            json.dumps({"until": "2026-11-02T00:00:00Z"}),
            json.dumps({"message": "  ", "until": "2026-11-02T00:00:00Z"}),
            json.dumps({"message": "x", "until": "tomorrow"}),
            json.dumps({"message": "x", "until": 1793404800}),
            json.dumps({"message": 5, "until": "2026-11-02T00:00:00Z"}),
            json.dumps({"message": "naive", "until": "2026-11-02T00:00:00"}),
            json.dumps({"message": "expired", "until": "2026-10-31T23:59:59Z"}),
        ],
    )
    def test_empty_malformed_naive_or_expired_shows_nothing(self, raw):
        assert announcement.parse_announcement(raw, NOW) == {}

    def test_message_is_capped(self):
        raw = json.dumps({"message": "a" * 600, "until": "2026-11-02T00:00:00Z"})
        result = announcement.parse_announcement(raw, NOW)
        assert len(result["message"]) == announcement.MAX_MESSAGE_LENGTH


class TestAnnouncementEndpoint:
    def test_unset_env_returns_empty_without_ssm(self, monkeypatch):
        monkeypatch.delenv(announcement.ANNOUNCEMENT_SSM_PARAMETER_ENV, raising=False)
        with patch.object(announcement, "_ssm_client") as factory:
            response = TestClient(app).get("/announcement")
        assert response.status_code == 200
        assert response.json() == {}
        factory.assert_not_called()

    def test_live_announcement_without_auth(self, ssm):
        ssm_value(ssm, live("Down Wed"))
        response = TestClient(app).get("/announcement")
        assert response.status_code == 200
        assert response.json()["message"] == "Down Wed"
        ssm.get_parameter.assert_called_once_with(Name=PARAMETER)

    def test_success_is_cached(self, ssm):
        ssm_value(ssm, live())
        client = TestClient(app)
        client.get("/announcement")
        client.get("/announcement")
        assert ssm.get_parameter.call_count == 1

    def test_failure_returns_empty_and_is_cached(self, ssm):
        ssm.get_parameter.side_effect = RuntimeError("timeout")
        client = TestClient(app)
        assert client.get("/announcement").json() == {}
        assert client.get("/announcement").json() == {}
        assert ssm.get_parameter.call_count == 1

    def test_cache_expiry_rereads(self, ssm):
        ssm_value(ssm, live("first"))
        client = TestClient(app)
        assert client.get("/announcement").json()["message"] == "first"
        ssm_value(ssm, live("second"))
        announcement._cache["expires_at"] = 0.0
        assert client.get("/announcement").json()["message"] == "second"

    def test_refill_in_progress_serves_cached_value(self, ssm):
        announcement._cache["value"] = {"id": "old", "message": "old"}
        with announcement._refill_lock:
            response = TestClient(app).get("/announcement")
        assert response.json() == {"id": "old", "message": "old"}
        ssm.get_parameter.assert_not_called()

    def test_ssm_client_makes_a_single_attempt(self, monkeypatch):
        monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
        announcement._ssm_client.cache_clear()
        try:
            config = announcement._ssm_client().meta.config
        finally:
            announcement._ssm_client.cache_clear()
        assert config.retries["total_max_attempts"] == 1
