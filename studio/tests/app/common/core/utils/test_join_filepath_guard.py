"""join_filepath is the single chokepoint every request path passes through.

Guarding it there rather than at each router means a new endpoint cannot
forget the check. These tests pin what it accepts, what it refuses, and the
string it returns -- 185 call sites depend on the last one.
"""
import os

import pytest

from studio.app.common.core.utils.filepath_creater import (
    InvalidPathError,
    join_filepath,
)
from studio.app.dir_path import DIRPATH

OUT = DIRPATH.OUTPUT_DIR


def test_plain_join_is_unchanged():
    assert join_filepath([OUT, "1", "abc123"]) == f"{OUT}/1/abc123"


def test_relative_join_stays_relative():
    """Callers build workspace-relative keys as well as absolute paths."""
    assert join_filepath(["1", "uid", "node", "x.pkl"]) == "1/uid/node/x.pkl"


def test_single_string_passes_through():
    assert join_filepath(f"{OUT}/1/uid") == f"{OUT}/1/uid"


def test_absolute_path_split_into_parts_is_rebuilt():
    """PickleWriter passes `path.split("/")[:-1]`, whose first element is the
    empty string left by the leading separator."""
    parts = f"{OUT}/1/uid/node/x.pkl".split("/")[:-1]
    assert parts[0] == ""
    assert join_filepath(parts) == f"{OUT}/1/uid/node"


def test_nested_segments_are_allowed():
    """The input tree has subdirectories; only traversal is refused."""
    assert join_filepath([OUT, "1", "a/b/c.json"]) == f"{OUT}/1/a/b/c.json"


@pytest.mark.parametrize(
    "parts",
    [
        [OUT, "1", "../../etc/passwd"],
        [OUT, "..", "etc"],
        [OUT, "1/../../etc", "x"],
        ["1", "..", "2"],
    ],
)
def test_traversal_out_of_the_base_is_refused(parts):
    with pytest.raises(InvalidPathError):
        join_filepath(parts)


@pytest.mark.parametrize(
    "parts",
    [
        ["C:\\temp\\studio\\output", "..\\output_evil\\secret"],
        [OUT, "1", "..\\..\\etc"],
        [OUT, "1", "sub\\..\\..\\etc"],
    ],
)
def test_backslash_traversal_is_refused(parts):
    """normpath treats "\\" as a separator on Windows, and dir_path.py still
    builds Windows roots, so a slash-only check would let these normalise out
    of the base there."""
    with pytest.raises(InvalidPathError):
        join_filepath(parts)


def test_the_base_itself_is_contained():
    """A single-element list is the base. The containment check appends a
    separator to both sides, which must not turn that into an escape."""
    assert join_filepath([OUT]) == OUT
    assert join_filepath(f"{OUT}/1") == f"{OUT}/1"


# Not tested, deliberately: the containment check compares on a separator
# boundary, so ".../output" cannot accept ".../output_evil". No input reaches
# that branch today -- "/".join means the joined string always begins with
# path_list[0] verbatim, and the only thing that breaks it is "..", refused
# above. It is defence in depth against base becoming a trusted root rather
# than the caller's first element, and a test asserting otherwise would pass
# with the boundary removed.


def test_sideways_move_into_another_workspace_is_refused():
    """`../other` normalises back inside OUTPUT_DIR, so containment alone
    would allow it. It still leaves workspace 1, which is why ".." is refused
    outright rather than merely contained."""
    with pytest.raises(InvalidPathError):
        join_filepath([OUT, "1", "../other/expt"])


def test_absolute_component_cannot_reset_the_base():
    """ "/".join, not os.path.join: an absolute second component is appended
    rather than replacing what came before."""
    assert join_filepath([OUT, "/etc/passwd"]) == f"{OUT}/etc/passwd"


def test_result_is_normalised():
    assert join_filepath([OUT, "", "1", "uid"]) == f"{OUT}/1/uid"
    assert join_filepath([OUT, ".", "1"]) == f"{OUT}/1"


@pytest.mark.parametrize(
    "parts,expected",
    [
        ([], None),  # InvalidPathError -- see the test below
        ([OUT, ""], OUT),  # a trailing empty element loses its separator
        ([".", "output", "1"], "output/1"),  # a "." base is not an escape
    ],
)
def test_boundary_shapes_return_what_callers_now_get(parts, expected):
    """These return values changed with the guard; nothing else pins them."""
    if expected is None:
        with pytest.raises(InvalidPathError):
            join_filepath(parts)
    else:
        assert join_filepath(parts) == expected


def test_empty_list_is_refused_rather_than_asserted():
    """An `assert` would vanish under `python -O` and the next line indexes
    path_list, so the guard would raise IndexError from inside itself."""
    with pytest.raises(InvalidPathError):
        join_filepath([])


def test_foreign_absolute_prefix_is_appended_not_refused():
    """A stored config written under a different OUTPUT_DIR keeps its prefix:
    normalize_output_path cannot strip it and it contains no "..", so the
    guard passes it through. The read then fails exactly as it did before --
    this guard does not turn those records into 400s."""
    foreign = "/tmp/optinist/output/1/x.json"
    assert join_filepath([OUT, foreign]) == f"{OUT}{foreign}"


def test_invalid_path_error_is_a_value_error():
    """The snakemake rule processes import this module in conda environments
    without FastAPI, so the exception must not depend on it."""
    assert issubclass(InvalidPathError, ValueError)


def test_guard_matches_what_the_filesystem_would_do():
    """A sanity check that the accepted form is the path callers then open."""
    built = join_filepath([OUT, "1", "uid", "f.json"])
    assert built == os.path.normpath(built)
