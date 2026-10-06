import gc
import os
from typing import Optional

import imageio
import numpy as np
import tifffile

from studio.app.common.core.utils.filepath_creater import (
    create_directory,
    join_filepath,
)
from studio.app.common.core.utils.json_writer import JsonWriter
from studio.app.common.core.workflow.workflow import OutputPath, OutputType
from studio.app.common.dataclass.base import BaseData
from studio.app.common.dataclass.utils import create_images_list
from studio.app.common.schemas.outputs import PlotMetaData
from studio.app.dir_path import DIRPATH


class ImageData(BaseData):
    def __init__(
        self,
        data,
        output_dir=DIRPATH.OUTPUT_DIR,
        file_name="image",
        meta: Optional[PlotMetaData] = None,
    ):
        super().__init__(file_name)

        self.json_path = None
        self.meta = meta

        if data is None:
            self.path = None
        elif isinstance(data, str):
            self.path = data
        elif isinstance(data, list) and isinstance(data[0], str):
            self.path = data
        else:
            _dir = join_filepath([output_dir, "tiff", file_name])
            create_directory(_dir)

            _path = join_filepath([_dir, f"{file_name}.tif"])
            tifffile.imwrite(_path, data)
            self.path = [_path]

            del data
            gc.collect()

    def split_image(self, output_dir: str, n_files: int = 2, lengths: list = None):
        """
        Split the image along the time axis, either into n_files near-equal
        parts or at the explicit per-part frame counts given in lengths.
        """
        image = self.data
        frames = image.shape[0]

        if lengths is not None:
            if len(lengths) < 2:
                raise ValueError(f"lengths needs at least 2 entries. Got {lengths}.")
            try:
                lengths = [int(length) for length in lengths]
            except (TypeError, ValueError):
                raise ValueError(f"lengths must be integers. Got {lengths}.")
            if min(lengths) <= 0:
                raise ValueError(f"lengths must be positive. Got {lengths}.")
            if sum(lengths) != frames:
                raise ValueError(
                    f"sum of lengths ({sum(lengths)}) must equal "
                    f"total frames ({frames})."
                )
        else:
            if n_files < 2:
                raise ValueError(f"n_files should be greater than 1. Got {n_files}.")
            if frames < n_files:
                raise ValueError(f"cannot split {frames} frames into {n_files} parts.")
            base, extra = divmod(frames, n_files)
            lengths = [base + 1 if i < extra else base for i in range(n_files)]

        file_name = self.path[0] if isinstance(self.path, list) else self.path
        name, ext = os.path.splitext(os.path.basename(file_name))
        save_paths = []

        _dir = join_filepath([output_dir, "image_split", name])
        create_directory(_dir)

        offset = 0
        for n, length in enumerate(lengths):
            _path = join_filepath([_dir, f"{name}_{n}{ext}"])
            with tifffile.TiffWriter(_path, bigtiff=True) as tif:
                tif.write(image[offset : offset + length])
            offset += length
            save_paths.append(_path)

        return save_paths

    @property
    def data(self):
        if isinstance(self.path, list):
            return np.concatenate([imageio.volread(p) for p in self.path])
        else:
            return np.array(imageio.volread(self.path))

    def save_json(self, json_dir):
        if self.data.ndim < 3:
            self.json_path = join_filepath([json_dir, f"{self.file_name}.json"])
            JsonWriter.write_as_split(self.json_path, create_images_list(self.data))
            JsonWriter.write_plot_meta(json_dir, self.file_name, self.meta)

    @property
    def output_path(self) -> OutputPath:
        if self.data.ndim >= 3:
            # self.path will be a list if self.data got into else statement on __init__
            if isinstance(self.path, list) and isinstance(self.path[0], str):
                _path = self.path[0]
            else:
                _path = self.path
            return OutputPath(
                path=_path,
                type=OutputType.IMAGE,
                max_index=len(self.data),
            )
        else:
            return OutputPath(
                path=self.json_path,
                type=OutputType.IMAGE,
                max_index=1,
            )
