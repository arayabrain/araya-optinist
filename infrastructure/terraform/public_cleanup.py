"""Scheduled cleanup tasks for the public tier.

Currently wipes the on-demand raw-input cache on shared EFS — those inputs are
kept off the lean root EBS and are pure cache (re-synced on access), so clearing
them daily bounds EFS growth. Add further public-tier cleanup tasks here as
needed; `handler` aggregates each task's result.
"""

import os
import shutil

INPUT_CACHE_PATH = os.environ.get("INPUT_CACHE_PATH", "/mnt/input")


def _clear_dir_contents(path):
    """Remove everything under `path`, leaving the directory itself in place."""
    if not os.path.isdir(path):
        print(f"[public-cleanup] {path} not present; nothing to do")
        return {"deleted": 0, "errors": 0}

    deleted = 0
    errors = 0
    for entry in os.scandir(path):
        try:
            if entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry.path)
            else:
                os.remove(entry.path)
            deleted += 1
        except OSError as e:
            errors += 1
            print(f"[public-cleanup] failed to delete {entry.path}: {e}")
    return {"deleted": deleted, "errors": errors}


def handler(event, context):
    results = {"input_cache": _clear_dir_contents(INPUT_CACHE_PATH)}
    print(f"[public-cleanup] done: {results}")
    return results
