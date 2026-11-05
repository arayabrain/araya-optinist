"""``JsonReader.read_as_output_bytes``: a JSON file served without parsing it."""

import json
import os
from dataclasses import asdict

import numpy as np
import pandas as pd
import pytest

from studio.app.common.core.utils import file_reader
from studio.app.common.core.utils.file_reader import JsonReader
from studio.app.common.core.utils.json_writer import JsonWriter
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


@pytest.mark.parametrize("cut", ["empty", "half", "last_line"])
def test_a_file_cut_short_is_refused(tmp_path, cut):
    text = pd.DataFrame(FRAMES).to_json(orient="split", indent=4)
    path = tmp_path / "stack_1_2.json"
    path.write_text(
        {
            "empty": "",
            "half": text[: len(text) // 2],
            "last_line": text[: text.rindex("\n")],
        }[cut]
    )

    with pytest.raises(ValueError, match="whole JSON object"):
        JsonReader.read_as_output_bytes(str(path))


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


class RewrittenAfterFirstBatch:
    """A file handle whose file is rewritten once its first batch is read."""

    def __init__(self, f, rewrite):
        self.f, self.rewrite, self.batches = f, rewrite, 0

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.f.close()

    def readlines(self, hint):
        lines = self.f.readlines(hint)
        self.batches += 1
        if self.batches == 1:
            self.rewrite()
        return lines


def test_a_file_rewritten_during_the_read_is_served_as_it_was(tmp_path, monkeypatch):
    monkeypatch.setattr(JsonReader, "READ_BATCH_BYTES", 64 << 10)
    path = str(tmp_path / "cell_roi.json")
    # The first batch ends in the data's first rows, which run on far past it
    # and the read buffer
    old = np.arange(300 * 300, dtype=float).reshape(300, 300)
    JsonWriter.write_as_split(path, old)

    def open_rewritten(file, *args, **kwargs):
        f = open(file, *args, **kwargs)
        if file != path:
            return f
        return RewrittenAfterFirstBatch(
            f, lambda: JsonWriter.write_as_split(path, old + 0.5)
        )

    monkeypatch.setattr(file_reader, "open", open_rewritten, raising=False)
    served = json.loads(JsonReader.read_as_output_bytes(path))

    assert served["data"] == old.tolist()
    # the rewrite took effect and left nothing beside it
    rewritten = json.loads((tmp_path / "cell_roi.json").read_text())
    assert rewritten["data"] == (old + 0.5).tolist()
    assert os.listdir(tmp_path) == ["cell_roi.json"]
