import json
import logging
import os
import re

from studio.app.dir_path import DIRPATH

logger = logging.getLogger(__name__)


def get_app_version_from_pyproject() -> str:
    """Extract version from pyproject.toml."""
    # Look for pyproject.toml in parent directories
    pyproject_path = os.path.join(DIRPATH.ROOT_DIR, "pyproject.toml")

    # Read and parse the version from pyproject.toml
    with open(pyproject_path, "r") as f:
        content = f.read()

    # Use regex to extract version
    version_match = re.search(r'version\s*=\s*["\']([^"\']+)["\']', content)
    if version_match:
        version = version_match.group(1)
        return version
    else:
        return "1.0.0"


def _load_build_info() -> dict:
    """Load build metadata written by the Dockerfile at build time."""
    build_info_path = os.path.join(DIRPATH.ROOT_DIR, "BUILD_INFO")
    try:
        with open(build_info_path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.debug(f"Could not load BUILD_INFO: {e}")
        return {}


class Version:
    APP_VERSION = get_app_version_from_pyproject()


class BuildInfo:
    _data = _load_build_info()
    GIT_COMMIT = _data.get("git_commit", "N/A")
    BUILD_TIMESTAMP = _data.get("build_timestamp", "N/A")
