import os
from typing import Optional

import numpy as np
import tifffile

from studio.app.common.dataclass.base import BaseData
from studio.app.common.schemas.outputs import PlotMetaData


class Suite2pData(BaseData):
    def __init__(self, data, file_name="suite2p", meta: Optional[PlotMetaData] = None):
        super().__init__(file_name)
        self.data = data
        self.meta = meta

    def save_json(self, json_dir):
        pass


def suite2p_movie(ops: dict):
    """
    The movie a suite2p node reads from the ops it is given, (frames, Ly, Lx)
    int16, memory-mapped read-only.

    Registration writes its result to the TIFF in ops["registered_tiff"] and
    leaves the binary unregistered. Ops without that key come from file
    conversion, or from an earlier release that registered the binary in
    place: their movie is the binary, ops["reg_file"].
    """
    tiff = ops.get("registered_tiff")
    path = ops["reg_file"] if tiff is None else tiff
    if not os.path.exists(path):
        raise FileNotFoundError(f"suite2p movie not found: {path}")
    shape = (int(ops["nframes"]), ops["Ly"], ops["Lx"])
    if tiff is not None:
        # tifffile reads a one-frame stack without its frame axis
        return tifffile.memmap(path, mode="r").reshape(shape)
    return np.memmap(path, dtype=np.int16, mode="r", shape=shape)
