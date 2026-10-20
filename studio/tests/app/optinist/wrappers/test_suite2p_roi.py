import sys
import types

import numpy as np
import pytest
import tifffile

from studio.app.optinist.dataclass import Suite2pData
from studio.app.optinist.wrappers.suite2p.roi import suite2p_roi

LY = LX = 8
FRAMES = 4
CENTERS = [(2, 2), (5, 5)]


class FakeROI:
    def __init__(self, ypix, xpix, lam, med, do_crop):
        self.ypix, self.xpix = ypix, xpix

    def to_array(self, Ly, Lx):
        array = np.zeros((Ly, Lx))
        array[self.ypix, self.xpix] = 1
        return array


def stub_suite2p(monkeypatch, label):
    stat = [
        {
            "ypix": np.array([cy, cy + 1]),
            "xpix": np.array([cx, cx]),
            "lam": np.ones(2),
            "med": [cy, cx],
        }
        for cy, cx in CENTERS
    ]
    n = len(stat)
    fake = types.ModuleType("suite2p")
    fake.default_ops = lambda: {"use_builtin_classifier": True}
    fake.ROI = FakeROI
    fake.detection = types.SimpleNamespace(detect=lambda ops, classfile: (ops, stat))
    fake.extraction = types.SimpleNamespace(
        create_masks_and_extract=lambda ops, stat: (
            ops,
            np.array(stat, dtype=object),
            np.ones((n, FRAMES)),
            np.zeros((n, FRAMES)),
            None,
            None,
        )
    )
    fake.classification = types.SimpleNamespace(
        builtin_classfile="builtin",
        user_classfile="user",
        classify=lambda stat, classfile: np.column_stack(
            [np.full(n, label), np.full(n, 0.5)]
        ),
    )
    monkeypatch.setitem(sys.modules, "suite2p", fake)


@pytest.mark.parametrize("label", [1, 0], ids=["all_cells", "no_cells"])
def test_an_empty_cell_or_non_cell_set_gives_an_all_nan_image(
    tmp_path, monkeypatch, label
):
    stub_suite2p(monkeypatch, label)
    movie = tmp_path / "movie.tif"
    tifffile.imwrite(
        movie, np.zeros((FRAMES, LY, LX), np.float32), photometric="minisblack"
    )
    output_dir = tmp_path / "output" / "default" / "uid" / "func"
    output_dir.mkdir(parents=True)
    ops = Suite2pData(
        {
            "Ly": LY,
            "Lx": LX,
            "nframes": FRAMES,
            "filelist": [str(movie)],
            "max_proj": np.zeros((LY, LX)),
            "Vcorr": np.zeros((LY, LX)),
        }
    )

    info = suite2p_roi(ops, str(output_dir), params={})

    # the stubs ran, so the wrapper did not fall back to its empty outputs
    assert list(info["iscell"].data) == [label] * len(CENTERS)
    filled, empty = (
        ("cell_roi", "non_cell_roi") if label else ("non_cell_roi", "cell_roi")
    )
    assert np.isnan(info[empty].data).all()
    np.testing.assert_array_equal(info[filled].data, info["all_roi"].data)
    assert np.nanmax(info["all_roi"].data) == len(CENTERS) - 1
