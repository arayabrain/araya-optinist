"""
Email notifications for the expiration data-deletion lifecycle.

Sends via AWS SES. The feature is enabled by setting NOTIFICATION_FROM_EMAIL to a
verified SES sender address; when unset, every send is a logged no-op and the
expiration lifecycle behaves as if this module did not exist.
"""

from datetime import datetime

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.subscription.constants import (
    StorageQuota,
    StorageSize,
    SubscriptionPeriods,
)
from studio.app.common.core.utils.config_handler import get_env_var
from studio.app.common.core.utils.datetime_utils import (
    ensure_utc,
    format_date_for_display,
    get_current_datetime,
)
from studio.app.const import FRONTEND_URL

logger = AppLogger.get_logger()

NOTIFICATION_FROM_EMAIL_ENV = "NOTIFICATION_FROM_EMAIL"

_GRACE_START_SUBJECT = (
    "OptiNiSt Cloud: your premium plan expired - data deletion notice"
)
_GRACE_START_BODY = """\
Your OptiNiSt Cloud premium subscription has expired.

{grace_line}

Your current storage usage is {usage_gb} GB, which exceeds the free plan
quota of {quota_gb} GB.

Data exceeding the free plan quota will be automatically deleted on or
after {deletion_not_before}.

To keep your data, renew your subscription or reduce your storage usage
below {quota_gb} GB before that date.

Sign in to manage your subscription: {frontend_url}
"""

_GRACE_REMINDER_SUBJECT = "OptiNiSt Cloud: reminder - data deletion approaching"
_GRACE_REMINDER_BODY = """\
This is a reminder about your expired OptiNiSt Cloud premium subscription.

{grace_line}

Your current storage usage is {usage_gb} GB, which exceeds the free plan
quota of {quota_gb} GB. After the grace period ends, data exceeding the
free plan quota will be automatically deleted.

To keep your data, renew your subscription or reduce your storage usage
below {quota_gb} GB.

Sign in to manage your subscription: {frontend_url}
"""

_DELETION_SUMMARY_SUBJECT = "OptiNiSt Cloud: automatic data deletion completed"
_DELETION_SUMMARY_BODY = """\
Automatic data deletion has run on your OptiNiSt Cloud account because your
premium subscription expired and your storage usage exceeded the free plan
quota of {quota_gb} GB.

In this pass, {freed_gb} GB across {units} data unit(s) was removed.

Data exceeding the free plan quota may continue to be removed while your
subscription remains expired.

Sign in to review your data or renew your subscription: {frontend_url}
"""


def _gb(num_bytes: int) -> str:
    return f"{num_bytes / StorageSize.GB:.2f}"


class EmailService:
    """SES-backed lifecycle emails. All send methods return bool and never raise."""

    @staticmethod
    def is_enabled() -> bool:
        # Read per call (not at import) so tests and runtime config changes work.
        return bool(get_env_var(NOTIFICATION_FROM_EMAIL_ENV, default=""))

    @classmethod
    def send_grace_period_start_email(
        cls,
        to_email: str,
        grace_end: datetime,
        storage_usage_bytes: int,
        deletion_not_before: datetime,
    ) -> bool:
        grace_end = ensure_utc(grace_end)
        if grace_end < get_current_datetime():
            grace_line = (
                f"The {SubscriptionPeriods.GRACE_PERIOD_DAYS}-day grace period "
                f"ended on {format_date_for_display(grace_end)}."
            )
        else:
            grace_line = (
                f"You are now in a {SubscriptionPeriods.GRACE_PERIOD_DAYS}-day "
                f"grace period that ends on {format_date_for_display(grace_end)}."
            )
        body = _GRACE_START_BODY.format(
            grace_line=grace_line,
            usage_gb=_gb(storage_usage_bytes),
            quota_gb=StorageQuota.FREE,
            deletion_not_before=format_date_for_display(
                ensure_utc(deletion_not_before)
            ),
            frontend_url=FRONTEND_URL,
        )
        return cls._send(to_email, _GRACE_START_SUBJECT, body)

    @classmethod
    def send_grace_period_reminder_email(
        cls,
        to_email: str,
        grace_end: datetime,
        storage_usage_bytes: int,
    ) -> bool:
        grace_end = ensure_utc(grace_end)
        tense = "ended" if grace_end < get_current_datetime() else "ends"
        body = _GRACE_REMINDER_BODY.format(
            grace_line=(
                f"The grace period {tense} on {format_date_for_display(grace_end)}."
            ),
            usage_gb=_gb(storage_usage_bytes),
            quota_gb=StorageQuota.FREE,
            frontend_url=FRONTEND_URL,
        )
        return cls._send(to_email, _GRACE_REMINDER_SUBJECT, body)

    @classmethod
    def send_deletion_summary_email(
        cls,
        to_email: str,
        bytes_deleted: int,
        units_deleted: int,
    ) -> bool:
        body = _DELETION_SUMMARY_BODY.format(
            quota_gb=StorageQuota.FREE,
            freed_gb=_gb(bytes_deleted),
            units=units_deleted,
            frontend_url=FRONTEND_URL,
        )
        return cls._send(to_email, _DELETION_SUMMARY_SUBJECT, body)

    @staticmethod
    def _send(to_email: str, subject: str, body: str) -> bool:
        sender = get_env_var(NOTIFICATION_FROM_EMAIL_ENV, default="")
        if not sender:
            logger.info(
                "Email notifications disabled "
                f"({NOTIFICATION_FROM_EMAIL_ENV} not set), skipping send"
            )
            return False
        try:
            import boto3

            ses = boto3.client("ses")
            ses.send_email(
                Source=sender,
                Destination={"ToAddresses": [to_email]},
                Message={
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
                },
            )
            logger.info(f"Sent notification email: {subject}")
            return True
        except Exception as e:
            logger.error(f"Failed to send notification email ({subject}): {e}")
            return False
