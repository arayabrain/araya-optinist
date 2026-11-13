import logging
import sys
import types

import numpy as np
import pytest

from studio.app.optinist.wrappers.caiman.cnmf import caiman_dff, dff_frames_window
from studio.app.optinist.wrappers.caiman.cnmf_multisession import session_dff

PIXELS = 4
FRAMES = 6
C = np.arange(3)[:, None] * 10.0 + np.arange(FRAMES)


def estimates(**overrides):
    fields = dict(
        A=np.ones((PIXELS, 3)),
        b=np.ones((PIXELS, 1)),
        C=C,
        f=np.ones((1, FRAMES)),
        YrA=np.ones((3, FRAMES)),
    )
    return types.SimpleNamespace(**{**fields, **overrides})


@pytest.fixture
def detrend(monkeypatch):
    """Stands in for caiman.source_extraction.cnmf.utilities, absent here."""
    stub = types.SimpleNamespace(calls=[], error=None)

    def detrend_df_f(A, b, C, f, **kwargs):
        stub.calls.append({"A": A, "b": b, "C": C, "f": f, **kwargs})
        if stub.error:
            raise stub.error
        return C + 0.5

    utilities = types.ModuleType("caiman.source_extraction.cnmf.utilities")
    utilities.detrend_df_f = detrend_df_f
    for name in ("caiman", "caiman.source_extraction", "caiman.source_extraction.cnmf"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, utilities.__name__, utilities)
    return stub


def test_caiman_inputs_and_params_are_passed_through(detrend):
    est = estimates()

    caiman_dff(est, [0, 1, 2], FRAMES, 500)

    (call,) = detrend.calls
    assert call["A"] is est.A and call["b"] is est.b and call["f"] is est.f
    assert call["C"] is C and call["YrA"] is est.YrA
    assert call["frames_window"] == 500 and "quantileMin" not in call
    assert call["flag_auto"] is True and call["use_fast"] is False


def test_a_null_frames_window_reaches_caiman(detrend):
    caiman_dff(estimates(), [0], FRAMES, dff_frames_window(None))

    assert detrend.calls[0]["frames_window"] is None


@pytest.mark.parametrize(
    "b, f", [(None, None), (np.zeros((PIXELS, 0)), np.zeros((0, FRAMES)))]
)
def test_the_ring_model_static_background_is_the_denominator(detrend, b, f):
    b0 = np.arange(PIXELS, dtype=float)

    caiman_dff(estimates(b=b, f=f, b0=b0), [0, 1], FRAMES, 500)

    call = detrend.calls[0]
    np.testing.assert_array_equal(call["b"], b0.reshape(-1, 1))
    np.testing.assert_array_equal(call["f"], np.ones((1, FRAMES)))


@pytest.mark.parametrize(
    "b, f", [(None, None), (np.zeros((PIXELS, 0)), np.zeros((0, FRAMES)))]
)
def test_no_background_at_all_is_logged_as_detrended_only(detrend, caplog, b, f):
    caplog.set_level(logging.WARNING, logger="optinist")

    caiman_dff(estimates(b=b, f=f), [0], FRAMES, 500)

    assert detrend.calls[0]["b"] is None and detrend.calls[0]["f"] is None
    assert "detrended only" in caplog.text


def test_rows_follow_the_fluorescence_order_as_float32(detrend):
    order = [2, 0, 1]

    dff = caiman_dff(estimates(), order, FRAMES, 500)

    assert dff.dtype == np.float32
    np.testing.assert_array_equal(dff, (C + 0.5)[order])


def test_a_session_with_no_match_gives_nan_not_another_cell(detrend):
    sessions = [estimates(), estimates(C=C + 100.0)]
    # register_multisession's NaN: union cell 0 is not in session 1
    assignments = np.array([[2, np.nan], [0, 1]])

    dff = session_dff(sessions, assignments, 500)

    assert dff.shape == (2, 2 * FRAMES) and dff.dtype == np.float32
    np.testing.assert_array_equal(dff[:, :FRAMES], (C + 0.5)[[2, 0]])
    assert np.isnan(dff[0, FRAMES:]).all()
    np.testing.assert_array_equal(dff[1, FRAMES:], C[1] + 100.5)


def test_zero_components_give_an_empty_row_block(detrend):
    dff = caiman_dff(estimates(), [], FRAMES, 500)

    assert dff.shape == (0, FRAMES)
    assert detrend.calls == []


def test_a_caiman_error_gives_all_nan_and_a_warning(detrend, caplog):
    caplog.set_level(logging.WARNING, logger="optinist")
    detrend.error = ValueError("operands could not be broadcast")

    dff = caiman_dff(estimates(), [1, 0], FRAMES, 500)

    assert dff.shape == (2, FRAMES) and dff.dtype == np.float32
    assert np.isnan(dff).all()
    (record,) = [r for r in caplog.records if "detrend_df_f failed" in r.message]
    assert record.exc_info[1] is detrend.error


@pytest.mark.parametrize("value, window", [(None, None), (500, 500), (2, 2), (8.0, 8)])
def test_frames_window_accepts_null_and_integers_of_at_least_two(value, window):
    assert dff_frames_window(value) == window


@pytest.mark.parametrize("value", [0, 1, -5, 500.5, "abc", "500", True, [500]])
def test_frames_window_rejects_anything_else_by_name(value):
    with pytest.raises(ValueError, match="frames_window"):
        dff_frames_window(value)
