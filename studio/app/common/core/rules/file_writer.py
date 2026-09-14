import os

import h5py
import numpy as np

from studio.app.common.core.snakemake.smk import Rule
from studio.app.common.dataclass import CsvData, ImageData
from studio.app.const import FILETYPE
from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.dataclass.fluo import FluoData
from studio.app.optinist.dataclass.iscell import IscellData
from studio.app.optinist.dataclass.microscope import MicroscopeData
from studio.app.optinist.routers.mat import MatGetter


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
            data = dataset[:]
            n_rois = len(dataset.parent["rois"]) if "rois" in dataset.parent else None

        info = cls.get_info_from_array_data(rule_config, nwbfile, data)
        if n_rois is not None and data.ndim == 2:
            cls._orient_nwb_fluorescence(info[rule_config.return_arg], n_rois)
        return info

    @classmethod
    def _orient_nwb_fluorescence(cls, fluo: FluoData, n_rois: int):
        """NWB RoiResponseSeries is (time, roi); FluoData is (roi, time)."""
        n_rows, n_cols = fluo.data.shape
        if n_cols == n_rois and n_rows != n_rois:
            fluo.data = fluo.data.T
            fluo.index = np.arange(n_rows)
            fluo.cell_numbers = range(n_cols)
        elif n_rows != n_rois:
            return
        fluo.nwb_oriented = True

    @classmethod
    def mat(cls, rule_config: Rule):
        nwbfile = rule_config.nwbfile
        data = MatGetter.data(rule_config.input, rule_config.matPath)
        return cls.get_info_from_array_data(rule_config, nwbfile, data)

    @classmethod
    def microscope(cls, rule_config: Rule):
        info = {rule_config.return_arg: MicroscopeData(rule_config.input)}
        nwbfile = rule_config.nwbfile
        nwbfile["image_series"]["external_file"] = info[rule_config.return_arg]
        info["nwbfile"] = {"input": nwbfile}
        return info

    @classmethod
    def get_info_from_array_data(cls, rule_config: Rule, nwbfile, data):
        if data.ndim >= 3:
            if data.dtype == np.float64:
                data = data.astype(np.float32)
            info = {
                rule_config.return_arg: ImageData(
                    data,
                    output_dir=os.path.dirname(rule_config.output),
                    file_name="image",
                )
            }
            nwbfile["image_series"]["external_file"] = info[rule_config.return_arg]
            info["nwbfile"] = {"input": nwbfile}
            info["nwbfile"][FILETYPE.IMAGE] = nwbfile
        elif data.ndim == 2:
            info = {rule_config.return_arg: FluoData(data)}

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
