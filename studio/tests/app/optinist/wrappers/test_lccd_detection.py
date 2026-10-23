"""lccd_detect validates parameters against the movie's shape before running
the algorithm, so a short movie with default settings fails fast with one
actionable error instead of an opaque crash deep in the pipeline.
"""
import sys
import types

import numpy as np
import pytest
import scipy.sparse

from studio.app.common.core.workflow.workflow_params import read_default_params
from studio.app.common.dataclass import ImageData
from studio.app.optinist.wrappers.lccd import lccd_detection
from studio.app.optinist.wrappers.lccd.lccd_detection import (
    lccd_config,
    lccd_detect,
    validate_params,
)
from studio.app.optinist.wrappers.lccd.lccd_python.roi_integration import (
    filter_roi_by_area,
)

ROWS, COLS, N_TIME = 16, 12, 40
SHAPE = (ROWS, COLS, N_TIME)


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
    frames = np.random.default_rng(0).random((N_TIME, ROWS, COLS)).astype(np.float32)
    return ImageData(frames, output_dir=str(tmp_path), file_name="img")


@pytest.fixture
def lccd_stub(monkeypatch):
    """Three fixed ROIs; records the config LCCD is built with."""
    configs = []

    class FakeLCCD:
        def __init__(self, config):
            configs.append(config)

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
    return configs


def test_default_params_on_short_movie_fail():
    with pytest.raises(ValueError, match="frame_divider \\(100\\) is larger"):
        validate_params(read_default_params("lccd_cell_detection"), SHAPE)


def test_default_params_valid_from_100_frames(caplog):
    params = read_default_params("lccd_cell_detection")
    with caplog.at_level("WARNING"):
        validate_params(params, (512, 512, 100))
    assert "dff will be entirely NaN" in caplog.text

    with pytest.raises(ValueError, match="set frame_divider <= 99"):
        validate_params(params, (512, 512, 99))


def test_valid_params_pass_without_warnings(caplog):
    with caplog.at_level("WARNING"):
        validate_params(valid_params(), SHAPE)
    assert not caplog.records


def test_none_params_pass():
    validate_params(None, SHAPE)


@pytest.mark.parametrize(
    "section, key, value, expected",
    [
        ("lccd", "frame_divider", 0, "frame_divider must be >= 1"),
        ("lccd", "frame_divider", 41, "frame_divider (41) is larger"),
        ("blob_detector", "filtersize1", 0, "filtersize1 must be >= 1"),
        ("blob_detector", "filtersize2", 0, "filtersize2 must be >= 1"),
        ("blob_detector", "filtersize2", 10, "must be < filtersize1"),
        ("blob_detector", "sigma", 0, "sigma must be > 0"),
        ("blob_detector", "fsize", 0, "fsize (0) must be between 1"),
        ("blob_detector", "fsize", 13, "shorter side (12 pixels)"),
        ("roi_integration", "overlap_threshold", -0.1, "between 0 and 1"),
        ("roi_integration", "overlap_threshold", 1.1, "between 0 and 1"),
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
        (
            "blob_detector",
            "min_area",
            True,
            "blob_detector.min_area must be a finite number",
        ),
        (
            "roi_integration",
            "min_area",
            16,
            "roi_integration.min_area (16) must be < roi_integration.max_area (16)",
        ),
    ],
)
def test_single_rule_violations(section, key, value, expected):
    params = valid_params()
    params[section][key] = value
    with pytest.raises(ValueError, match="invalid LCCD parameters") as exc:
        validate_params(params, SHAPE)
    assert expected in str(exc.value)


@pytest.mark.parametrize(
    "section, key, value",
    [
        ("roi_integration", "overlap_threshold", 0),
        ("roi_integration", "overlap_threshold", 1),
        ("blob_detector", "max_area", ROWS * COLS * 10),
        ("blob_detector", "min_area", 4.5),
        ("lccd", "frame_divider", N_TIME),
        ("blob_detector", "sigma", 10**400),
    ],
)
def test_values_that_run_are_accepted(section, key, value):
    params = valid_params()
    params[section][key] = value
    validate_params(params, SHAPE)


def test_filtersize1_larger_than_frame_divider_warns_only(caplog):
    params = valid_params()
    params["blob_detector"]["filtersize1"] = 25
    with caplog.at_level("WARNING"):
        validate_params(params, SHAPE)
    assert "filtersize1 (25) is larger than frame_divider (10)" in caplog.text


def test_flat_params_report_an_area_error_once():
    with pytest.raises(ValueError) as exc:
        validate_params({"min_area": 16, "max_area": 16}, SHAPE)
    assert str(exc.value).count("min_area (16) must be < max_area (16)") == 1


def test_even_fsize_warns_only(caplog):
    params = valid_params()
    params["blob_detector"]["fsize"] = 8
    with caplog.at_level("WARNING"):
        validate_params(params, SHAPE)
    assert "odd size is recommended" in caplog.text


@pytest.mark.parametrize("f0_frames, warns", [(19, False), (20, True), (100, True)])
def test_all_nan_dff_warning_boundary(caplog, f0_frames, warns):
    params = valid_params()
    params["dff"]["f0_frames"] = f0_frames
    with caplog.at_level("WARNING"):
        validate_params(params, SHAPE)
    assert ("dff will be entirely NaN" in caplog.text) == warns


def test_each_stage_keeps_its_own_section():
    params = valid_params()
    params["blob_detector"]["max_area"] = 32
    params["roi_integration"]["sparse"] = True
    config = lccd_config(params)
    assert config["blob_detector"]["max_area"] == 32
    assert config["blob_detector"]["sparse"] is False
    assert config["roi_integration"]["max_area"] == 16
    assert config["roi_integration"]["sparse"] is True
    assert config["frame_divider"] == 10


def test_flat_params_feed_both_stages():
    config = lccd_config({"min_area": 4, "max_area": 16, "frame_divider": 10})
    for stage in ("blob_detector", "roi_integration"):
        assert config[stage]["max_area"] == 16


def test_lccd_detect_fails_before_reading_the_movie(images, output_dir, monkeypatch):
    def no_read(*args):
        raise AssertionError("LoadData ran")

    monkeypatch.setattr(lccd_detection, "LoadData", no_read)
    params = read_default_params("lccd_cell_detection")
    with pytest.raises(ValueError, match="invalid LCCD parameters"):
        lccd_detect(images, output_dir, params=params)


def test_lccd_detect_builds_lccd_per_stage(images, output_dir, lccd_stub):
    params = valid_params()
    params["blob_detector"]["max_area"] = 32
    lccd_detect(images, output_dir, params=params)
    [config] = lccd_stub
    assert config["blob_detector"]["max_area"] == 32
    assert config["roi_integration"]["max_area"] == 16


def test_lccd_detect_runs_on_non_square_frames(images, output_dir, lccd_stub):
    info = lccd_detect(images, output_dir, params=valid_params())
    assert info["fluorescence"].data.shape == (3, N_TIME)
    assert info["edit_roi_data"].im.shape == (3, ROWS, COLS)


@pytest.mark.parametrize("sparse", [False, True])
def test_filter_roi_by_area_returns_empty_when_all_filtered(sparse):
    roi = np.ones((16, 2))
    if sparse:
        roi = scipy.sparse.csc_matrix(roi)
    assert filter_roi_by_area(roi, 1, 4).shape == (16, 0)
