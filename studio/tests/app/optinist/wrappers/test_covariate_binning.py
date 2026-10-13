import json
import logging

import numpy as np
import pytest

from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.dataclass import BehaviorData, FluoData, IscellData
from studio.app.optinist.wrappers.optinist.basic_neural_analysis.covariate_binning import (  # noqa: E501
    covariate_binning,
)

NUM_CELL = 3
NUM_FRAME = 120
PARAMS = {
    "transpose_x": True,
    "transpose_y": False,
    "event_col_index": 1,
    "n_bins": 4,
    "use_data_range": False,
    "bin_min": 0.0,
    "bin_max": 4.0,
    "sort_by_peak": False,
}


def _fluo():
    return np.random.default_rng(0).random((NUM_CELL, NUM_FRAME))


def _behavior(covariate):
    return np.column_stack([np.zeros(len(covariate)), covariate])


def _covariate():
    return np.tile([0.5, 1.5, 2.5, 3.5], NUM_FRAME // 4)


def _bin(tmp_path, fluo=None, behavior=None, params=None, iscell=None):
    return covariate_binning(
        FluoData(_fluo() if fluo is None else fluo, file_name="f"),
        BehaviorData(
            _behavior(_covariate()) if behavior is None else behavior, file_name="b"
        ),
        str(tmp_path / "default" / "uid" / "covariate_binning_1"),
        iscell=iscell,
        params={**PARAMS, **(params or {})},
    )


def _postprocess(info):
    return next(iter(info["nwbfile"][NWBDATASET.POSTPROCESS].values()))


def test_shapes_and_bin_centres(tmp_path):
    info = _bin(tmp_path)
    out = _postprocess(info)

    assert info["mean"].data.shape == (NUM_CELL, 4)
    assert out["std"].shape == out["sem"].shape == (NUM_CELL, 4)
    assert info["mean_heatmap"].data.shape == (NUM_CELL, 4)
    assert list(info["mean"].index) == [0.5, 1.5, 2.5, 3.5]
    assert info["mean_heatmap"].columns == [0.5, 1.5, 2.5, 3.5]
    assert list(out["num_sample"]) == [NUM_FRAME // 4] * 4


def test_each_bin_averages_exactly_its_samples(tmp_path):
    fluo = _fluo()
    covariate = _covariate()
    out = _postprocess(_bin(tmp_path, fluo=fluo))

    for b, centre in enumerate([0.5, 1.5, 2.5, 3.5]):
        samples = fluo[:, covariate == centre]
        np.testing.assert_allclose(out["mean"][:, b], samples.mean(axis=1))
        np.testing.assert_allclose(out["std"][:, b], samples.std(axis=1, ddof=1))
        np.testing.assert_allclose(
            out["sem"][:, b], out["std"][:, b] / np.sqrt(samples.shape[1])
        )


def test_edges_are_half_open_except_the_last_and_out_of_range_is_dropped(tmp_path):
    covariate = np.array([0.0, 1.0, 1.999, 4.0, 4.5, -0.1, np.nan, 3.0])
    fluo = np.arange(len(covariate), dtype=float)[np.newaxis, :]

    out = _postprocess(_bin(tmp_path, fluo=fluo, behavior=_behavior(covariate)))

    assert list(out["num_sample"]) == [1, 2, 0, 2]
    np.testing.assert_allclose(out["mean"][0], [0.0, 1.5, np.nan, 5.0])


def test_data_range_spans_the_covariate_min_to_max(tmp_path):
    covariate = np.linspace(10.0, 20.0, NUM_FRAME)

    info = _bin(
        tmp_path, behavior=_behavior(covariate), params={"use_data_range": True}
    )

    assert list(info["mean"].index) == [11.25, 13.75, 16.25, 18.75]
    assert _postprocess(info)["num_sample"].sum() == NUM_FRAME


def test_sort_by_peak_reorders_only_the_heatmap(tmp_path):
    covariate = _covariate()
    peak_bins = [2, 0, 3]
    fluo = np.stack([(covariate == [0.5, 1.5, 2.5, 3.5][b]) * 1.0 for b in peak_bins])

    info = _bin(tmp_path, fluo=fluo, params={"sort_by_peak": True})

    assert info["mean_heatmap"].index == [1, 0, 2]
    np.testing.assert_array_equal(
        np.argmax(info["mean_heatmap"].data, axis=1), [0, 2, 3]
    )
    np.testing.assert_array_equal(np.argmax(info["mean"].data, axis=1), peak_bins)


def test_sort_by_peak_ignores_an_empty_bin(tmp_path):
    covariate = np.tile([1.5, 2.5, 3.5], 40)  # bin 0 stays empty, so NaN
    fluo = np.stack([(covariate == 3.5) * 1.0, (covariate == 1.5) * 1.0])

    info = _bin(
        tmp_path,
        fluo=fluo,
        behavior=_behavior(covariate),
        params={"sort_by_peak": True},
    )

    assert info["mean_heatmap"].index == [1, 0]


def test_heatmap_rows_are_cell_numbers_with_iscell(tmp_path):
    covariate = _covariate()
    fluo = np.stack([(covariate == c) * 1.0 for c in [3.5, 2.5, 0.5, 1.5]])

    info = _bin(
        tmp_path,
        fluo=fluo,
        iscell=IscellData(np.array([0, 1, 1, 1])),
        params={"sort_by_peak": True},
    )

    assert info["mean"].data.shape == (3, 4)
    assert info["mean_heatmap"].index == [2, 3, 1]


@pytest.mark.parametrize("length", [NUM_CELL - 1, NUM_CELL + 1])
def test_iscell_of_the_wrong_length_fails_with_a_clear_message(tmp_path, length):
    with pytest.raises(AssertionError, match=f"iscell has {length} entries"):
        _bin(tmp_path, iscell=IscellData(np.ones(length)))


def test_nan_fluorescence_blanks_only_that_cell_and_bin(tmp_path):
    fluo = _fluo()
    fluo[0, 0] = np.nan  # covariate 0.5, so bin 0

    mean = _bin(tmp_path, fluo=fluo)["mean"].data

    assert np.isnan(mean[0, 0])
    assert np.isfinite(mean[0, 1:]).all() and np.isfinite(mean[1:]).all()


def test_bin_centres_carry_no_float_noise(tmp_path):
    info = _bin(tmp_path, params={"bin_max": 1.2})

    assert info["mean"].index == [0.15, 0.45, 0.75, 1.05]


def test_heatmap_is_normalised_per_cell(tmp_path):
    fluo = _fluo()
    fluo[1] = 0.7
    heatmap = _bin(tmp_path, fluo=fluo)["mean_heatmap"].data

    assert (heatmap[1] == 0).all()
    assert heatmap[0].min() == 0 and heatmap[0].max() == 1


def test_empty_and_single_sample_bins_are_nan_and_logged(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="optinist")
    covariate = np.array([0.5, 0.5, 2.5])
    fluo = np.ones((NUM_CELL, 3))

    out = _postprocess(_bin(tmp_path, fluo=fluo, behavior=_behavior(covariate)))

    assert np.isnan(out["mean"][:, [1, 3]]).all()
    assert np.isnan(out["std"][:, 2]).all() and np.isnan(out["sem"][:, 2]).all()
    assert "2 of 4 bins have no samples: [1, 3]" in caplog.text


def test_empty_bins_serialise_as_null_for_the_heatmap(tmp_path):
    covariate = np.array([0.5, 0.5, 2.5])
    heatmap = _bin(
        tmp_path, fluo=np.ones((NUM_CELL, 3)), behavior=_behavior(covariate)
    )["mean_heatmap"]

    heatmap.save_json(str(tmp_path))

    rows = json.loads(open(heatmap.json_path).read())["data"]
    assert [row[1] for row in rows] == [None] * NUM_CELL


def test_1d_behaviour_is_one_column(tmp_path):
    covariate = _covariate()
    info = covariate_binning(
        FluoData(_fluo(), file_name="f"),
        IscellData(covariate),
        str(tmp_path / "default" / "uid" / "covariate_binning_1"),
        params={**PARAMS, "event_col_index": 0},
    )

    assert list(_postprocess(info)["num_sample"]) == [NUM_FRAME // 4] * 4


def test_data_range_ignores_infinite_samples(tmp_path):
    covariate = np.array([0.0, 1.0, 2.0, 3.0, 4.0, np.inf, -np.inf, np.nan])
    fluo = np.arange(len(covariate), dtype=float)[np.newaxis, :]

    info = _bin(
        tmp_path,
        fluo=fluo,
        behavior=_behavior(covariate),
        params={"use_data_range": True},
    )

    assert list(info["mean"].index) == [0.5, 1.5, 2.5, 3.5]
    assert list(_postprocess(info)["num_sample"]) == [1, 1, 1, 2]


def test_infinite_bin_bound_fails_with_a_clear_message(tmp_path):
    with pytest.raises(AssertionError, match="bin range must be finite"):
        _bin(tmp_path, params={"bin_max": float("inf")})


def test_out_of_range_column_fails_with_a_clear_message(tmp_path):
    with pytest.raises(AssertionError, match="event_col_index 2 is out of range"):
        _bin(tmp_path, params={"event_col_index": 2})


def test_no_sample_in_range_fails_with_a_clear_message(tmp_path):
    with pytest.raises(AssertionError, match="no sample of behaviour column 1"):
        _bin(tmp_path, params={"bin_min": 10.0, "bin_max": 20.0})


def test_empty_range_fails_with_a_clear_message(tmp_path):
    with pytest.raises(AssertionError, match="bin range is empty"):
        _bin(tmp_path, params={"bin_min": 1.0, "bin_max": 1.0})


def test_nested_params_are_flattened(tmp_path):
    nested = {
        "I/O": {"transpose_x": True, "transpose_y": False, "event_col_index": 1},
        "covariate_binning": {
            k: PARAMS[k]
            for k in ("n_bins", "use_data_range", "bin_min", "bin_max", "sort_by_peak")
        },
    }
    info = covariate_binning(
        FluoData(_fluo(), file_name="f"),
        BehaviorData(_behavior(_covariate()), file_name="b"),
        str(tmp_path / "default" / "uid" / "covariate_binning_1"),
        params=nested,
    )

    assert info["mean"].data.shape == (NUM_CELL, 4)
