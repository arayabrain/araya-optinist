import hashlib
import os
import shutil
from typing import Optional

import pytest

from studio.app.common.core.snakemake.smk_utils import SmkInternalUtils
from studio.app.dir_path import DIRPATH

conda_name = "suite2p"

conda_env_yaml_path = f"{DIRPATH.DATA_DIR}/conda_envs/{conda_name}/{conda_name}.yaml"

"""
Address of the conda env fixture that used to be committed under
`studio/test_data/conda_envs`, and the hash snakemake gave it when it built the
env inside the test container.
Snakemake hashes the absolute path of the env dir, so this pair is only
reproducible by passing the path explicitly -- see `test_conda_env_hash`.

If `suite2p.yaml` changes, `container_env_hash` must be regenerated from
snakemake's own output, never from `_get_conda_env_hash`: the constant is an
independent oracle, and recomputing it with the function under test turns the
assertion into a tautology.
"""
container_env_rootpath = f"/app/studio/test_data/conda_envs/{conda_name}"
container_env_hash = "361e81b39710026dc0021c8cf18e6fad"


def _marker_path(env_dirpath: str, layout: str) -> str:
    """
    The two `env_setup_done` layouts `verify_conda_env_exists` accepts. The name
    is snakemake's, not this test's, and only its existence is ever read.
    """
    return {
        # What snakemake writes today:
        # `snakemake.deployment.conda.get_env_setup_done_flag_file`
        "sibling": f"{env_dirpath}.env_setup_done",
        # Older snakemake wrote the flag inside the env dir. This is the layout
        # the fixture formerly committed under `studio/test_data` used.
        "inside": f"{env_dirpath}/env_setup_done",
    }[layout]


def _build_conda_env_fixture(
    env_rootpath: str, marker_layout: Optional[str] = "sibling"
) -> None:
    """
    Reproduce what snakemake leaves behind for a conda env: a `<md5>_` directory
    -- the md5 covering the realpath of the env dir followed by the env file's
    bytes, the trailing `_` snakemake's own suffix -- plus the empty
    `env_setup_done` flag it writes once creation finished.

    `marker_layout` picks which flag layout to write; `None` writes none, the
    state of an env snakemake has not finished creating.
    """
    os.makedirs(env_rootpath, exist_ok=True)
    shutil.copy(conda_env_yaml_path, f"{env_rootpath}/{conda_name}.yaml")

    md5hash = hashlib.md5()
    md5hash.update(os.path.realpath(env_rootpath).encode())
    with open(f"{env_rootpath}/{conda_name}.yaml", "rb") as f:
        md5hash.update(f.read())

    env_dirpath = f"{env_rootpath}/{md5hash.hexdigest()}_"
    os.makedirs(env_dirpath, exist_ok=True)
    if marker_layout is not None:
        open(_marker_path(env_dirpath, marker_layout), "w").close()


def test_conda_env_hash():
    """
    `SmkInternalUtils` ports snakemake's hashing rather than calling into it, so
    pin the port against a hash snakemake itself produced.
    """
    env_hash = SmkInternalUtils._get_conda_env_hash(
        conda_env_yaml_path, container_env_rootpath
    )

    assert env_hash == container_env_hash, f"Invalid conda env hash: {env_hash}"


@pytest.mark.parametrize("marker_layout", ["sibling", "inside"])
def test_SmkInternalUtils(tmp_path, marker_layout):
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

    _build_conda_env_fixture(conda_env_rootpath, marker_layout=marker_layout)

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

    _build_conda_env_fixture(conda_env_rootpath, marker_layout=None)

    conda_env_exists = SmkInternalUtils.verify_conda_env_exists(
        conda_name, conda_env_rootpath, conda_env_filepath
    )

    assert not conda_env_exists, f"Invalid verify_conda_env_exists result: {conda_name}"
