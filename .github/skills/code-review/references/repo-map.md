# Repo map

- Backend: `studio/app/common/` (auth, routers, DB, storage, workflow engine) and `studio/app/optinist/` (calcium-imaging wrappers, dataclasses, NWB, ROI routers).
- Router registration and auth dependencies: `_register_routers` in `studio/__main_unit__.py`; workspace dependencies in `studio/app/common/core/workspace/workspace_dependencies.py`.
- Path helper: `join_filepath` in `studio/app/common/core/utils/filepath_creater.py`.
- Tier and mode switches: `studio/app/common/core/instance_mode.py`, `studio/app/common/core/mode.py`.
- Snakemake: `studio/app/Snakefile`, rule scripts in `studio/app/common/core/rules/`.
- Saved-workflow params: `studio/app/common/core/workflow/workflow_params.py`.
- DB models: `studio/app/common/models/`; migrations: `studio/alembic/versions/`; connection setup: `studio/app/common/db/config.py`.
- Frontend: `frontend/src/api/<area>/` (HTTP), `frontend/src/store/slice/<Area>/` (Redux Toolkit slices and thunks), shared axios instance in `frontend/src/utils/axios.ts`.
- Tests: `studio/tests/app/` (backend, mirrors the source tree), `*.test.ts` and `*.test.tsx` files under `frontend/src/`, Playwright in `frontend/e2e/`.
