"""Tests for expiration lifecycle email service."""

from datetime import timedelta
from unittest.mock import MagicMock, patch

from studio.app.common.core.subscription.email_service import (
    NOTIFICATION_FROM_EMAIL_ENV,
    EmailService,
)
from studio.app.common.core.utils.datetime_utils import get_current_datetime

SENDER = "noreply@example.org"
RECIPIENT = "user@example.org"
GB = 1024**3


class TestIsEnabled:
    def test_disabled_when_env_unset(self, monkeypatch):
        monkeypatch.delenv(NOTIFICATION_FROM_EMAIL_ENV, raising=False)
        assert EmailService.is_enabled() is False

    def test_enabled_when_env_set(self, monkeypatch):
        monkeypatch.setenv(NOTIFICATION_FROM_EMAIL_ENV, SENDER)
        assert EmailService.is_enabled() is True


class TestSend:
    def test_disabled_returns_false_without_boto3(self, monkeypatch):
        monkeypatch.delenv(NOTIFICATION_FROM_EMAIL_ENV, raising=False)
        with patch("boto3.client") as mock_client:
            ok = EmailService.send_deletion_summary_email(RECIPIENT, GB, 3)
        assert ok is False
        mock_client.assert_not_called()

    def test_sends_via_ses(self, monkeypatch):
        monkeypatch.setenv(NOTIFICATION_FROM_EMAIL_ENV, SENDER)
        mock_ses = MagicMock()
        with patch("boto3.client", return_value=mock_ses) as mock_client:
            ok = EmailService.send_deletion_summary_email(RECIPIENT, 2 * GB, 5)

        assert ok is True
        mock_client.assert_called_once_with("ses")
        kwargs = mock_ses.send_email.call_args.kwargs
        assert kwargs["Source"] == SENDER
        assert kwargs["Destination"] == {"ToAddresses": [RECIPIENT]}
        assert "2.00 GB" in kwargs["Message"]["Body"]["Text"]["Data"]
        assert "5 data unit(s)" in kwargs["Message"]["Body"]["Text"]["Data"]

    def test_ses_error_returns_false(self, monkeypatch):
        monkeypatch.setenv(NOTIFICATION_FROM_EMAIL_ENV, SENDER)
        mock_ses = MagicMock()
        mock_ses.send_email.side_effect = Exception("throttled")
        with patch("boto3.client", return_value=mock_ses):
            ok = EmailService.send_grace_period_reminder_email(
                RECIPIENT, get_current_datetime(), 6 * GB
            )
        assert ok is False


class TestGraceStartTemplate:
    def _send_and_get_body(self, grace_end):
        mock_ses = MagicMock()
        with patch("boto3.client", return_value=mock_ses):
            ok = EmailService.send_grace_period_start_email(
                RECIPIENT,
                grace_end,
                6 * GB,
                grace_end + timedelta(days=1),
            )
        assert ok is True
        return mock_ses.send_email.call_args.kwargs["Message"]["Body"]["Text"]["Data"]

    def test_future_grace_end_uses_present_tense(self, monkeypatch):
        monkeypatch.setenv(NOTIFICATION_FROM_EMAIL_ENV, SENDER)
        body = self._send_and_get_body(get_current_datetime() + timedelta(days=25))
        assert "grace period that ends on" in body

    def test_past_grace_end_uses_past_tense(self, monkeypatch):
        monkeypatch.setenv(NOTIFICATION_FROM_EMAIL_ENV, SENDER)
        body = self._send_and_get_body(get_current_datetime() - timedelta(days=10))
        assert "ended on" in body

    def test_naive_datetimes_accepted(self, monkeypatch):
        monkeypatch.setenv(NOTIFICATION_FROM_EMAIL_ENV, SENDER)
        naive = (get_current_datetime() + timedelta(days=25)).replace(tzinfo=None)
        body = self._send_and_get_body(naive)
        assert "(UTC)" in body
