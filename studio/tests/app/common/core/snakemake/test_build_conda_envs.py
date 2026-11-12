import os

import pytest

from studio.app.common.core.snakemake import (
    build_conda_envs,
    smk_utils,
    snakemake_executor,
)
from studio.app.common.core.snakemake.smk_utils import SmkInternalUtils
from studio.app.dir_path import DIRPATH


@pytest.fixture
def conda_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(DIRPATH, "SNAKEMAKE_CONDA_ENV_DIR", str(tmp_path))
    monkeypatch.setattr(smk_utils, "_global_smk_conda_env_paths_cache", {})
    return tmp_path


def _flag(env_file):
    address = SmkInternalUtils._get_conda_env_address(
        env_file, DIRPATH.SNAKEMAKE_CONDA_ENV_DIR
    )
    return f"{address}.env_setup_done"


def test_every_conda_wrapper_has_an_env_file():
    env_files = build_conda_envs.conda_env_files()
    assert {"caiman", "suite2p", "lccd"} <= set(env_files)
    assert all(os.path.isfile(path) for path in env_files.values())


def test_the_build_creates_the_env_a_run_looks_for(conda_dir, monkeypatch):
    env_file = build_conda_envs.conda_env_files()["lccd"]
    created = []

    def fake_create(env, dryrun=False):
        created.append(env.address)
        open(f"{env.address}.env_setup_done", "w").close()
        return env.address

    monkeypatch.setattr(snakemake_executor, "_env_create", fake_create)
    cwd = os.getcwd()

    build_conda_envs.create_env(env_file)

    assert [f"{a}.env_setup_done" for a in created] == [_flag(env_file)]
    assert os.getcwd() == cwd


def test_only_missing_envs_are_built_and_a_failure_is_reported(conda_dir, monkeypatch):
    env_files = build_conda_envs.conda_env_files()
    open(_flag(env_files["caiman"]), "w").close()
    built = []

    def create_env(path):
        built.append(path)
        if path == env_files["lccd"]:
            raise RuntimeError("solver failed")
        open(_flag(path), "w").close()

    monkeypatch.setattr(build_conda_envs, "create_env", create_env)

    assert build_conda_envs.main() == 1
    assert env_files["caiman"] not in built
    assert sorted(built) == sorted(set(env_files.values()) - {env_files["caiman"]})
    assert build_conda_envs.main() == 1
    assert built[-1] == env_files["lccd"]


def test_nothing_to_build_exits_zero(conda_dir, monkeypatch):
    for path in build_conda_envs.conda_env_files().values():
        open(_flag(path), "w").close()
    monkeypatch.setattr(build_conda_envs, "create_env", pytest.fail)

    assert build_conda_envs.main() == 0
