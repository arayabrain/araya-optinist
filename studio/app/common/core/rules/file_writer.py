import hashlib
import os
import shutil

import h5py
import numpy as np

from studio.app.common.core.snakemake.smk import Rule
from studio.app.common.core.utils.filepath_creater import (
    create_directory,
    join_filepath,
)
from studio.app.common.dataclass import CsvData, ImageData
from studio.app.const import FILETYPE
from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.dataclass.fluo import FluoData
from studio.app.optinist.dataclass.iscell import IscellData
from studio.app.optinist.dataclass.microscope import MicroscopeData
from studio.app.optinist.routers.mat import MatGetter


def dataclass_for_rank(ndim: int):
    """The rank rule, in one place. The run-time precheck compares against this."""
    if ndim >= 3:
        return ImageData
    return FluoData if ndim == 2 else IscellData


class FileWriter:
    @classmethod
    def csv(cls, rule_config: Rule, nodeType):
        info = {
            rule_config.return_arg: CsvData(rule_config.input, rule_config.params, "")
        }
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
        info = {rule_config.return_arg: ImageData(rule_config.input, "")}
        nwbfile = rule_config.nwbfile
        nwbfile["image_series"]["external_file"] = info[rule_config.return_arg]
        info["nwbfile"] = {"input": nwbfile}
        return info

    @classmethod
    def hdf5(cls, rule_config: Rule):
        nwbfile = rule_config.nwbfile

        with h5py.File(rule_config.input, "r") as f:
            dataset = f[rule_config.hdf5Path]
            n_rois = cls._sibling_roi_count(dataset)
            cached = cls._cached_tiff(rule_config, rule_config.hdf5Path)
            data = dataset[:] if cached is None else None

        if cached is not None:
            return cls._image_info(rule_config, nwbfile, ImageData([cached]))

        info = cls.get_info_from_array_data(
            rule_config, nwbfile, data, source_key=rule_config.hdf5Path
        )
        if n_rois is not None and data.ndim == 2:
            cls._orient_nwb_fluorescence(info[rule_config.return_arg], n_rois)
        return info

    @staticmethod
    def _sibling_roi_count(dataset):
        rois = dataset.parent.get("rois")
        if isinstance(rois, h5py.Dataset) and rois.ndim == 1:
            return rois.shape[0]
        return None

    @classmethod
    def _orient_nwb_fluorescence(cls, fluo: FluoData, n_rois: int):
        """NWB RoiResponseSeries is (time, roi); FluoData is (roi, time)."""
        n_rows, n_cols = fluo.data.shape
        if (n_rows == n_rois) == (n_cols == n_rois):
            return
        if n_cols == n_rois:
            fluo.data = fluo.data.T
            fluo.index = np.arange(n_rows)
            fluo.cell_numbers = range(n_cols)
        fluo.nwb_oriented = True

    @classmethod
    def mat(cls, rule_config: Rule):
        nwbfile = rule_config.nwbfile
        cached = cls._cached_tiff(rule_config, rule_config.matPath)
        if cached is not None:
            return cls._image_info(rule_config, nwbfile, ImageData([cached]))

        data = MatGetter.data(rule_config.input, rule_config.matPath)
        return cls.get_info_from_array_data(
            rule_config, nwbfile, data, source_key=rule_config.matPath
        )

    @classmethod
    def microscope(cls, rule_config: Rule):
        info = {rule_config.return_arg: MicroscopeData(rule_config.input)}
        nwbfile = rule_config.nwbfile
        nwbfile["image_series"]["external_file"] = info[rule_config.return_arg]
        info["nwbfile"] = {"input": nwbfile}
        return info

    @classmethod
    def _tiff_cache_path(cls, rule_config: Rule, source_key: str):
        """One tiff per (file, dataset, mtime) per workspace, not one per run.

        ponytail: never pruned, so a re-uploaded input leaks its old tiff;
        add a sweep if workspaces grow faster than users delete them.
        """
        src = rule_config.input
        if not source_key or not isinstance(src, str) or not os.path.isfile(src):
            return None
        digest = hashlib.sha1(
            f"{os.path.realpath(src)}:{source_key}:{os.path.getmtime(src)}".encode()
        ).hexdigest()[:16]
        # dirname(output) is OUTPUT_DIR/{workspace}/{unique_id}/{node}
        workspace_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(rule_config.output))
        )
        return join_filepath(
            [workspace_dir, "input_tiff", digest, "tiff", "image", "image.tif"]
        )

    @classmethod
    def _cached_tiff(cls, rule_config: Rule, source_key: str):
        path = cls._tiff_cache_path(rule_config, source_key)
        return path if path and os.path.isfile(path) else None

    @classmethod
    def _write_tiff(cls, rule_config: Rule, source_key: str, data) -> ImageData:
        cache_path = cls._tiff_cache_path(rule_config, source_key)
        if cache_path is None:
            return ImageData(
                data,
                output_dir=os.path.dirname(rule_config.output),
                file_name="image",
            )

        cache_dir = os.path.dirname(os.path.dirname(os.path.dirname(cache_path)))
        tmp_dir = f"{cache_dir}.{os.getpid()}"
        try:
            image = ImageData(data, output_dir=tmp_dir, file_name="image")
            create_directory(os.path.dirname(cache_path))
            os.replace(image.path[0], cache_path)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        image.path = [cache_path]
        return image

    @classmethod
    def _image_info(cls, rule_config: Rule, nwbfile, image: ImageData):
        info = {rule_config.return_arg: image}
        nwbfile["image_series"]["external_file"] = image
        info["nwbfile"] = {"input": nwbfile}
        info["nwbfile"][FILETYPE.IMAGE] = nwbfile
        return info

    @classmethod
    def get_info_from_array_data(
        cls, rule_config: Rule, nwbfile, data, source_key: str = None
    ):
        produced = dataclass_for_rank(data.ndim)
        if produced is ImageData:
            if data.dtype == np.float64:
                # halves the tiff; astype peaks at ~1.5x the input in memory
                data = data.astype(np.float32)
            info = cls._image_info(
                rule_config, nwbfile, cls._write_tiff(rule_config, source_key, data)
            )
        elif produced is FluoData:
            info = {rule_config.return_arg: FluoData(data)}

            if NWBDATASET.TIMESERIES not in nwbfile:
                nwbfile[NWBDATASET.TIMESERIES] = {}

            nwbfile[NWBDATASET.TIMESERIES][rule_config.return_arg] = info[
                rule_config.return_arg
            ]
            nwbfile.pop("image_series", None)
            info["nwbfile"] = {"input": nwbfile}
        elif produced is IscellData and data.ndim == 1:
            info = {rule_config.return_arg: IscellData(data)}

            if NWBDATASET.COLUMN not in nwbfile:
                nwbfile[NWBDATASET.COLUMN] = {}

            nwbfile[NWBDATASET.COLUMN][rule_config.return_arg] = info[
                rule_config.return_arg
            ]
            info["nwbfile"] = {"input": nwbfile}
        return info
