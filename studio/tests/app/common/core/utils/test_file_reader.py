"""``JsonReader.read_as_output_bytes``: a JSON file served without parsing it."""

import json
from dataclasses import asdict

import pandas as pd
import pytest

from studio.app.common.core.utils.file_reader import JsonReader
from studio.app.common.schemas.outputs import PlotMetaData

FRAMES = [[[0, 1], [2, 3]], [[4.5, 5], [6, 7]]]


@pytest.mark.parametrize("indent", [None, 4], ids=["compact", "indented"])
@pytest.mark.parametrize(
    "plot_meta", [None, {"title": "t", "xlabel": "x"}], ids=["no_meta", "meta"]
)
def test_the_file_is_served_with_the_plot_meta_added(tmp_path, indent, plot_meta):
    path = tmp_path / "stack_1_2.json"
    path.write_text(pd.DataFrame(FRAMES).to_json(orient="split", indent=indent))
    if plot_meta is not None:
        (tmp_path / "stack_1_2.plot-meta.json").write_text(json.dumps(plot_meta))

    served = json.loads(JsonReader.read_as_output_bytes(str(path)))

    expected = json.loads(path.read_text())
    expected["meta"] = asdict(JsonReader.read_as_output(str(path)).meta)
    assert served == expected


@pytest.mark.parametrize("batch_bytes", [1 << 20, 16], ids=["one_batch", "batches"])
def test_line_breaks_and_indentation_are_dropped_and_strings_kept(
    tmp_path, monkeypatch, batch_bytes
):
    monkeypatch.setattr(JsonReader, "READ_BATCH_BYTES", batch_bytes)
    columns = ["a b", "  lead", "line\nbreak", 'quote " and \\']
    frame = pd.DataFrame([[1.5, None, 3, 4]], columns=columns)
    path = tmp_path / "table.json"
    path.write_text(frame.to_json(orient="split", indent=4))

    served = JsonReader.read_as_output_bytes(str(path))

    # no string holds four spaces, so any left would be indentation
    assert b"\n" not in served and b"    " not in served
    expected = json.loads(path.read_text())
    assert json.loads(served) == {**expected, "meta": asdict(PlotMetaData())}
    assert json.loads(served)["columns"] == columns
