import json
import logging

import numpy as np
import pytest

from studio.app.common.dataclass import HeatMapData
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


def _bin(tmp_path, fluo=None, behavior=None, params=None, iscell=None, order_from=None):
    return covariate_binning(
        FluoData(_fluo() if fluo is None else fluo, file_name="f"),
        BehaviorData(
            _behavior(_covariate()) if behavior is None else behavior, file_name="b"
        ),
        str(tmp_path / "default" / "uid" / "covariate_binning_1"),
        iscell=iscell,
        order_from=order_from,
        params={**PARAMS, **(params or {})},
    )


def _peaked_fluo(centres):
    covariate = _covariate()
    return np.stack([(covariate == c) * 1.0 for c in centres])


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


def test_order_from_overrides_sort_by_peak(tmp_path):
    reference = HeatMapData(np.zeros((3, 4)), index=[1, 2, 0])

    info = _bin(
        tmp_path,
        fluo=_peaked_fluo([3.5, 2.5, 0.5]),
        order_from=reference,
        params={"sort_by_peak": True},
    )

    assert info["mean_heatmap"].index == [1, 2, 0]
    assert list(np.argmax(info["mean_heatmap"].data, axis=1)) == [2, 0, 3]
    assert list(info["mean"].cell_numbers) == [0, 1, 2]


def test_order_from_matches_cell_numbers_under_iscell(tmp_path):
    reference = HeatMapData(np.zeros((4, 4)), index=[5, 1, 3, 2])

    info = _bin(
        tmp_path,
        fluo=_peaked_fluo([3.5, 2.5, 0.5, 1.5]),
        iscell=IscellData(np.array([0, 1, 1, 1])),
        order_from=reference,
        params={"sort_by_peak": True},
    )

    assert info["mean_heatmap"].index == [1, 3, 2]


def test_order_from_keeps_the_first_row_of_a_duplicate_label(tmp_path):
    reference = HeatMapData(np.zeros((3, 4)), index=[1, 0, 1])

    info = _bin(tmp_path, fluo=_peaked_fluo([0.5, 2.5]), order_from=reference)

    assert info["mean_heatmap"].index == [1, 0]


def test_order_from_missing_a_cell_fails_with_a_clear_message(tmp_path):
    reference = HeatMapData(np.zeros((2, 4)), index=[0, 2])

    with pytest.raises(AssertionError, match="no row for 1 of 3 cells"):
        _bin(tmp_path, order_from=reference)


def test_order_from_that_is_not_a_heatmap_fails_with_a_clear_message(tmp_path):
    # A 2D HDF5 dataset loads as FluoData and the validator lets it reach this port
    with pytest.raises(AssertionError, match="must be a heatmap, got FluoData"):
        _bin(tmp_path, order_from=FluoData(np.zeros((3, 4))))


def test_order_from_without_row_labels_fails_with_a_clear_message(tmp_path):
    with pytest.raises(AssertionError, match="no row labels"):
        _bin(tmp_path, order_from=HeatMapData(np.zeros((3, 4))))


def test_heatmap_normalization_mean_divides_by_the_cell_mean(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="optinist")
    covariate = _covariate()
    fluo = np.stack(
        [covariate, np.tile([1.0, -1.0], NUM_FRAME // 2), covariate + 1, covariate - 3]
    )

    info = _bin(tmp_path, fluo=fluo, params={"heatmap_normalization": "mean"})

    np.testing.assert_allclose(info["mean_heatmap"].data[0], [0.25, 0.75, 1.25, 1.75])
    assert np.isnan(info["mean_heatmap"].data[1]).all()
    np.testing.assert_allclose(info["mean_heatmap"].data[2], [0.5, 2.5 / 3, 7 / 6, 1.5])
    assert np.isnan(info["mean_heatmap"].data[3]).all()
    np.testing.assert_allclose(info["mean"].data[0], [0.5, 1.5, 2.5, 3.5])
    assert "blanking 2 cells: [1, 3]" in caplog.text


def test_heatmap_normalization_mean_uses_only_in_range_samples(tmp_path):
    covariate = np.tile([0.5, 1.5, 2.5, 3.5, 10.0, 10.0], NUM_FRAME // 6)
    fluo = np.where(covariate > 4, 100.0, covariate)[np.newaxis]

    info = _bin(
        tmp_path,
        fluo=fluo,
        behavior=_behavior(covariate),
        params={"heatmap_normalization": "mean"},
    )

    np.testing.assert_allclose(info["mean_heatmap"].data[0], [0.25, 0.75, 1.25, 1.75])


@pytest.mark.parametrize("normalization", ["minmax", "mean", "none", None])
def test_row_order_does_not_depend_on_heatmap_normalization(tmp_path, normalization):
    info = _bin(
        tmp_path,
        fluo=_peaked_fluo([3.5, 2.5, 0.5]) + 1,
        params={"sort_by_peak": True, "heatmap_normalization": normalization},
    )

    assert info["mean_heatmap"].index == [2, 1, 0]


def test_heatmap_normalization_none_plots_the_raw_mean(tmp_path):
    fluo = _fluo()
    info = _bin(tmp_path, fluo=fluo, params={"heatmap_normalization": "none"})

    np.testing.assert_array_equal(info["mean_heatmap"].data, info["mean"].data)


def test_unknown_heatmap_normalization_fails_with_a_clear_message(tmp_path):
    with pytest.raises(AssertionError, match="heatmap_normalization must be one of"):
        _bin(tmp_path, params={"heatmap_normalization": "max"})


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


def _trial_params(**extra):
    return {"trial_col_index": 2, **extra}


def _trial_behavior(samples_per_trial=12):
    covariate = _covariate()
    trial = np.arange(NUM_FRAME) // samples_per_trial
    return np.column_stack([np.zeros(NUM_FRAME), covariate, trial])


def test_trial_mode_averages_per_trial_then_across_trials(tmp_path):
    fluo = _fluo()
    behavior = _trial_behavior()

    info = _bin(tmp_path, fluo=fluo, behavior=behavior, params=_trial_params())
    out = _postprocess(info)

    covariate, trial = behavior[:, 1], behavior[:, 2]
    per_trial = np.array(
        [
            [
                fluo[:, (trial == t) & (covariate == c)].mean(axis=1)
                for c in _bin_centres()
            ]
            for t in range(10)
        ]
    )  # (trial, bin, cell)
    np.testing.assert_allclose(info["mean"].data, per_trial.mean(axis=0).T)
    np.testing.assert_allclose(out["std"], per_trial.std(axis=0, ddof=1).T)
    np.testing.assert_allclose(
        out["sem"], per_trial.std(axis=0, ddof=1).T / np.sqrt(10)
    )
    assert list(out["num_sample"]) == [10] * 4
    assert info["mean_heatmap"].meta.title == "n = 10 trials, 10 to 10 per bin"


def _bin_centres():
    return [0.5, 1.5, 2.5, 3.5]


def test_trial_mode_sem_is_over_trials_not_samples(tmp_path):
    behavior = _trial_behavior()
    fluo = np.tile(behavior[:, 2], (NUM_CELL, 1))  # each trial is flat at its id

    out = _postprocess(
        _bin(tmp_path, fluo=fluo, behavior=behavior, params=_trial_params())
    )

    np.testing.assert_allclose(out["std"], np.std(np.arange(10), ddof=1))
    np.testing.assert_allclose(out["sem"], np.std(np.arange(10), ddof=1) / np.sqrt(10))


def test_trial_mode_sem_counts_the_trials_the_cell_has(tmp_path):
    """A NaN sample drops that trial's bin for the cell; num_sample still counts
    the occupancy, so the sem must not divide by it."""
    behavior = _trial_behavior()
    fluo = np.tile(behavior[:, 2], (NUM_CELL, 1)).astype(float)
    in_trial_2_bin_0 = (behavior[:, 2] == 2) & (behavior[:, 1] == 0.5)
    fluo[0, np.flatnonzero(in_trial_2_bin_0)[0]] = np.nan

    out = _postprocess(
        _bin(tmp_path, fluo=fluo, behavior=behavior, params=_trial_params())
    )

    left = np.array([0, 1, 3, 4, 5, 6, 7, 8, 9], dtype=float)
    np.testing.assert_allclose(out["mean"][0, 0], left.mean())
    np.testing.assert_allclose(out["sem"][0, 0], left.std(ddof=1) / 3)
    np.testing.assert_allclose(
        out["sem"][1, 0], np.std(np.arange(10), ddof=1) / np.sqrt(10)
    )
    assert list(out["num_sample"]) == [10] * 4


def test_trial_mode_min_samples_drops_a_sparse_trial_bin(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="optinist")
    behavior = _trial_behavior()
    behavior[0, 1] = 10.0  # trial 0 keeps 2 of its 3 samples in bin 0
    fluo = np.ones((NUM_CELL, NUM_FRAME))
    fluo[:, :12] = 5.0  # trial 0 is the odd one out

    dense = _postprocess(
        _bin(
            tmp_path, fluo=fluo, behavior=behavior, params=_trial_params(min_samples=3)
        )
    )
    loose = _postprocess(
        _bin(
            tmp_path, fluo=fluo, behavior=behavior, params=_trial_params(min_samples=2)
        )
    )

    assert list(dense["num_sample"]) == [9, 10, 10, 10]
    np.testing.assert_allclose(dense["mean"][:, 0], 1.0)
    assert (
        "1 of 40 trial-bins with samples have fewer than min_samples 3" in caplog.text
    )
    assert "bins have fewer than min_samples" not in caplog.text
    assert list(loose["num_sample"]) == [10] * 4
    np.testing.assert_allclose(loose["mean"][:, 0], 1.4)


def test_trial_mode_ignores_samples_without_a_trial_id(tmp_path):
    behavior = _trial_behavior()
    behavior[:12, 2] = np.nan

    out = _postprocess(_bin(tmp_path, behavior=behavior, params=_trial_params()))

    assert list(out["num_sample"]) == [9] * 4


def test_trial_mode_nan_sample_blanks_that_trial_bin_only(tmp_path):
    behavior = _trial_behavior()
    fluo = np.ones((NUM_CELL, NUM_FRAME))
    fluo[0, 0] = np.nan  # cell 0, trial 0, bin 0

    out = _postprocess(
        _bin(tmp_path, fluo=fluo, behavior=behavior, params=_trial_params())
    )

    assert np.isfinite(out["mean"]).all()
    assert list(out["num_sample"]) == [10] * 4


def test_trial_col_out_of_range_fails_with_a_clear_message(tmp_path):
    with pytest.raises(AssertionError, match="trial_col_index 5 is out of range"):
        _bin(tmp_path, params={"trial_col_index": 5})


def _with_column(behavior, column):
    return np.column_stack([behavior, column])


def test_continuous_trial_column_fails_before_allocating(tmp_path):
    speed = np.random.default_rng(1).random(NUM_FRAME)
    behavior = _with_column(_trial_behavior(), speed)

    with pytest.raises(AssertionError) as error:
        _bin(tmp_path, behavior=behavior, params={"trial_col_index": 3})

    message = str(error.value)
    assert "trial_col_index 3 does not look like a trial id" in message
    assert "120 distinct values over 120 binned samples" in message
    assert message.endswith("Column 2 looks like one.")


def test_event_code_trial_column_fails_with_a_clear_message(tmp_path):
    lick = np.tile([0, 0, 1, 2, 0, 1], NUM_FRAME // 6)
    behavior = _with_column(_trial_behavior(), lick)

    with pytest.raises(
        AssertionError, match="its 3 values recur in 100 separate stretches of frames"
    ):
        _bin(tmp_path, behavior=behavior, params={"trial_col_index": 3})


def test_trial_column_with_a_between_trials_code_points_at_condition_split(tmp_path):
    behavior = _trial_behavior()
    trial = behavior[:, 2] + 1
    trial[trial % 2 == 0] = 0  # every other block of frames is between trials
    behavior[:, 2] = trial

    with pytest.raises(
        AssertionError,
        match="value 0 recurs in 5 separate stretches.*condition_split first.*"
        "No other column looks like one",
    ):
        _bin(tmp_path, behavior=behavior, params=_trial_params())


def test_min_samples_blanks_sparse_bins_in_sample_mode(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="optinist")
    covariate = np.array([0.5, 0.5, 2.5])
    fluo = np.ones((NUM_CELL, 3))

    out = _postprocess(
        _bin(
            tmp_path,
            fluo=fluo,
            behavior=_behavior(covariate),
            params={"min_samples": 2},
        )
    )

    np.testing.assert_allclose(out["mean"][:, 0], 1.0)
    assert np.isnan(out["mean"][:, 2]).all()
    assert list(out["num_sample"]) == [2, 0, 1, 0]
    assert "1 of 4 bins have fewer than min_samples 2 and are NaN: [2]" in caplog.text


def test_invalid_min_samples_fails_with_a_clear_message(tmp_path):
    with pytest.raises(AssertionError, match="min_samples must be >= 1"):
        _bin(tmp_path, params={"min_samples": 0})


def test_smoothing_changes_the_heatmap_not_the_mean(tmp_path):
    from scipy.ndimage import gaussian_filter1d

    fluo = _peaked_fluo([1.5, 2.5, 3.5])
    info = _bin(
        tmp_path,
        fluo=fluo,
        params={"smooth_sigma_bins": 1, "heatmap_normalization": "none"},
    )
    out = _postprocess(info)

    raw = np.eye(4)[1:]
    np.testing.assert_allclose(info["mean"].data, raw)
    np.testing.assert_allclose(out["mean"], raw)
    expected = gaussian_filter1d(raw, 1, axis=1, mode="constant") / gaussian_filter1d(
        np.ones_like(raw), 1, axis=1, mode="constant"
    )
    np.testing.assert_allclose(info["mean_heatmap"].data, expected)
    np.testing.assert_allclose(out["mean_smoothed"], expected)
    assert (
        info["mean_heatmap"].meta.title == "30 to 30 samples per bin, smoothed 1 bins"
    )


def test_smoothing_leaves_an_empty_bin_blank_and_its_neighbours_finite(tmp_path):
    covariate = np.tile([1.5, 2.5, 3.5], 40)
    fluo = np.stack([(covariate == 2.5) * 1.0])

    info = _bin(
        tmp_path,
        fluo=fluo,
        behavior=_behavior(covariate),
        params={"smooth_sigma_bins": 1, "heatmap_normalization": "none"},
    )

    row = info["mean_heatmap"].data[0]
    assert np.isnan(row[0])
    assert np.isfinite(row[1:]).all()
    assert row[2] == np.nanmax(row) > row[1] > 0


def test_sort_by_peak_follows_the_smoothed_row(tmp_path):
    covariate = _covariate()
    # cell 0 peaks in bin 3 with a wide shoulder in bins 0 and 1; smoothing moves it
    fluo = np.stack(
        [
            np.select(
                [covariate == 0.5, covariate == 1.5, covariate == 3.5], [0.9, 0.9, 1.0]
            ),
            (covariate == 2.5) * 1.0,
        ]
    )

    raw = _bin(tmp_path, fluo=fluo, params={"sort_by_peak": True})
    smooth = _bin(
        tmp_path, fluo=fluo, params={"sort_by_peak": True, "smooth_sigma_bins": 1}
    )

    assert raw["mean_heatmap"].index == [1, 0]
    assert smooth["mean_heatmap"].index == [0, 1]


def test_invalid_smoothing_fails_with_a_clear_message(tmp_path):
    with pytest.raises(AssertionError, match="smooth_sigma_bins must be >= 0"):
        _bin(tmp_path, params={"smooth_sigma_bins": -1})


def test_default_title_reports_samples_per_bin(tmp_path):
    info = _bin(tmp_path)
    assert info["mean_heatmap"].meta.title == "30 to 30 samples per bin"
