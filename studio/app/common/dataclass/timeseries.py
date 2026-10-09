from typing import Optional

import numpy as np
import pandas as pd

from studio.app.common.core.utils.filepath_creater import (
    create_directory,
    join_filepath,
)
from studio.app.common.core.utils.json_writer import JsonWriter
from studio.app.common.core.workflow.workflow import OutputPath, OutputType
from studio.app.common.dataclass.base import BaseData
from studio.app.common.dataclass.timeseries_chunk_handler import TimeSeriesChunkHandler
from studio.app.common.schemas.outputs import PlotMetaData


class TimeSeriesData(BaseData):
    def __init__(
        self,
        data,
        std=None,
        sem=None,
        index=None,
        cell_numbers=None,
        params=None,
        file_name="timeseries",
        meta: Optional[PlotMetaData] = None,
    ):
        super().__init__(file_name)
        self.meta = meta

        assert data.ndim <= 2, "TimeSeries Dimension Error"

        if isinstance(data, str):
            header = params.get("setHeader", None) if isinstance(params, dict) else None
            self.data = pd.read_csv(data, header=header).values
        else:
            self.data = data

        # Handle empty data case
        if self.data.size == 0:
            # For K=0 case, create empty 2D array with correct time dimension
            self.data = np.zeros((0, index.size if index is not None else 0))
        elif len(self.data.shape) == 1:
            self.data = self.data[np.newaxis, :]

        self.std = std
        self.sem = sem
        # True once the loader has put the array in (roi, time); wrappers skip transpose
        self.nwb_oriented = False

        # Handle index for empty data case
        if index is not None:
            self.index = index
        elif self.data.size > 0:
            self.index = np.arange(len(self.data[0]))
        else:
            self.index = np.array([])  # Empty index for empty data

        # Handle cell numbers for empty data case
        if cell_numbers is not None:
            self.cell_numbers = cell_numbers
        else:
            self.cell_numbers = range(len(self.data))

    def save_json(self, json_dir):
        # timeseriesだけはdirを返す
        self.json_path = join_filepath([json_dir, self.file_name])
        create_directory(self.json_path, delete_dir=True)
        JsonWriter.write_plot_meta(json_dir, self.file_name, self.meta)

        record_ids = [str(cell_i) for cell_i in self.cell_numbers]
        columns = {"data": self.data}
        if self.std is not None:
            # A 1D std holds one value per record, spread over its time points
            std = np.asarray(self.std)[: len(self.data)]
            if std.ndim == 1:
                std = std[:, np.newaxis]
            columns["std"] = np.broadcast_to(std, self.data.shape)

        TimeSeriesChunkHandler.save_chunked_data(
            dirpath=self.json_path,
            record_ids=record_ids,
            columns=columns,
            index=self.index,
        )

    @property
    def output_path(self) -> OutputPath:
        return OutputPath(
            path=self.json_path,
            type=OutputType.TIMESERIES,
            max_index=len(self.data),
            data_shape=list(self.data.shape),
        )
