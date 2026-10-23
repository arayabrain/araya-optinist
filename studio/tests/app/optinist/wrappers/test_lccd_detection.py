"""lccd_detect validates parameters against the video dimensions before running
the algorithm, so a short video with default settings fails fast with one
actionable error instead of an opaque crash deep in the pipeline.
"""
import sys
import types

import numpy as np
import pytest

from studio.app.common.core.workflow.workflow_params import read_default_params
from studio.app.common.dataclass import ImageData
from studio.app.optinist.wrappers.lccd import lccd_detection
from studio.app.optinist.wrappers.lccd.lccd_detection import (
    lccd_detect,
    validate_params,
)
from studio.app.optinist.wrappers.lccd.lccd_python.roi_integration import (
    filter_roi_by_area,
)

H, W, N_TIME = 8, 8, 40
SHAPE = (H, W, N_TIME)  # (X, Y, T), LoadData's convention


def valid_params():
    return {
        "blob_detector": {
            "filtersize1": 10,
            "filtersize2": 4,
            "sigma": 1.25,
            "fsize": 9,
            "min_area": 4,
            "max_area": 16,
            "sparse": False,
        },
        "roi_integration": {
            "overlap_threshold": 0.25,
            "min_area": 4,
            "max_area": 16,
            "sparse": False,
        },
        "lccd": {"frame_divider": 10},
        "dff": {"f0_frames": 5, "f0_percentile": 8},
    }


@pytest.fixture
def output_dir(tmp_path):
    d = tmp_path / "output" / "default" / "uid" / "func"
    d.mkdir(parents=True)
    return str(d)


@pytest.fixture
def images(tmp_path):
    frames = np.random.default_rng(0).random((N_TIME, H, W)).astype(np.float32)
    return ImageData(frames, output_dir=str(tmp_path), file_name="img")


@pytest.fixture
def lccd_stub(monkeypatch):
    class FakeLCCD:
        def __init__(self, params):
            pass

        def apply(self, D):
            roi = np.zeros((D.shape[0] * D.shape[1], 3))
            for i in range(3):
                roi[i * 4 : (i + 1) * 4, i] = 1
            return roi

    fake = types.ModuleType("lccd")
    fake.LCCD = FakeLCCD
    monkeypatch.setitem(
        sys.modules, f"{lccd_detection.__package__}.lccd_python.lccd", fake
    )


def test_default_params_on_short_video_list_every_violation():
    params = read_default_params("lccd_cell_detection")
    with pytest.raises(ValueError) as exc:
        validate_params(params, SHAPE)
    message = str(exc.value)
    for key in ("frame_divider", "filtersize1", "max_area"):
        assert key in message


def test_valid_params_pass_without_warnings(caplog):
    with caplog.at_level("WARNING"):
        validate_params(valid_params(), SHAPE)
    assert not caplog.records


def test_none_params_pass():
    validate_params(None, SHAPE)


@pytest.mark.parametrize("frames, warns", [(299, True), (300, False)])
def test_single_chunk_warning_boundary(caplog, frames, warns):
    params = read_default_params("lccd_cell_detection")
    with caplog.at_level("WARNING"):
        validate_params(params, (512, 512, frames))
    assert ("ROI integration never runs" in caplog.text) == warns


def test_default_params_valid_from_200_frames(caplog):
    params = read_default_params("lccd_cell_detection")
    with caplog.at_level("WARNING"):
        validate_params(params, (512, 512, 200))
    assert "dff will be entirely NaN" in caplog.text
    assert "ROI integration never runs" in caplog.text

    with pytest.raises(ValueError) as exc:
        validate_params(params, (512, 512, 199))
    assert "frame_divider" in str(exc.value)
    assert "filtersize1" in str(exc.value)


@pytest.mark.parametrize(
    "section, key, value, expected",
    [
        ("lccd", "frame_divider", 0, "frame_divider must be >= 1"),
        ("lccd", "frame_divider", 30, "frame_divider (30) is too large"),
        ("blob_detector", "filtersize1", 0, "filtersize1 must be >= 1"),
        ("blob_detector", "filtersize1", 21, "must be <= Time / 2"),
        ("blob_detector", "filtersize2", 10, "must be < filtersize1"),
        ("blob_detector", "sigma", 0, "sigma must be > 0"),
        ("roi_integration", "overlap_threshold", 0, "between 0 and 1"),
        ("roi_integration", "overlap_threshold", 1, "between 0 and 1"),
        ("dff", "f0_frames", 0, "f0_frames must be >= 1"),
        ("dff", "f0_percentile", -1, "between 0 and 100"),
        ("dff", "f0_percentile", 101, "between 0 and 100"),
        ("lccd", "frame_divider", 10.5, "must be an integer"),
        ("blob_detector", "filtersize1", 10.5, "must be an integer"),
        ("dff", "f0_frames", "100", "must be an integer"),
        ("lccd", "frame_divider", None, "must be an integer"),
        ("blob_detector", "sigma", "1.25", "sigma must be a finite number"),
        ("blob_detector", "sigma", float("nan"), "sigma must be a finite number"),
        ("roi_integration", "overlap_threshold", True, "must be a finite number"),
    ],
)
def test_single_rule_violations(section, key, value, expected):
    params = valid_params()
    params[section][key] = value
    with pytest.raises(ValueError, match="invalid LCCD parameters") as exc:
        validate_params(params, SHAPE)
    assert expected in str(exc.value)


def test_filtersize1_larger_than_frame_divider_warns_only(caplog):
    params = valid_params()
    params["blob_detector"]["filtersize1"] = 15
    with caplog.at_level("WARNING"):
        validate_params(params, SHAPE)
    assert "filtersize1 (15) is larger than frame_divider (10)" in caplog.text


def test_min_area_not_below_max_area_rejected():
    params = valid_params()
    for section in ("blob_detector", "roi_integration"):
        params[section]["min_area"] = 16
        params[section]["max_area"] = 16
    with pytest.raises(ValueError) as exc:
        validate_params(params, SHAPE)
    assert "min_area (16) must be < max_area (16)" in str(exc.value)


def test_max_area_bounded_by_frame_pixels():
    params = valid_params()
    for section in ("blob_detector", "roi_integration"):
        params[section]["max_area"] = H * W
    with pytest.raises(ValueError) as exc:
        validate_params(params, SHAPE)
    assert "max_area (64) must be < X * Y" in str(exc.value)


def test_mismatched_shared_section_values_warn_which_value_applies(caplog):
    params = valid_params()
    params["blob_detector"]["min_area"] = 8
    with caplog.at_level("WARNING"):
        validate_params(params, SHAPE)
    assert "blob_detector.min_area (8) and roi_integration.min_area (4)" in caplog.text
    assert "uses 4 for both stages" in caplog.text


def test_mismatch_warning_reports_winner_for_reversed_section_order(caplog):
    params = valid_params()
    params["blob_detector"]["min_area"] = 8
    reversed_params = {
        "roi_integration": params["roi_integration"],
        "blob_detector": params["blob_detector"],
        "lccd": params["lccd"],
        "dff": params["dff"],
    }
    with caplog.at_level("WARNING"):
        validate_params(reversed_params, SHAPE)
    assert "uses 8 for both stages" in caplog.text


def test_boolean_rejected_for_integer_params():
    params = valid_params()
    for section in ("blob_detector", "roi_integration"):
        params[section]["min_area"] = True
    with pytest.raises(ValueError) as exc:
        validate_params(params, SHAPE)
    assert "min_area must be an integer, got True" in str(exc.value)


def test_even_fsize_warns_only(caplog):
    params = valid_params()
    params["blob_detector"]["fsize"] = 8
    with caplog.at_level("WARNING"):
        validate_params(params, SHAPE)
    assert "odd size is recommended" in caplog.text


@pytest.mark.parametrize("f0_frames", [20, 40, 100])
def test_all_nan_dff_warns_only(caplog, f0_frames):
    params = valid_params()
    params["dff"]["f0_frames"] = f0_frames
    with caplog.at_level("WARNING"):
        validate_params(params, SHAPE)
    assert "dff will be entirely NaN" in caplog.text


def test_lccd_detect_fails_fast_on_short_video_with_defaults(images, output_dir):
    params = read_default_params("lccd_cell_detection")
    with pytest.raises(ValueError, match="invalid LCCD parameters") as exc:
        lccd_detect(images, output_dir, params=params)
    assert "frame_divider" in str(exc.value)


def test_lccd_detect_warns_on_mismatched_sections(
    images, output_dir, lccd_stub, caplog
):
    params = valid_params()
    params["blob_detector"]["max_area"] = 32
    with caplog.at_level("WARNING"):
        info = lccd_detect(images, output_dir, params=params)
    assert "blob_detector.max_area (32) and roi_integration.max_area (16)" in (
        caplog.text
    )
    assert "uses 16 for both stages" in caplog.text
    assert info["fluorescence"].data.shape == (3, N_TIME)


def test_lccd_detect_runs_with_valid_nested_params(images, output_dir, lccd_stub):
    info = lccd_detect(images, output_dir, params=valid_params())
    assert info["fluorescence"].data.shape == (3, N_TIME)


def test_filter_roi_by_area_returns_empty_when_all_filtered():
    roi = np.ones((16, 2))
    assert filter_roi_by_area(roi, 1, 4).shape == (16, 0)
