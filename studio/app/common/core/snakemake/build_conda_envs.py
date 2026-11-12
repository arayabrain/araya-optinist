import sys
import tempfile
from pathlib import Path

from snakemake.api import OutputSettings, SnakemakeApi

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.snakemake.smk import SmkParam
from studio.app.common.core.snakemake.smk_utils import SmkInternalUtils, SmkUtils
from studio.app.common.core.snakemake.snakemake_executor import _dag
from studio.app.wrappers import wrapper_dict

logger = AppLogger.get_logger()


def conda_env_files(node=wrapper_dict) -> dict:
    if "function" in node:
        name = node.get("conda_name")
        return {name: SmkUtils.get_conda_env_filepath(name)} if name else {}
    return {k: v for child in node.values() for k, v in conda_env_files(child).items()}


def missing_envs(env_files: dict) -> dict:
    return {
        name: path
        for name, path in env_files.items()
        if not SmkInternalUtils.verify_conda_env_exists(name, conda_env_filepath=path)
    }


def create_env(env_file: str):
    with tempfile.TemporaryDirectory() as workdir:
        snakefile = Path(workdir, "Snakefile")
        snakefile.write_text(
            f"rule env:\n    output: 'env'\n    conda: {env_file!r}\n"
            "    shell: 'touch {output}'\n"
        )
        params = SmkParam(use_conda=True, cores=1, forceall=False)
        with SnakemakeApi(OutputSettings()) as snakemake_api:
            _dag(snakemake_api, workdir, params, str(snakefile)).conda_create_envs()


def main() -> int:
    missing = missing_envs(conda_env_files())
    logger.info(f"Conda envs to build: {sorted(missing) or 'none'}")
    for name, path in missing.items():
        try:
            create_env(path)
        except Exception:
            logger.exception(f"Conda env {name} failed to build")
    missing = missing_envs(missing)
    if missing:
        logger.error(f"Conda envs not built: {sorted(missing)}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
