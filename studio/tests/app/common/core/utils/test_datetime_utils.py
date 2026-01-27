"""Tests for datetime_utils module.

Tests cover:
- UTC datetime functions
- User timezone handling (valid and invalid)
- Timestamp conversion
- Formatted output
"""

from datetime import datetime, timezone

from studio.app.common.core.utils.datetime_utils import (
    TIMEZONE_KEY,
    TIMEZONE_UTC,
    TZ_UTC,
    datetime_from_timestamp,
    format_date_for_display,
    get_current_datetime,
    get_current_datetime_formatted,
    get_current_timestamp,
    get_datetime_for_timezone,
    get_datetime_for_timezone_formatted,
)


class TestUTCDatetimeFunctions:
    """Tests for UTC-based datetime functions."""

    def test_get_current_datetime_returns_utc(self):
        """get_current_datetime should return a UTC-aware datetime."""
        dt = get_current_datetime()
        assert dt.tzinfo == timezone.utc

    def test_get_current_datetime_is_recent(self):
        """get_current_datetime should return a time close to now."""
        before = datetime.now(timezone.utc)
        dt = get_current_datetime()
        after = datetime.now(timezone.utc)
        assert before <= dt <= after

    def test_get_current_timestamp_returns_float(self):
        """get_current_timestamp should return a float timestamp."""
        ts = get_current_timestamp()
        assert isinstance(ts, float)
        assert ts > 0

    def test_get_current_datetime_formatted_default_format(self):
        """get_current_datetime_formatted should use default format."""
        formatted = get_current_datetime_formatted()
        # Format: "2024/01/15 10:30:45"
        parts = formatted.split()
        assert len(parts) == 2
        assert "/" in parts[0]  # Date part
        assert ":" in parts[1]  # Time part

    def test_get_current_datetime_formatted_custom_format(self):
        """get_current_datetime_formatted should accept custom format."""
        formatted = get_current_datetime_formatted("%Y-%m-%d")
        # Format: "2024-01-15"
        assert "-" in formatted
        assert "/" not in formatted


class TestTimestampConversion:
    """Tests for timestamp conversion functions."""

    def test_datetime_from_timestamp_returns_utc(self):
        """datetime_from_timestamp should return a UTC-aware datetime."""
        # Unix timestamp for 2024-01-15 10:30:45 UTC
        timestamp = 1705314645.0
        dt = datetime_from_timestamp(timestamp)
        assert dt.tzinfo == timezone.utc

    def test_datetime_from_timestamp_correct_value(self):
        """datetime_from_timestamp should convert correctly."""
        timestamp = 1705314645.0
        dt = datetime_from_timestamp(timestamp)
        expected = datetime(2024, 1, 15, 10, 30, 45, tzinfo=timezone.utc)
        assert dt == expected

    def test_datetime_from_timestamp_roundtrip(self):
        """Converting datetime to timestamp and back should be consistent."""
        original = get_current_datetime()
        timestamp = original.timestamp()
        recovered = datetime_from_timestamp(timestamp)
        # Allow for microsecond rounding
        assert abs((recovered - original).total_seconds()) < 0.001


class TestUserTimezoneHandling:
    """Tests for user timezone functions (for scientific data)."""

    def test_get_datetime_for_timezone_none_returns_utc(self):
        """get_datetime_for_timezone(None) should return UTC datetime."""
        dt = get_datetime_for_timezone(None)
        assert dt.tzinfo == timezone.utc

    def test_get_datetime_for_timezone_empty_string_returns_utc(self):
        """get_datetime_for_timezone('') should return UTC datetime."""
        dt = get_datetime_for_timezone("")
        assert dt.tzinfo == timezone.utc

    def test_get_datetime_for_timezone_valid_timezone(self):
        """get_datetime_for_timezone should handle valid IANA timezones."""
        dt = get_datetime_for_timezone("America/New_York")
        assert dt.tzinfo is not None
        assert str(dt.tzinfo) == "America/New_York"

    def test_get_datetime_for_timezone_utc_string(self):
        """get_datetime_for_timezone('UTC') should work."""
        dt = get_datetime_for_timezone("UTC")
        assert dt.tzinfo is not None
        assert str(dt.tzinfo) == "UTC"

    def test_get_datetime_for_timezone_invalid_falls_back_to_utc(self):
        """get_datetime_for_timezone should fall back to UTC for invalid timezone."""
        dt = get_datetime_for_timezone("Invalid/Timezone")
        assert dt.tzinfo == timezone.utc

    def test_get_datetime_for_timezone_invalid_logs_warning(self, caplog):
        """get_datetime_for_timezone should log warning for invalid timezone."""
        get_datetime_for_timezone("NotA/RealTimezone")
        assert "Invalid timezone" in caplog.text
        assert "NotA/RealTimezone" in caplog.text
        assert TIMEZONE_UTC in caplog.text

    def test_get_datetime_for_timezone_formatted_none_returns_utc_time(self):
        """get_datetime_for_timezone_formatted(None) should format UTC time."""
        formatted = get_datetime_for_timezone_formatted(None)
        assert isinstance(formatted, str)
        assert "/" in formatted

    def test_get_datetime_for_timezone_formatted_valid_timezone(self):
        """get_datetime_for_timezone_formatted should format in user's timezone."""
        formatted = get_datetime_for_timezone_formatted(
            "America/New_York", "%Y-%m-%d %H:%M:%S"
        )
        assert isinstance(formatted, str)
        assert "-" in formatted

    def test_get_datetime_for_timezone_formatted_custom_format(self):
        """get_datetime_for_timezone_formatted should accept custom format."""
        formatted = get_datetime_for_timezone_formatted("UTC", "%Y-%m-%d")
        # Should only have date, no time
        assert " " not in formatted
        assert formatted.count("-") == 2


class TestDSTHandling:
    """Tests for Daylight Saving Time edge cases."""

    def test_timezone_handles_dst_timezone(self):
        """Timezones with DST should be handled correctly."""
        # America/New_York has DST
        dt = get_datetime_for_timezone("America/New_York")
        assert dt.tzinfo is not None
        # ZoneInfo handles DST automatically

    def test_timezone_handles_non_dst_timezone(self):
        """Timezones without DST should be handled correctly."""
        # Asia/Tokyo does not have DST
        dt = get_datetime_for_timezone("Asia/Tokyo")
        assert dt.tzinfo is not None
        assert str(dt.tzinfo) == "Asia/Tokyo"


class TestDisplayFormatting:
    """Tests for display formatting functions."""

    def test_format_date_for_display_default_format(self):
        """format_date_for_display should use default format with UTC indicator."""
        dt = datetime(2024, 1, 15, 10, 30, 45, tzinfo=timezone.utc)
        result = format_date_for_display(dt)
        assert result == "2024-01-15 (UTC)"

    def test_format_date_for_display_custom_format(self):
        """format_date_for_display should accept custom format."""
        dt = datetime(2024, 1, 15, 10, 30, 45, tzinfo=timezone.utc)
        result = format_date_for_display(dt, "%Y/%m/%d")
        assert result == "2024/01/15 (UTC)"


class TestConstants:
    """Tests for module constants."""

    def test_timezone_utc_constant(self):
        """TIMEZONE_UTC should be 'UTC'."""
        assert TIMEZONE_UTC == "UTC"

    def test_timezone_key_constant(self):
        """TIMEZONE_KEY should be 'timezone'."""
        assert TIMEZONE_KEY == "timezone"

    def test_tz_utc_constant(self):
        """TZ_UTC should be timezone.utc."""
        assert TZ_UTC == timezone.utc
