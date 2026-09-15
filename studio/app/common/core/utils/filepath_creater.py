import os
import shutil

from studio.app.dir_path import DIRPATH


class InvalidPathError(ValueError):
    """A path built from request data would leave the directory it belongs to.

    A ValueError subclass so the snakemake rule processes, which import this
    module in conda environments without FastAPI, see an ordinary exception.
    __main_unit__ maps it to a 400 for requests.
    """


def join_filepath(path_list):
    """Join path parts, refusing anything that would leave the base directory.

    CONSTRAINT -- do not restructure the containment check.

        The startswith() must be the entire `if` condition.

            OK  if not normalized.startswith(prefix):

            NG  contained = normalized.startswith(prefix)
                if not contained:

            NG  if normalized != base_norm and not normalized.startswith(p):

        CodeQL's Path::SafeAccessCheck binds its barrier to the guard node, so
        either NG form reads as no sanitizer and py/path-injection goes from 0
        back to 151 across the whole codebase. Both were measured; both are
        behaviourally identical to the OK form.

        Nothing local catches this -- not a test, not a linter. Only the
        CodeQL job, which is not a required check.
    """
    if isinstance(path_list, str):
        joined = path_list
        base = path_list
    elif isinstance(path_list, list):
        if not path_list:
            raise InvalidPathError("path list is empty")
        joined = "/".join(path_list)
        # A leading empty element comes from splitting an absolute path
        # ("/a/b".split("/") -> ["", "a", "b"]); its base is the root.
        base = path_list[0] or os.sep
    else:
        assert False, "Path is not list"

    # Reject ".." outright rather than merely containing it: a segment that
    # normalises back inside the base still crosses workspaces, as
    # [OUTPUT_DIR, "1", "../other/expt"] does. Both separators, because
    # normpath treats "\\" as one on Windows and dir_path.py still builds
    # Windows roots -- a slash-only check would miss "..\\other".
    if ".." in joined.replace("\\", "/").split("/"):
        raise InvalidPathError(f"path contains '..': {joined!r}")

    # Separator on both sides, so a sibling sharing a name prefix is not read
    # as contained: ".../output_evil" against a ".../output" base.
    #   relative base -> "" (normpath drops the "./", so "." never matches)
    #   absolute base -> base + separator
    base_norm = os.path.normpath(base)
    prefix = "" if base_norm == os.curdir else base_norm.rstrip(os.sep) + os.sep

    normalized = os.path.normpath(joined) + os.sep
    if not normalized.startswith(prefix):
        # Defence in depth: "/".join never resets the base, and ".." is
        # refused above, so no input reaches here today.
        raise InvalidPathError(f"path escapes its base directory: {joined!r}")

    return normalized[: -len(os.sep)]


def create_filepath(dirname, filename):
    create_directory(dirname)

    return join_filepath([dirname, filename])


def get_pickle_file(workspace_id, unique_id, node_id, algo_name):
    return join_filepath([workspace_id, unique_id, node_id, f"{algo_name}.pkl"])


def create_directory(dirpath, delete_dir=False):
    if delete_dir and os.path.exists(dirpath):
        try:
            shutil.rmtree(dirpath)
        except FileNotFoundError:
            # Handle race condition where directory/files are deleted
            # by another process or due to filesystem delays
            pass

    os.makedirs(dirpath, exist_ok=True)


def normalize_output_path(path: str) -> str:
    """
    Convert absolute output path to relative path.

    Handles paths like:
    - /tmp/studio/output/93/tutorial1/... → 93/tutorial1/...
    - /app/studio_data/output/93/tutorial1/... → 93/tutorial1/...

    Args:
        path: The path to normalize (may be absolute or relative)

    Returns:
        Relative path without output directory prefix
    """
    if not path:
        return path

    # Strip OUTPUT_DIR prefix if present
    if path.startswith(DIRPATH.OUTPUT_DIR):
        return path[len(DIRPATH.OUTPUT_DIR) :].lstrip("/")

    return path


def resolve_absolute_output_path(filepath: str) -> str:
    """Normalize and convert to absolute path for filesystem operations."""
    filepath = normalize_output_path(filepath)
    return join_filepath([DIRPATH.OUTPUT_DIR, filepath])
