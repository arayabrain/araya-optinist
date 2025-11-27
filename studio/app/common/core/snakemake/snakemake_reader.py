import os

from studio.app.common.core.cloud_batch.config_handler import get_batch_config_path
from studio.app.common.core.experiment.experiment import ExptOutputPathIds
from studio.app.common.core.mode import MODE
from studio.app.common.core.snakemake.smk import Rule, SmkParam
from studio.app.common.core.utils.config_handler import ConfigReader
from studio.app.common.core.utils.filepath_creater import join_filepath
from studio.app.dir_path import DIRPATH


class RuleConfigReader:
    @classmethod
    def read(cls, rule):
        return Rule(
            input=rule["input"],
            return_arg=rule["return_arg"],
            params=rule["params"],
            output=rule["output"],
            type=rule["type"],
            nwbfile=rule["nwbfile"],
            hdf5Path=rule["hdf5Path"],
            matPath=rule["matPath"],
            path=rule["path"],
        )


class SmkParamReader:
    @classmethod
    def read(cls, params):
        return SmkParam(
            use_conda=params["use_conda"],
            cores=params["cores"],
            forceall=params["forceall"],
            forcerun=params["forcerun"] if "forcerun" in params else [],
            # forcetargets=params["forcetargets"],
            # lock=params["lock"],
        )


class SmkConfigReader:
    @classmethod
    def get_config_yaml_path(cls, workspace_id: str, unique_id: str) -> str:
        # Check if running in batch mode
        if MODE.IN_SNAKEMAKE_BATCH:
            return get_batch_config_path()
        else:
            # Local mode: use workspace-specific path
            path = join_filepath(
                [
                    DIRPATH.OUTPUT_DIR,
                    workspace_id,
                    unique_id,
                    DIRPATH.SNAKEMAKE_CONFIG_YML,
                ]
            )
            return path

    @classmethod
    def read(cls, workspace_id: str, unique_id: str) -> dict:
        filepath = cls.get_config_yaml_path(workspace_id, unique_id)
        config = ConfigReader.read(filepath)

        assert config, f"Invalid config yaml file: [{filepath}] [{config}]"

        return config

    @classmethod
    def read_from_path(cls, filepath: str) -> dict:
        # In batch mode, ignore the filepath and use batch config location
        if MODE.IN_SNAKEMAKE_BATCH:
            # Extract IDs for logging but use batch config path
            ids = ExptOutputPathIds(os.path.dirname(filepath))
            return cls.read(ids.workspace_id, ids.unique_id)
        else:
            # Local mode: extract IDs from path
            ids = ExptOutputPathIds(os.path.dirname(filepath))
            return cls.read(ids.workspace_id, ids.unique_id)
