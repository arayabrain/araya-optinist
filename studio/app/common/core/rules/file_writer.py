import os
import shutil
from typing import List, Union

import h5py

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.mode import MODE
from studio.app.common.core.snakemake.smk import Rule
from studio.app.common.dataclass import CsvData, ImageData, TimeSeriesData
from studio.app.const import FILETYPE
from studio.app.dir_path import DIRPATH
from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.dataclass.iscell import IscellData
from studio.app.optinist.dataclass.microscope import MicroscopeData
from studio.app.optinist.routers.mat import MatGetter

logger = AppLogger.get_logger()


class FileWriter:
    @classmethod
    def _resolve_snakemake_storage_path(
        cls, input_path: Union[str, List[str], tuple]
    ) -> Union[str, List[str]]:
        """
        Resolve Snakemake storage paths to permanent locations.

        When running in AWS Batch with S3 storage, Snakemake downloads files to
        .snakemake/storage/s3/... and cleans them up after each job. This method
        copies those files to permanent locations so downstream jobs can access
        them.

        Args:
            input_path: Single path or list of paths (may be in Snakemake storage)

        Returns:
            Permanent path(s) - copied from Snakemake storage or original path

        Raises:
            ValueError: If workspace_id cannot be extracted from storage path
        """
        # Early exit if not running in batch mode
        # No need to copy files from Snakemake storage in non-batch contexts
        if not MODE.IN_SNAKEMAKE_BATCH:
            return input_path

        # Handle list/tuple by recursing on each element
        if isinstance(input_path, (list, tuple)):
            resolved = [cls._resolve_snakemake_storage_path(p) for p in input_path]
            return resolved if isinstance(input_path, list) else tuple(resolved)

        # Now handle single path case
        if not isinstance(input_path, str) or ".snakemake/storage" not in input_path:
            return input_path

        # Extract filename and workspace_id
        filename = os.path.basename(input_path)
        path_parts = input_path.split("/")

        # Find the workspace_id (should be after 'input' directory)
        try:
            input_idx = path_parts.index("input")
            workspace_id = path_parts[input_idx + 1]
        except (ValueError, IndexError):
            raise ValueError(
                f"Cannot extract workspace_id from Snakemake storage path: "
                f"{input_path}. Expected format: .../input/WORKSPACE_ID/filename"
            )

        # Create permanent directory and copy file
        permanent_dir = os.path.join(DIRPATH.INPUT_DIR, workspace_id)
        os.makedirs(permanent_dir, exist_ok=True)
        permanent_path = os.path.join(permanent_dir, filename)

        # Copy file if it doesn't already exist at permanent location
        if not os.path.exists(permanent_path):
            logger.info(
                "Copying file from Snakemake storage to permanent location: "
                f"{input_path} -> {permanent_path}"
            )
            shutil.copy2(input_path, permanent_path)
        else:
            logger.info(
                "File already exists at permanent location, skipping copy: "
                f"{permanent_path}"
            )

        return permanent_path

    @classmethod
    def csv(cls, rule_config: Rule, nodeType):
        input_path = cls._resolve_snakemake_storage_path(rule_config.input)

        info = {rule_config.return_arg: CsvData(input_path, rule_config.params, "")}
        nwbfile = rule_config.nwbfile

        if nodeType == FILETYPE.CSV:
            if NWBDATASET.TIMESERIES not in nwbfile:
                nwbfile[NWBDATASET.TIMESERIES] = {}
            nwbfile[NWBDATASET.TIMESERIES][rule_config.return_arg] = info[
                rule_config.return_arg
            ]
        elif nodeType == FILETYPE.BEHAVIOR:
            if NWBDATASET.BEHAVIOR not in nwbfile:
                nwbfile[NWBDATASET.BEHAVIOR] = {}
            nwbfile[NWBDATASET.BEHAVIOR][rule_config.return_arg] = info[
                rule_config.return_arg
            ]
        else:
            assert False, "NodeType doesn't exist"

        nwbfile.pop("image_series", None)
        info["nwbfile"] = {"input": nwbfile}
        return info

    @classmethod
    def image(cls, rule_config: Rule):
        input_path = cls._resolve_snakemake_storage_path(rule_config.input)

        info = {rule_config.return_arg: ImageData(input_path, "")}
        nwbfile = rule_config.nwbfile
        nwbfile["image_series"]["external_file"] = info[rule_config.return_arg]
        info["nwbfile"] = {"input": nwbfile}
        return info

    @classmethod
    def hdf5(cls, rule_config: Rule):
        input_path = cls._resolve_snakemake_storage_path(rule_config.input)
        nwbfile = rule_config.nwbfile

        with h5py.File(input_path, "r") as f:
            data = f[rule_config.hdf5Path][:]

        return cls.get_info_from_array_data(rule_config, nwbfile, data)

    @classmethod
    def mat(cls, rule_config: Rule):
        input_path = cls._resolve_snakemake_storage_path(rule_config.input)
        nwbfile = rule_config.nwbfile
        data = MatGetter.data(input_path, rule_config.matPath)
        return cls.get_info_from_array_data(rule_config, nwbfile, data)

    @classmethod
    def microscope(cls, rule_config: Rule):
        input_path = cls._resolve_snakemake_storage_path(rule_config.input)

        info = {rule_config.return_arg: MicroscopeData(input_path)}
        nwbfile = rule_config.nwbfile
        nwbfile["image_series"]["external_file"] = info[rule_config.return_arg]
        info["nwbfile"] = {"input": nwbfile}
        return info

    @classmethod
    def get_info_from_array_data(cls, rule_config: Rule, nwbfile, data):
        if data.ndim == 3:
            info = {rule_config.return_arg: ImageData(data)}
            nwbfile["image_series"]["external_file"] = info[rule_config.return_arg]
            info["nwbfile"] = {"input": nwbfile}
            info["nwbfile"][FILETYPE.IMAGE] = nwbfile
        elif data.ndim == 2:
            info = {rule_config.return_arg: TimeSeriesData(data)}

            if NWBDATASET.TIMESERIES not in nwbfile:
                nwbfile[NWBDATASET.TIMESERIES] = {}

            nwbfile[NWBDATASET.TIMESERIES][rule_config.return_arg] = info[
                rule_config.return_arg
            ]
            nwbfile.pop("image_series", None)
            info["nwbfile"] = {"input": nwbfile}
        elif data.ndim == 1:
            info = {rule_config.return_arg: IscellData(data)}

            if NWBDATASET.COLUMN not in nwbfile:
                nwbfile[NWBDATASET.COLUMN] = {}

            nwbfile[NWBDATASET.COLUMN][rule_config.return_arg] = info[
                rule_config.return_arg
            ]
            info["nwbfile"] = {"input": nwbfile}
        return info
