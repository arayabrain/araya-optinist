---
applyTo: "studio/app/**,studio/alembic/**,infrastructure/**/*.py"
---

# Backend: storage, concurrency, database, API contracts

- A read-modify-write of a file that two processes or requests can touch needs an existing file-lock helper with a bounded timeout (today `FileLockUtils` and `InputFileLock`).
- Publish order: an artifact must not become visible, locally or on S3, before the artifacts it depends on, and a failed step must not leave a partial artifact visible.
- A status flag derived from stored state must be read only after its producer has committed (today `has_nwb`, valid once `analyzed_at` is set).
- Request-scoped DB sessions, file handles and locks must not be held for, or passed into, background or long-running work.
- Flag list endpoints and queries without a limit, directory or S3 listings materialised in full, and one DB or S3 call per item in a loop.
- An input too large for the tier should get a clear 413 or 422, not an unhandled 500. Tiers have different memory limits; code the `public` tier loads runs under the smallest.
- Any engine or `creator=` that builds pymysql connections directly must pass `CLIENT.FOUND_ROWS` (today in `studio/app/common/db/config.py` and the `common_user_manager` Lambda), or `rowcount` counts changed rows and no-op updates read as missing rows.
- A response schema change must be mirrored in `frontend/src/api/` types and in the contract tests (`studio/tests/app/common/routers/test_*_contract.py`).
- Backend logic changes come with pytest tests under `studio/tests/app/`, mirroring the source path. New test environment variables go in the root `conftest.py`.
- A diff that rewrites every line of a file with no visible change is a line-ending change. Flag it unless the PR says normalisation is its purpose.
