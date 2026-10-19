import numpy as np
from scipy.sparse import csc_matrix

from studio.app.optinist.wrappers.caiman.cnmf_multisession import union_footprints

PIXELS = 8


def test_each_union_cell_takes_its_first_matched_session_footprint():
    pixel = np.eye(PIXELS)
    sessions = [
        csc_matrix(pixel[:, 0:3]),
        csc_matrix(2 * pixel[:, 3:5]),
        csc_matrix(3 * pixel[:, 5:7]),
    ]
    # register_multisession's NaN: no match for that union cell in that session
    assignments = np.array([[2, np.nan, 0], [np.nan, 1, 1], [np.nan, np.nan, 0]])

    footprints = union_footprints(sessions, assignments)

    assert footprints.format == "csc"
    np.testing.assert_array_equal(
        footprints.toarray(),
        np.column_stack([pixel[:, 2], 2 * pixel[:, 4], 3 * pixel[:, 5]]),
    )
