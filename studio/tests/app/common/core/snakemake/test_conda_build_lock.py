import fcntl
import os
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from snakemake.deployment.conda import Env
from snakemake.exceptions import WorkflowError

from studio.app.common.core.snakemake import snakemake_executor
from studio.app.dir_path import DIRPATH


@pytest.fixture
def conda_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(DIRPATH, "SNAKEMAKE_CONDA_ENV_DIR", str(tmp_path))
    return tmp_path


def _create(writes_flag=True):
    def create(env, dryrun=False):
        if writes_flag:
            open(f"{env.address}.env_setup_done", "w").close()
        return env.address

    return Mock(side_effect=create)


def test_env_create_is_wrapped():
    assert Env.create is snakemake_executor._env_create_locked


def test_a_built_env_skips_the_lock(conda_dir, monkeypatch):
    env = SimpleNamespace(address=str(conda_dir / "abc_"))
    open(f"{env.address}.env_setup_done", "w").close()
    create = _create()
    monkeypatch.setattr(snakemake_executor, "_env_create", create)

    assert snakemake_executor._env_create_locked(env) == env.address
    create.assert_called_once_with(env, False)
    assert not (conda_dir / ".build.lock").exists()


def test_a_missing_env_is_built_under_the_lock(conda_dir, monkeypatch):
    env = SimpleNamespace(address=str(conda_dir / "abc_"))
    create = _create()
    monkeypatch.setattr(snakemake_executor, "_env_create", create)
    monkeypatch.setattr(snakemake_executor, "BUILD_LOCK_POLL_SECONDS", 0.05)

    with open(conda_dir / ".build.lock", "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        builder = threading.Thread(
            target=snakemake_executor._env_create_locked, args=(env,)
        )
        builder.start()
        time.sleep(0.3)
        assert create.call_count == 0
    builder.join(5)

    create.assert_called_once_with(env, False)


def test_a_lock_held_past_the_deadline_fails_the_run(conda_dir, monkeypatch):
    env = SimpleNamespace(address=str(conda_dir / "abc_"))
    create = _create()
    monkeypatch.setattr(snakemake_executor, "_env_create", create)
    monkeypatch.setattr(snakemake_executor, "BUILD_LOCK_WAIT_SECONDS", 0.2)
    monkeypatch.setattr(snakemake_executor, "BUILD_LOCK_POLL_SECONDS", 0.05)

    with open(conda_dir / ".build.lock", "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        with pytest.raises(WorkflowError, match="Gave up"):
            snakemake_executor._env_create_locked(env)
    create.assert_not_called()


def test_a_build_that_leaves_no_flag_fails(conda_dir, monkeypatch):
    env = SimpleNamespace(address=str(conda_dir / "abc_"))
    monkeypatch.setattr(snakemake_executor, "_env_create", _create(writes_flag=False))

    with pytest.raises(WorkflowError, match="was not built"):
        snakemake_executor._env_create_locked(env)
    assert not os.path.exists(f"{env.address}.env_setup_done")
