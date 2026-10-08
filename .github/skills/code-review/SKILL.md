---
name: code-review
description: OptiNiSt-specific pull request review checklist. Use when reviewing pull requests in this repository.
---

# OptiNiSt code review

Focus on issues a linter cannot catch. Python formatting, import order and
flake8 are enforced by the `Python Linters` workflow (black, isort, flake8); do
not comment on them. Label each comment High, Medium or Low. Skip a comment
entirely if you are not confident it is a real problem, and never ask for a
change you cannot point to a concrete failure for.

## Repo map (where things actually live)
- Backend: `studio/app/common/**` (auth, routers, DB, storage, workflow engine) and `studio/app/optinist/**` (calcium-imaging wrappers, dataclasses, NWB, ROI routers).
- Router registration and auth dependencies: `_register_routers` in `studio/__main_unit__.py`.
- Snakemake: `studio/app/Snakefile`, rule scripts in `studio/app/common/core/rules/`.
- DB models: `studio/app/common/models/`; migrations: `studio/alembic/versions/`.
- Frontend: `frontend/src/api/<area>/index.ts` (HTTP), `frontend/src/store/slice/<Area>/` (Redux Toolkit slices and thunks), shared axios instance in `frontend/src/utils/axios.ts`.
- Base branch is `develop-main`, not `main`.

## Security and access control (High when missed)
- Router-level `Depends(get_current_user)` only authenticates. Any route taking a `workspace_id` must also depend on `is_workspace_owner` (writes, deletes) or `is_workspace_available` (reads, includes shared users) from `studio/app/common/core/workspace/workspace_dependencies.py`. A route with only the router-level dependency lets any logged-in user touch any workspace.
- Admin-only routes belong on `users_admin.router` (guarded by `get_admin_user`); flag admin actions added to other routers without an explicit admin check.
- Paths built from request data must go through `join_filepath` (`studio/app/common/core/utils/filepath_creater.py`), which raises `InvalidPathError` on traversal. Flag `os.path.join` or f-string paths on request input.
- Do not accept any rewrite of the containment check inside `join_filepath`. The `startswith()` must remain the whole `if` condition: equivalent-looking refactors make CodeQL lose the sanitizer and reopen ~150 `py/path-injection` alerts, and no test or linter catches it.
- Search filters use `.contains(term, autoescape=True)`. Flag any new `like`/`ilike`/`contains` on user input without `autoescape=True` (`%` and `_` would act as wildcards; `test_search_terms_are_literal.py` covers the existing call sites only).
- Routing headers (`x-user-tier`, `x-routing-id`) are set by `secure_routing_middleware.py`; flag any code that trusts these from the client.

## Deployment modes
- One codebase runs as several ECS tiers selected by `INSTANCE_MODE` (`studio/app/common/core/instance_mode.py`). On the `public` tier the workflow routers and `wrapper_dict` imports are skipped. New module-level imports of heavy wrapper or snakemake code from routers that the public tier loads will break that tier at startup.
- `IS_STANDALONE` (`studio/app/common/core/mode.py`) switches single-user vs multi-user. Code that assumes a DB, Firebase user, or subscription must handle standalone mode, and vice versa.

## Algorithm wrappers (studio/app/optinist/wrappers/**)
- A new function must be added to its package's `*_wrapper_dict` with the right `conda_name`, and that package dict must be merged in `studio/app/optinist/wrappers/__init__.py`. Unregistered functions silently do not appear in the UI.
- Each function needs a default params YAML under `<package>/params/`, and its conda env YAML under `<package>/conda/` must pin versions.
- Wrapper and rule code runs inside conda envs, not the FastAPI process. `suite2p`, `lccd`, `optinist` and `microscope` envs are Python 3.9: flag 3.10+ syntax (`match`, `X | Y` type unions at runtime, parenthesized context managers) and any import of `fastapi`, `snakemake_executor` or `snakemake.api` from wrapper or `core/rules/` code.
- Inputs and outputs use the existing dataclasses (`ImageData`, `TimeSeriesData`, `FluoData`, `RoiData`, `IscellData`, etc. in `studio/app/common/dataclass/` and `studio/app/optinist/dataclass/`), not raw arrays or dicts.
- Fluorescence arrays: check the axis order a change assumes. Mixed (roi, time) vs (time, roi) handling between in-memory data and NWB has caused real bugs; ask for a test that pins the shape.
- Pinned scientific stacks (numpy, pandas, suite2p, CaImAn) differ per env. Flag a version bump in one env that is not reflected in code paths shared with another env.

## Saved workflows and params
- Renaming, removing, or changing a param from scalar to dict (or back) in a default params YAML is a breaking change for users' saved workflows: `check_types` in `studio/app/common/core/workflow/workflow_params.py` drops keys that no longer match, with only a log warning, so the user's saved value is lost. Flag such changes and ask how existing saved values migrate.
- Changes to the stored workflow or snakemake YAML format must still load existing files.

## Concurrency and storage
- Async route handlers must not do blocking filesystem, S3, or `subprocess` work directly; use `asyncio.to_thread` / `run_in_threadpool`. Large directory walks on the event loop have stalled the server before.
- Writes to shared per-file state (metadata caches, ROI edits, node pickles) need the existing locks (`InputFileLock` in `remote_storage_controller.py`, the RUN lock for ROI edits). Flag new read-modify-write of shared files without one.
- Ordering matters for staged outputs: node pickles are published after `whole.nwb`, and a failed node must not leave its staged pickle published to S3. Flag changes that reorder or skip this.
- Frontend status flags derived from the DB (for example `has_nwb`) can race with workflow finalisation; flag new fields read before the run is finalised (`analyzed_at`).
- `studio/app/common/core/storage/remote_storage_controller.py` and `mock_storage_controller.py` have CRLF line endings. A diff that rewrites every line of these files is a line-ending change, not a real edit; ask for it to be reverted.

## Database
- Model changes need an Alembic migration in `studio/alembic/versions/`. The `Alembic Check` workflow runs `alembic check` against a fresh DB, so models and migrations must describe the same schema.
- Code relying on `UPDATE ... rowcount` depends on the connection's `CLIENT.FOUND_ROWS` flag (`studio/app/common/db/config.py`). Any new custom `creator=` or connect args must keep it, or rowcount reports changed rows instead of matched rows and "no-op update" paths misfire.

## Backend API contracts
- Response schema changes must be mirrored in the frontend types under `frontend/src/api/**`. Contract tests live in `studio/tests/app/common/routers/test_*_contract.py`; a schema change without a contract test update is suspect.

## Frontend (frontend/src/**)
- HTTP calls go through `frontend/src/api/<area>/index.ts` using the shared axios instance and are dispatched from Redux thunks in `store/slice/`. Flag ad hoc `fetch` or a new axios instance (it would skip auth headers and error handling).
- No `any` or unchecked casts to hide a mismatch with a backend schema.
- Long-running actions (runs, uploads, ROI edits) show progress and surface errors to the user.

## Tests
- Backend logic changes come with pytest tests under `studio/tests/app/`, mirroring the source path. CI runs `make test_backend` (excludes `heavier_processing`), `make test_lambda` and `make test_frontend`.
- New test environment variables go in the root `conftest.py`, not only `docker-compose.test.yml`.
- `@pytest.mark.skip` without a condition is rejected by `test_no_dead_tests.py`; use `skipif` with a real condition.
- A test that would still pass with the fix reverted is not coverage. Flag tests whose assertions only check mocks were called, or that guard on a condition the setup already guarantees.
- Flag PRs that change wrapper output shapes without updating test fixtures.

## Out of scope
- Do not suggest rewrites of unrelated code.
- Do not request docs changes unless public behavior changed.
- Do not comment on PR body formatting; the template is checked by humans.
