"""Contracts around the two identifiers derived by hashing.

Both functions had no coverage at all, and the one thing here that can break
silently is the agreement between the writer and the reader of a client_id:
logging_middleware stamps `generate_client_id(uid)` into each log line, and
log_reader re-derives it to filter by user. If the two ever diverge -- a
partial revert, a truncation-width change -- user-scoped log search returns
zero results with no error anywhere.

The assertions are deliberately algorithm-agnostic so they survive the next
change to either function.
"""
import os

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.utils.filelock_handler import FileLockUtils
from studio.app.common.core.utils.log_reader import LogLevel, LogRecordReader
from studio.app.dir_path import DIRPATH

CLIENT_ID_LENGTH = 16


def test_client_id_is_the_width_the_log_format_assumes():
    assert len(AppLogger.generate_client_id("uid-x")) == CLIENT_ID_LENGTH


def test_client_id_is_deterministic():
    assert AppLogger.generate_client_id("uid-x") == AppLogger.generate_client_id(
        "uid-x"
    )


def test_client_id_separates_users():
    assert AppLogger.generate_client_id("a") != AppLogger.generate_client_id("b")


def test_an_empty_uid_is_returned_unchanged():
    """Anonymous requests reach the middleware with no uid; the early return
    keeps them out of the hash rather than giving them all one shared id."""
    assert AppLogger.generate_client_id("") == ""


def test_the_log_reader_filter_matches_what_the_middleware_stamps():
    """The invariant this file exists for. logging_middleware writes
    generate_client_id(uid) into the line; LogRecordReader re-derives it to
    match against those lines."""
    reader = LogRecordReader(levels=[LogLevel.ALL], filter_user_id="uid-x")
    assert reader.filter_client_id == AppLogger.generate_client_id("uid-x").encode()


def test_no_filter_means_no_client_id():
    reader = LogRecordReader(levels=[LogLevel.ALL])
    assert reader.filter_client_id is None


def test_same_named_files_in_different_directories_get_different_locks():
    """The reason the path is hashed at all: every experiment has an
    experiment.yaml, and they must not serialise against each other."""
    a = FileLockUtils.get_lockfile_path("/a/dir1/experiment.yaml")
    b = FileLockUtils.get_lockfile_path("/a/dir2/experiment.yaml")
    assert a != b


def test_the_lockfile_name_keeps_the_basename_readable():
    path = FileLockUtils.get_lockfile_path("/a/dir1/experiment.yaml")
    assert path.endswith("_experiment.yaml.lock")
    assert os.path.dirname(path) == DIRPATH.LOCKFILE_DIR


def test_the_lockfile_path_is_deterministic():
    assert FileLockUtils.get_lockfile_path(
        "/a/dir1/experiment.yaml"
    ) == FileLockUtils.get_lockfile_path("/a/dir1/experiment.yaml")
