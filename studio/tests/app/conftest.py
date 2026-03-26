import shutil
from typing import Generator

import pytest
from fastapi.testclient import TestClient

# Set MODE before importing __main_unit__ to prevent scheduler initialization
# and DB access during tests. MODE is a singleton that reads .env at import time,
# so it must be overridden before any module-level code in __main_unit__ runs.
from studio.app.common.core.mode import MODE

MODE.IS_TEST = True
MODE.IS_STANDALONE = True

from studio.__main_unit__ import app, skip_dependencies  # noqa: E402
from studio.app.common.core.auth.auth_dependencies import (
    get_admin_user,
    get_current_user,
    get_current_user_with_dataview_outputs_check,
    get_outputs_remote_bucket_name,
)
from studio.app.common.core.workspace.workspace_dependencies import (
    is_workspace_available,
    is_workspace_owner,
)
from studio.app.dir_path import DIRPATH


def skip_outputs_remote_bucket_name():
    return ""


@pytest.fixture(scope="session", autouse=True)
def session_fixture():
    app.dependency_overrides[get_admin_user] = skip_dependencies
    app.dependency_overrides[get_current_user] = skip_dependencies
    app.dependency_overrides[
        get_current_user_with_dataview_outputs_check
    ] = skip_dependencies
    app.dependency_overrides[
        get_outputs_remote_bucket_name
    ] = skip_outputs_remote_bucket_name
    app.dependency_overrides[is_workspace_available] = skip_dependencies
    app.dependency_overrides[is_workspace_owner] = skip_dependencies

    yield

    shutil.rmtree(f"{DIRPATH.DATA_DIR}/output")


@pytest.fixture(scope="module")
def client() -> Generator:
    with TestClient(app) as c:
        yield c
