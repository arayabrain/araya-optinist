"""Chunks built straight from arrays keep the content the DataFrame path wrote."""

from unittest.mock import patch

import numpy as np

from studio.app.common.dataclass.csv import CsvData
from studio.app.common.dataclass.timeseries import TimeSeriesData
from studio.app.common.dataclass.timeseries_chunk_handler import (
    TimeSeriesChunkHandler as Handler,
)


def test_records_across_chunks_keep_nan_and_a_per_record_std(tmp_path):
    data = TimeSeriesData(
        np.array([[1.0, np.nan], [np.inf, 4.0]]),
        std=np.array([0.5, 0.25]),
        index=np.array([10, 20]),
    )
    with patch.object(Handler, "CHUNK_SIZE", 1):
        data.save_json(str(tmp_path))

    path = str(tmp_path / "timeseries")
    columns = ["data", "std"]
    assert Handler.load_index_map(path) == {"0": 0, "1": 1}
    assert Handler.load_all_records(path) == {
        "0": {"index": [10, 20], "columns": columns, "data": [[1.0, 0.5], [None, 0.5]]},
        "1": {
            "index": [10, 20],
            "columns": columns,
            "data": [[None, 0.25], [4.0, 0.25]],
        },
    }


def test_a_mixed_csv_column_writes_nan_as_null(tmp_path):
    CsvData(np.array([["a", np.nan]], dtype=object), params={}).save_json(str(tmp_path))

    assert Handler.get_record_data(str(tmp_path / "csv"), "0") == {
        "index": [0, 1],
        "columns": ["data"],
        "data": [["a"], [None]],
    }
