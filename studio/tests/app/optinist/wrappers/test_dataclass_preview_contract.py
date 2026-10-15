"""The preview contract: a time-series-like output saves one record per
non-time row, each with the time index as x, and output_path describes the
same records. BehaviorData is (time, columns), so it saves one record per
column; the other types are pinned as they are."""

import json

import numpy as np
import pytest

from studio.app.common.dataclass import (
    BarData,
    CsvData,
    HeatMapData,
    ImageData,
    ScatterData,
    TimeSeriesData,
)
from studio.app.common.dataclass.timeseries_chunk_handler import TimeSeriesChunkHandler
from studio.app.optinist.dataclass import BehaviorData, FluoData, IscellData, RoiData

N_TIME, N_COL = 12, 3
RNG = np.random.default_rng(0)
TIME_INDEX = np.arange(100, 100 + N_TIME)


def assert_preview(saved, rows, index, std=None):
    """`saved` was saved as len(rows) records of (index -> row)."""
    records = TimeSeriesChunkHandler.load_all_records(saved.json_path)
    assert sorted(records, key=int) == [str(i) for i in range(len(rows))]
    for i, row in enumerate(rows):
        record = records[str(i)]
        values = np.array(record["data"], dtype=float).reshape(len(row), -1)
        assert record["index"] == list(index)
        np.testing.assert_allclose(values[:, record["columns"].index("data")], row)
        if std is not None:
            np.testing.assert_allclose(
                values[:, record["columns"].index("std")], std[i]
            )
    assert saved.output_path.max_index == len(rows)
    assert saved.output_path.data_shape == [len(rows), len(index)]


@pytest.fixture
def json_dir(tmp_path):
    d = tmp_path / "output" / "default" / "uid" / "func"
    d.mkdir(parents=True)
    return str(d)


@pytest.mark.parametrize("cls", [TimeSeriesData, FluoData])
def test_rows_time_series_save_one_record_per_row(cls, json_dir):
    data, std = RNG.random((N_COL, N_TIME)), RNG.random((N_COL, N_TIME))
    saved = cls(data, std=std, index=TIME_INDEX, file_name="t")

    saved.save_json(json_dir)

    assert_preview(saved, data, TIME_INDEX, std)


def test_behaviour_saves_one_record_per_column(json_dir):
    data, std = RNG.random((N_TIME, N_COL)), RNG.random((N_TIME, N_COL))
    saved = BehaviorData(data, std=std, index=TIME_INDEX, file_name="b")

    saved.save_json(json_dir)

    assert_preview(saved, data.T, TIME_INDEX, std.T)
    assert saved.data.shape == (N_TIME, N_COL)


def test_behaviour_defaults_index_time_and_records_columns():
    saved = BehaviorData(RNG.random((N_TIME, N_COL)))

    assert list(saved.index) == list(range(N_TIME))
    assert list(saved.cell_numbers) == list(range(N_COL))


@pytest.mark.parametrize("with_std", [False, True])
def test_1d_behaviour_is_one_column_and_one_record(with_std, json_dir):
    data = RNG.random(N_TIME)
    std = RNG.random(N_TIME) if with_std else None
    saved = BehaviorData(data, std=std, file_name="b")

    saved.save_json(json_dir)

    assert saved.data.shape == (N_TIME, 1)
    assert_preview(
        saved, data[np.newaxis], np.arange(N_TIME), None if std is None else [std]
    )


def test_empty_behaviour_keeps_its_columns(json_dir):
    saved = BehaviorData(np.zeros((0, N_COL)), file_name="b")

    saved.save_json(json_dir)

    assert saved.data.shape == (0, N_COL)
    assert saved.output_path.data_shape == [N_COL, 0]


def test_csv_saves_one_record_per_row(json_dir):
    data = RNG.random((N_TIME, N_COL))
    saved = CsvData(data, params={}, file_name="c")

    saved.save_json(json_dir)

    records = TimeSeriesChunkHandler.load_all_records(saved.json_path)
    assert len(records) == N_TIME
    assert np.array(records["0"]["data"]).ravel().tolist() == list(data[0])
    assert saved.output_path is None


def _split_json(path):
    with open(path) as f:
        return json.load(f)


def test_heatmap_saves_its_rows(json_dir):
    saved = HeatMapData(RNG.random((4, 5)), index=[1, 3, 4, 7], file_name="h")
    saved.save_json(json_dir)

    split = _split_json(saved.json_path)
    assert split["index"] == [1, 3, 4, 7] and np.shape(split["data"]) == (4, 5)
    assert saved.output_path.type == "heatmap"


def test_bar_saves_its_rows(json_dir):
    saved = BarData(RNG.random((3, 2)), file_name="bar")
    saved.save_json(json_dir)

    assert np.shape(_split_json(saved.json_path)["data"]) == (3, 2)
    assert saved.output_path.type == "bar"


def test_scatter_saves_coordinates_as_rows(json_dir):
    points = RNG.random((10, 2))
    saved = ScatterData(points, file_name="s")
    saved.save_json(json_dir)

    np.testing.assert_allclose(_split_json(saved.json_path)["data"], points.T)
    assert saved.output_path.type == "scatter"


def test_iscell_saves_nothing(json_dir):
    saved = IscellData(np.ones(N_COL))
    saved.save_json(json_dir)

    assert saved.output_path is None and not hasattr(saved, "json_path")


def test_image_movie_reports_frames_and_a_frame_saves_one_image(json_dir):
    movie = ImageData(
        RNG.random((5, 4, 4)).astype(np.float32), output_dir=json_dir, file_name="m"
    )
    frame = ImageData(
        RNG.random((4, 4)).astype(np.float32), output_dir=json_dir, file_name="f"
    )
    movie.save_json(json_dir)
    frame.save_json(json_dir)

    assert movie.output_path.max_index == 5
    assert frame.output_path.max_index == 1
    assert np.shape(_split_json(frame.json_path)["data"]) == (1, 4, 4)


def test_roi_saves_one_image(json_dir):
    saved = RoiData(RNG.random((4, 4)), output_dir=json_dir, file_name="r")
    saved.save_json(json_dir)

    assert len(_split_json(saved.json_path)["data"]) == 1
    assert saved.output_path.type == "roi"
