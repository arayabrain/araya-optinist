"""MAT variables take the same transpose and behaviour role settings as HDF5
datasets. pymatreader squeezes a saved column vector to 1D, so only the 2D
cases differ from rank alone."""

import numpy as np
import pytest
import scipy.io

from studio.app.common.core.rules.file_writer import FileWriter
from studio.tests.app.common.core.rules.test_file_writer_hdf5 import (
    LOAD_CASES,
    _matrule,
    assert_loaded,
)


@pytest.mark.parametrize("case", LOAD_CASES.values(), ids=LOAD_CASES.keys())
def test_input_transpose_and_behaviour_role(tmp_path, case):
    stored = case[0]
    mat = tmp_path / "in.mat"
    scipy.io.savemat(mat, {"x": stored[:, np.newaxis] if stored.ndim == 1 else stored})

    info = FileWriter.mat(_matrule(tmp_path, mat, "x", params=case[1]))

    assert_loaded(info, case)
