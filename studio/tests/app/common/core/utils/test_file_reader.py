"""``JsonReader.read_as_output_bytes``: a JSON file served without parsing it."""

import json
from dataclasses import asdict

import pandas as pd
import pytest

from studio.app.common.core.utils.file_reader import JsonReader

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
