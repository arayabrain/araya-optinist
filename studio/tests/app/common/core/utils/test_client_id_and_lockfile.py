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

from studio.app.common.core import logger
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.utils.filelock_handler import FileLockUtils
from studio.app.common.core.utils.log_reader import LogLevel, LogRecordReader
from studio.app.dir_path import DIRPATH

CLIENT_ID_LENGTH = 16
LOCKFILE_HASH_LENGTH = 16


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


def test_auto_refresh_mixes_the_period_into_the_hash(monkeypatch):
    """The one branch that feeds generate_client_id a different hash_source.

    The rotation *period* is deliberately not asserted: the docstring says
    weekly while "%Y-%m-%d-%w" rotates daily, and which of the two is intended
    is still open. Whichever way that lands, the period has to reach the digest
    and the width has to stay what the log format assumes.
    """
    stamps = iter(["period-1", "period-2"])
    monkeypatch.setattr(
        logger, "get_current_datetime_formatted", lambda _: next(stamps)
    )

    first = AppLogger.generate_client_id("uid-x", auto_refresh=True)
    second = AppLogger.generate_client_id("uid-x", auto_refresh=True)

    assert first != second, "the period does not reach the digest"
    assert len(first) == CLIENT_ID_LENGTH
    assert first != AppLogger.generate_client_id("uid-x")


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


def test_the_lockfile_hash_is_the_width_the_name_format_assumes():
    """Nothing re-derives a lock name the way log_reader re-derives a
    client_id, so the width is a readability contract rather than a protocol
    one -- but the name format states it, so it is pinned here."""
    name = os.path.basename(FileLockUtils.get_lockfile_path("/a/dir1/experiment.yaml"))
    file_path_hash = name.split("_", 1)[0]

    assert len(file_path_hash) == LOCKFILE_HASH_LENGTH
    int(file_path_hash, 16)  # hex, so the name stays filesystem-safe


def test_the_lockfile_path_is_deterministic():
    assert FileLockUtils.get_lockfile_path(
        "/a/dir1/experiment.yaml"
    ) == FileLockUtils.get_lockfile_path("/a/dir1/experiment.yaml")
