import hashlib
import logging
import logging.config
import os
import platform
import traceback
from contextvars import ContextVar
from datetime import datetime
from typing import Optional

import yaml

from studio.app.common.core.mode import MODE
from studio.app.dir_path import DIRPATH

LOGGING_CLIENT_ID_KEY = "client_id"

# Context variable for storing client_id per request/context
_client_id_context: ContextVar[Optional[str]] = ContextVar(
    LOGGING_CLIENT_ID_KEY, default=None
)


class AppLogger:
    """
    Generic Application Logger
    """

    LOGGER_NAME = "optinist"

    class ClientIdFilter(logging.Filter):
        """
        Logging filter to inject client_id from context into log records
        """

        # Alternate text to log if client_id is not obtained
        NO_CLIENT_ID_DEFAULT_VALUE = "default"

        def filter(self, record):
            # Get client_id from context, default to "none" if not set
            client_id = _client_id_context.get()
            record.client_id = (
                client_id
                if client_id is not None
                else __class__.NO_CLIENT_ID_DEFAULT_VALUE
            )
            return True

    @classmethod
    def init_logger(cls):
        """
        Note #1.
            At the time of starting to use this Logger,
            the logging initialization process has already been performed
            at the following location,
            so no explicit initialization process is required.

            - logger initialization location
              - Web App ... studio.__main_unit__
              - Batch App ... studio.app.optinist.core.expdb.batch_runner
                (optinist-for-server)

        Note #2.
            However, only in the case of the snakemake process,
            the initialization process is required because it is a separate process.
        """

        # read logging config
        logging_config = cls.get_logging_config()

        # create log output directory (if none exists)
        log_file = (
            logging_config.get("handlers", {}).get("rotating_file", {}).get("filename")
        )
        if log_file:
            log_dir = os.path.dirname(log_file)
            if not os.path.isdir(log_dir):
                os.makedirs(log_dir)

        # set logging config
        logging.config.dictConfig(logging_config)

    @staticmethod
    def get_logging_config() -> dict:
        logging_config_file = (
            f"{DIRPATH.CONFIG_DIR}/logging.yaml"
            if MODE.IS_STANDALONE
            else f"{DIRPATH.CONFIG_DIR}/logging.multiuser.yaml"
        )

        logging_config = None

        with open(logging_config_file) as file:
            logging_config = yaml.load(file.read(), yaml.FullLoader)

        logging_handers = logging_config.get("handlers", {})

        # Switch rotating_file depending on platform
        if __class__.is_native_windows():
            # ATTENTION:
            # On the Windows Native Platform, "rotating_file_concurrency"
            # is currently not supported because pywin32 is required to use
            # concurrent_log_handler. (which is not installed in the conda env).
            pass
        else:
            if ("rotating_file" in logging_handers) and (
                "rotating_file_concurrency"
            ) in logging_handers:
                logging_config["handlers"]["rotating_file"] = logging_config[
                    "handlers"
                ]["rotating_file_concurrency"]

        # Delete unnecessary items
        if "rotating_file_concurrency" in logging_handers:
            del logging_config["handlers"]["rotating_file_concurrency"]

        # Adjust log file path (if none exists)
        log_file = logging_handers.get("rotating_file", {}).get("filename")
        if log_file:
            log_file = f"{DIRPATH.DATA_DIR}/{log_file}"
            logging_config["handlers"]["rotating_file"]["filename"] = log_file

        # Add ClientIdFilter configuration to filters section
        CLIENT_ID_FILTER_NAME = "client_id_filter"
        if "filters" not in logging_config:
            logging_config["filters"] = {}
        logging_config["filters"][CLIENT_ID_FILTER_NAME] = {
            "()": "studio.app.common.core.logger.AppLogger.ClientIdFilter"
        }

        # Add client_id_filter to all handlers
        for handler_config in logging_config.get("handlers", {}).values():
            if "filters" not in handler_config:
                handler_config["filters"] = []
            if CLIENT_ID_FILTER_NAME not in handler_config["filters"]:
                handler_config["filters"].append(CLIENT_ID_FILTER_NAME)

        return logging_config

    @staticmethod
    def get_logger() -> logging.Logger:
        logger = logging.getLogger(__class__.LOGGER_NAME)

        # If before initialization, call init
        if not logger.handlers:
            __class__.init_logger()

        return logger

    @staticmethod
    def set_client_id(client_id: Optional[str]):
        """
        Set client_id in the current context for logging
        """
        _client_id_context.set(client_id)

    @staticmethod
    def get_client_id() -> Optional[str]:
        """
        Get client_id from the current context
        """
        return _client_id_context.get()

    @staticmethod
    def generate_client_id(uid: str, auto_refresh: bool = False) -> str:
        """
        Generate a client_id for logging based on the specified uid.

        Args:
            uid: User ID to generate client_id from
            auto_refresh: If true, refresh id at period interval (weekly)

        Returns:
            Generated client_id (16 character hash)
        """
        if not uid:
            return uid

        if auto_refresh:
            datetime_str = datetime.now().strftime(
                "%Y-%m-%d-%w"
            )  # Refresh by period (weekly)
            hash_source = f"{uid}-{datetime_str}"
        else:
            hash_source = uid

        client_id = hashlib.md5(hash_source.encode()).hexdigest()
        client_id = client_id[0:16]

        return client_id

    @staticmethod
    def format_exc_traceback(e: Exception):
        return "{}: {}\n{}".format(type(e), e, traceback.format_exc())

    @staticmethod
    def is_native_windows():
        if platform.system() != "Windows":
            return False

        # Check WSL Platform
        if "WSL_DISTRO_NAME" in os.environ or "WSL_INTEROP" in os.environ:
            return False

        return True
