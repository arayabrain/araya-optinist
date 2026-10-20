"""CSV inputs load time-major: rows are time, and Transpose is for a file with
one row per channel. The node type picks the NWB bucket."""

import numpy as np
import pytest

from studio.app.common.core.rules.file_writer import FileWriter
from studio.app.common.core.snakemake.smk import Rule
from studio.app.common.dataclass import CsvData
from studio.app.const import FILETYPE
from studio.app.optinist.core.nwb.nwb import NWBDATASET

TABLE = np.random.default_rng(0).random((20, 3))


def _load(tmp_path, stored, node_type=FILETYPE.CSV, **params):
    path = tmp_path / "in.csv"
    np.savetxt(path, stored, delimiter=",", fmt="%.17g")
    rule = Rule(
        input=str(path),
        return_arg="input_x",
        params={"setHeader": None, "transpose": False, **params},
        output=str(tmp_path / "input_x.pkl"),
        type=node_type,
        nwbfile={"image_series": {}},
    )
    return FileWriter.csv(rule, node_type)


@pytest.mark.parametrize(
    "node_type, bucket",
    [(FILETYPE.CSV, NWBDATASET.TIMESERIES), (FILETYPE.BEHAVIOR, NWBDATASET.BEHAVIOR)],
)
@pytest.mark.parametrize("stored, transpose", [(TABLE, False), (TABLE.T, True)])
def test_rows_are_time_either_way(tmp_path, node_type, bucket, stored, transpose):
    info = _load(tmp_path, stored, node_type, transpose=transpose)

    loaded = info["input_x"]
    assert type(loaded) is CsvData
    np.testing.assert_allclose(loaded.data, TABLE)
    assert info["nwbfile"]["input"][bucket]["input_x"] is loaded


@pytest.mark.parametrize(
    "stored, transpose",
    [(TABLE[:, :1], False), (TABLE[:, :1].T, True)],
    ids=["one-column", "one-row-transposed"],
)
def test_one_channel_is_one_time_column(tmp_path, stored, transpose):
    loaded = _load(tmp_path, stored, transpose=transpose)["input_x"]

    np.testing.assert_allclose(loaded.data, TABLE[:, :1])


def test_header_row_is_dropped(tmp_path):
    path = tmp_path / "in.csv"
    np.savetxt(path, TABLE, delimiter=",", fmt="%.17g", header="a,b,c", comments="")
    rule = Rule(
        input=str(path),
        return_arg="input_x",
        params={"setHeader": 0, "transpose": False},
        output=str(tmp_path / "input_x.pkl"),
        type=FILETYPE.CSV,
        nwbfile={"image_series": {}},
    )

    loaded = FileWriter.csv(rule, FILETYPE.CSV)["input_x"]

    np.testing.assert_allclose(loaded.data, TABLE)
