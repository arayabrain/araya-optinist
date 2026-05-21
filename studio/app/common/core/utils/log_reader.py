import re
from enum import Enum

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.mode import MODE
from studio.app.common.core.utils.file_reader import (
    ContentUnitReader,
    PaginatedFileReader,
)
from studio.app.dir_path import DIRPATH

FRONTEND_LOG_PREFIX = "[FRONTEND]"


class LogLevel(str, Enum):
    ALL = "ALL"
    INFO = "INFO"
    ERROR = "ERROR"
    DEBUG = "DEBUG"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    FRONTEND = "FRONTEND"


class LogRecordReader(ContentUnitReader):
    """Log record reader that treats each log entry as a unit"""

    def __init__(
        self,
        levels: list[LogLevel],
        filter_user_id: str = None,
        **kwargs,
    ) -> None:
        self.filter_frontend = LogLevel.FRONTEND in levels
        non_frontend_levels = [lv for lv in levels if lv != LogLevel.FRONTEND]

        if LogLevel.ALL in non_frontend_levels:
            # ALL excludes DEBUG; DEBUG is only visible via direct CloudWatch access
            self.levels: list[bytes] = [
                level.value.encode()
                for level in LogLevel
                if level not in (LogLevel.ALL, LogLevel.DEBUG, LogLevel.FRONTEND)
            ]
        else:
            self.levels: list[bytes] = [
                level.value.encode() for level in non_frontend_levels
            ]

        # client_id filter (None means no filtering)
        client_id = (
            AppLogger.generate_client_id(filter_user_id) if filter_user_id else None
        )
        self.filter_client_id: bytes = client_id.encode() if client_id else None

        # Timestamp pattern shared between start_pattern and full pattern
        timestamp_pattern = rb"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}"

        # Pattern to detect the start of a log entry (for multiline support)
        self.start_pattern = re.compile(
            rb"(?=^" + timestamp_pattern + rb")", re.MULTILINE
        )

        # Full pattern to parse complete log entries including client_id field
        self.pattern = re.compile(
            rb"^(?P<asctime>" + timestamp_pattern + rb") "
            rb"(?:\x1b\[\d+m)?(?P<levelprefix>\w+)(?:\x1b\[0m)?:?\s+"
            rb"\[(?P<name>[^\]]+)\] "
            rb"\(pid:(?P<process>\w+)\) "
            rb"\(task:(?P<ecs_task_id>[^\)]*)\) "
            rb"\(client:(?P<client_id>[^\)]*)\) "
            rb"(?P<funcName>\w+)\(\):(?P<lineno>\d+) - "
            rb"(?P<message>.*)",
            re.DOTALL,
        )
        self.exclude_pattern: list[bytes] = [b"GET /logs", b"OPTIONS /logs"]

    def is_unit_start(self, line: bytes) -> bool:
        return bool(self.start_pattern.match(line))

    def parse(self, content: bytes) -> dict:
        if not content:
            return {"raw": b"", "parsed": False}

        match = self.pattern.match(content)
        if not match:
            return {"raw": content, "parsed": False}

        components = match.groupdict()

        return {
            "timestamp": components["asctime"],
            "level": components["levelprefix"],
            "name": components["name"],
            "client_id": components["client_id"],
            "function": components["funcName"],
            "line": int(components["lineno"]),
            "message": components["message"],
            "raw": content,
            "parsed": True,
        }

    def validate(self, content: bytes) -> bool:
        if any([pattern in content for pattern in self.exclude_pattern]):
            return False

        unit_dict = self.parse(content)
        if not unit_dict["parsed"]:
            return False

        has_frontend_prefix = unit_dict.get("message", b"").startswith(
            FRONTEND_LOG_PREFIX.encode()
        )

        # When FRONTEND filter is active alone, only show frontend lines
        if self.filter_frontend and not self.levels:
            if not has_frontend_prefix:
                return False
        # When FRONTEND + severity filters active, match either
        elif self.filter_frontend and self.levels:
            if unit_dict["level"] not in self.levels and not has_frontend_prefix:
                return False
        # Standard severity filter (no FRONTEND filter)
        elif self.levels:
            if unit_dict["level"] not in self.levels:
                return False

        # Filter by client_id
        if MODE.IS_MULTIUSER:
            if unit_dict["client_id"] != self.filter_client_id:
                return False
        else:
            # NOTE: In standalone mode, filtering by client_id is not performed.
            pass

        return True


class LogReader(PaginatedFileReader):
    def __init__(
        self,
        file_path=DIRPATH.LOG_FILE_PATH,
        levels: list[LogLevel] = [],
        filter_user_id: str = None,
        **kwargs,
    ):
        super().__init__(file_path, **kwargs)
        self.file_path = file_path
        self.unit_reader = LogRecordReader(levels=levels, filter_user_id=filter_user_id)
