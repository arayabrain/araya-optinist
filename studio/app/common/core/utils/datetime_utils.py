"""
Centralized datetime utilities for consistent timezone handling.

All datetime operations in the application should use these utilities
to ensure consistent UTC timezone handling and avoid naive datetime issues.

Note: Lambda packages (infrastructure/terraform/*_package/) cannot import from
this module as they are deployed as isolated ZIP files. If datetime handling
logic changes here, the following Lambda packages should also be updated:
  - free_manager_package/free_manager.py
  - free_manager_package/free_user_utils.py
  - free_cleanup_package/free_cleanup.py
  - premium_manager_package/premium_manager.py
  - cost_tracker_package/cost_tracker.py
  - common_user_manager_package/common_user_manager.py
  - storage_reconciliation_package/storage_reconciliation.py
"""

from datetime import datetime, timezone

from dateutil.tz import tzlocal


def get_current_datetime() -> datetime:
    """
    Get the current UTC datetime.

    Returns:
        datetime: Current datetime with UTC timezone

    Example:
        >>> from studio.app.common.core.utils.datetime_utils import get_current_datetime
        >>> now = get_current_datetime()
        >>> now.tzinfo
        datetime.timezone.utc
    """
    return datetime.now(timezone.utc)


def get_current_timestamp() -> float:
    """
    Get the current UTC timestamp as a float (seconds since epoch).

    Returns:
        float: Current UTC timestamp

    Example:
        >>> from studio.app.common.core.utils.datetime_utils import (
        ...     get_current_timestamp
        ... )
        >>> ts = get_current_timestamp()
    """
    return datetime.now(timezone.utc).timestamp()


def get_current_datetime_formatted(format_string: str = "%Y/%m/%d %H:%M:%S") -> str:
    """
    Get the current UTC datetime as a formatted string.

    Args:
        format_string: strftime format string (default: "%Y/%m/%d %H:%M:%S")

    Returns:
        str: Formatted datetime string

    Example:
        >>> from studio.app.common.core.utils.datetime_utils import (
        ...     get_current_datetime_formatted
        ... )
        >>> formatted = get_current_datetime_formatted()
        >>> formatted  # e.g., "2024/01/15 10:30:45"
    """
    return datetime.now(timezone.utc).strftime(format_string)


def get_local_datetime_formatted(format_string: str = "%Y/%m/%d %H:%M:%S") -> str:
    """
    Get the current local datetime as a formatted string.

    Use this for experiment timestamps that users correlate with their lab work,
    notes, and other local-time-based records. For system operations, prefer
    get_current_datetime_formatted() which returns UTC.

    Args:
        format_string: strftime format string (default: "%Y/%m/%d %H:%M:%S")

    Returns:
        str: Formatted datetime string in local timezone

    Example:
        >>> from studio.app.common.core.utils.datetime_utils import (
        ...     get_local_datetime_formatted
        ... )
        >>> formatted = get_local_datetime_formatted()
        >>> formatted  # e.g., "2024/01/15 10:30:45" (in local time)
    """
    return datetime.now(tzlocal()).strftime(format_string)


def datetime_from_timestamp(timestamp: float) -> datetime:
    """
    Convert a Unix timestamp to a UTC-aware datetime.

    Args:
        timestamp: Unix timestamp (seconds since epoch)

    Returns:
        datetime: UTC-aware datetime

    Example:
        >>> from studio.app.common.core.utils.datetime_utils import (
        ...     datetime_from_timestamp
        ... )
        >>> dt = datetime_from_timestamp(1705312245.0)
        >>> dt.tzinfo
        datetime.timezone.utc
    """
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def get_local_datetime() -> datetime:
    """
    Get the current local datetime with timezone info.

    Use this for scientific data where the actual local time of the experiment
    matters (e.g., NWB session_start_time). For system operations, prefer
    get_current_datetime() which returns UTC.

    Returns:
        datetime: Current datetime with local timezone

    Example:
        >>> from studio.app.common.core.utils.datetime_utils import get_local_datetime
        >>> now = get_local_datetime()
        >>> now.tzinfo is not None
        True
    """
    return datetime.now(tzlocal())


def format_date_for_display(dt: datetime, format_string: str = "%Y-%m-%d") -> str:
    """
    Format a datetime for user-facing display, appending UTC indicator.

    Use this when displaying dates to users (e.g., subscription dates) to make
    it clear the date is in UTC.

    Args:
        dt: datetime to format (should be UTC-aware)
        format_string: strftime format string (default: "%Y-%m-%d")

    Returns:
        str: Formatted date string with UTC indicator

    Example:
        >>> from studio.app.common.core.utils.datetime_utils import (
        ...     format_date_for_display, datetime_from_timestamp
        ... )
        >>> dt = datetime_from_timestamp(1705312245.0)
        >>> format_date_for_display(dt)
        '2024-01-15 (UTC)'
    """
    return f"{dt.strftime(format_string)} (UTC)"
