# flake8: noqa
# Exclude from lint for the following reason
# This file is executed by snakemake and cause the following lint errors
# - E402: sys.path.append is required to import optinist modules
# - F821: do not import snakemake
import sys
from os.path import abspath, dirname

ROOT_DIRPATH = dirname(dirname(dirname(dirname(dirname(dirname(abspath(__file__)))))))
sys.path.append(ROOT_DIRPATH)

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.logger_context_helpers import (
    init_client_id_from_snakemake_config,
)

logger = AppLogger.get_logger()


def main():
    try:
        import json

        from studio.app.common.core.rules.runner import Runner
        from studio.app.common.core.snakemake.snakemake_reader import RuleConfigReader
        from studio.app.common.core.utils.filepath_creater import join_filepath
        from studio.app.dir_path import DIRPATH

        # Initialize client_id from snakemake config
        init_client_id_from_snakemake_config(snakemake.config)

        # INVESTIGATION: Debug logging AFTER init_client_id_from_snakemake_config
        logger.debug("INVESTIGATION: In batch container func.py main()")
        logger.debug(
            f"INVESTIGATION: After init, AppLogger.get_client_id(): "
            f"{AppLogger.get_client_id()}"
        )
        logger.debug(
            f"INVESTIGATION: snakemake.config keys: {list(snakemake.config.keys())}"
        )
        logger.debug(
            f"INVESTIGATION: client_id in snakemake.config: "
            f"{snakemake.config.get('client_id', 'NOT FOUND')}"
        )
        try:
            logger.debug(
                f"INVESTIGATION: Full snakemake.config dump: "
                f"{json.dumps(dict(snakemake.config), indent=2, default=str)}"
            )
        except Exception as e:
            logger.debug(f"INVESTIGATION: Could not dump snakemake.config: {e}")

        last_output = [
            join_filepath([DIRPATH.OUTPUT_DIR, x])
            for x in snakemake.config["last_output"]
        ]

        rule_config = RuleConfigReader.read(snakemake.params.name)

        rule_config.input = snakemake.input
        rule_config.output = snakemake.output[0]
        run_script_path = sys.argv[0]

        Runner.run(rule_config, last_output, run_script_path)

    except Exception as e:
        logger.error(AppLogger.format_exc_traceback(e))


if __name__ == "__main__":
    main()
