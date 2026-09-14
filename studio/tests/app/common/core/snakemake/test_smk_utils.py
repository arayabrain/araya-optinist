import hashlib
import os
import shutil

from studio.app.common.core.snakemake.smk_utils import SmkInternalUtils
from studio.app.dir_path import DIRPATH

conda_name = "suite2p"

conda_env_yaml_path = f"{DIRPATH.DATA_DIR}/conda_envs/{conda_name}/{conda_name}.yaml"

"""
Address of the conda env fixture committed under `studio/test_data/conda_envs`,
and the hash snakemake gave it when it built the env inside the test container.
Snakemake hashes the absolute path of the env dir, so this pair is only
reproducible by passing the path explicitly -- see `test_conda_env_hash`.
"""
container_env_rootpath = f"/app/studio/test_data/conda_envs/{conda_name}"
container_env_hash = "361e81b39710026dc0021c8cf18e6fad"


def _build_conda_env_fixture(env_rootpath: str) -> None:
    """
    Reproduce what snakemake leaves behind for a successfully created conda env:
    a `<md5>_` directory holding an `env_setup_done` marker, where the md5 covers
    the realpath of the env dir followed by the env file's bytes.
    """
    os.makedirs(env_rootpath, exist_ok=True)
    shutil.copy(conda_env_yaml_path, f"{env_rootpath}/{conda_name}.yaml")

    md5hash = hashlib.md5()
    md5hash.update(os.path.realpath(env_rootpath).encode())
    with open(f"{env_rootpath}/{conda_name}.yaml", "rb") as f:
        md5hash.update(f.read())

    env_dirpath = f"{env_rootpath}/{md5hash.hexdigest()}_"
    os.makedirs(env_dirpath, exist_ok=True)
    open(f"{env_dirpath}/env_setup_done", "w").close()


def test_conda_env_hash():
    """
    `SmkInternalUtils` ports snakemake's hashing rather than calling into it, so
    pin the port against a hash snakemake itself produced.
    """
    env_hash = SmkInternalUtils._get_conda_env_hash(
        conda_env_yaml_path, container_env_rootpath
    )

    assert env_hash == container_env_hash, f"Invalid conda env hash: {env_hash}"


def test_SmkInternalUtils(tmp_path):
    """
    The committed fixture is addressed by the container path, so it only resolves
    inside Docker. Build an equivalent env for the current path instead, and keep
    the test runnable from any checkout.

    `tmp_path` is pytest's own fixture: a new empty directory per test, injected
    by argument name. Two properties matter here.
      - Its path differs every run, so the env fixture cannot be tied to one
        checkout -- which is the failure being fixed.
      - It is unique per test, so the env file path stays unique too. That path
        keys `SmkInternalUtils`'s module-level address cache, which would
        otherwise carry one test's result into the next.
    """
    conda_env_rootpath = f"{tmp_path}/conda_envs/{conda_name}"
    conda_env_filepath = f"{conda_env_rootpath}/{conda_name}.yaml"

    _build_conda_env_fixture(conda_env_rootpath)

    conda_env_exists = SmkInternalUtils.verify_conda_env_exists(
        conda_name, conda_env_rootpath, conda_env_filepath
    )

    assert conda_env_exists, f"Invalid verify_conda_env_exists result: {conda_name}"


def test_SmkInternalUtils_without_created_env(tmp_path):
    """
    An env dir that snakemake has not finished creating has no `env_setup_done`.

    Builds its own env under `tmp_path`, for the reasons in `test_SmkInternalUtils`.
    """
    conda_env_rootpath = f"{tmp_path}/conda_envs/{conda_name}"
    conda_env_filepath = f"{conda_env_rootpath}/{conda_name}.yaml"

    _build_conda_env_fixture(conda_env_rootpath)
    for entry in os.listdir(conda_env_rootpath):
        marker = f"{conda_env_rootpath}/{entry}/env_setup_done"
        if os.path.exists(marker):
            os.remove(marker)

    conda_env_exists = SmkInternalUtils.verify_conda_env_exists(
        conda_name, conda_env_rootpath, conda_env_filepath
    )

    assert not conda_env_exists, f"Invalid verify_conda_env_exists result: {conda_name}"
