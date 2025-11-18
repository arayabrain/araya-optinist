import os

import yaml
from filelock import FileLock

from studio.app.common.core.utils.filelock_handler import FileLockUtils
from studio.app.common.core.utils.filepath_creater import (
    create_directory,
    join_filepath,
)


def get_env_var(key: str, default: str = None, required: bool = False) -> str:
    """
    Get environment variable with optional validation.

    Args:
        key: Environment variable name
        default: Default value if not set (optional)
        required: If True, raises ValueError when variable is not set
                  and no default provided

    Returns:
        str: Environment variable value or default

    Raises:
        ValueError: If required=True and variable is not set with no default

    Examples:
        >>> get_env_var("BASE_URL", required=True)
        >>> get_env_var("FRONTEND_URL", default="http://localhost:3000")
    """
    value = os.getenv(key, default)
    if required and not value:
        raise ValueError(f"{key} environment variable is not set")
    return value


def get_env_bool(key: str, default: bool = False) -> bool:
    """
    Get boolean environment variable.

    Converts string values to boolean. Accepts: "true", "1", "yes", "on"
    (case-insensitive) as True. All other values are treated as False.

    Args:
        key: Environment variable name
        default: Default boolean value if not set

    Returns:
        bool: Environment variable value as boolean or default

    Examples:
        >>> get_env_bool("USE_FIREBASE_EMAIL", default=True)
        >>> get_env_bool("DEBUG_MODE")
    """
    value = os.getenv(key)
    if value is None:
        return default
    return value.lower() in ("true", "1", "yes", "on")


def differential_deep_merge(d1: dict, d2: dict) -> dict:
    """
    Deep merge only the differences to avoid destroying existing elements
    """
    result = d1.copy()
    for key, value in d2.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = differential_deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class ConfigReader:
    @classmethod
    def read(cls, filepath: str) -> dict:
        config = {}

        if filepath is not None and os.path.exists(filepath):
            with open(filepath) as f:
                config = yaml.safe_load(f)

        return config

    @classmethod
    def read_from_bytes(cls, content: bytes) -> dict:
        config = yaml.safe_load(content)
        return config


class ConfigWriter:
    FILE_LOCK_TIMEOUT = 60

    @classmethod
    def write(cls, dirname: str, filename: str, config: dict, auto_file_lock=True):
        create_directory(dirname)

        config_path = join_filepath([dirname, filename])

        if auto_file_lock:
            # Exclusive control for parallel updates from multiple processes.
            lock_path = FileLockUtils.get_lockfile_path(config_path)
            with FileLock(lock_path, cls.FILE_LOCK_TIMEOUT):
                cls.__write(config_path, config)
        else:
            cls.__write(config_path, config)

    @classmethod
    def __write(cls, config_path: str, config: dict):
        config_tmp_path = f"{config_path}.tmp"

        # First write to a temporary file
        # (a measure to avoid read conflicts due to write delays)
        with open(config_tmp_path, "w") as f:
            yaml.dump(config, f, sort_keys=False)

        # Write to the original file path
        # (write atomically by using os.replace)
        os.replace(config_tmp_path, config_path)
