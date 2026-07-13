## Test

We have unit tests for both frontend and backend. They are automatically run by GitHub workflow on submitting Pull Requests. You can also run them locally.

### Run everything (Docker)

From the repo root:

```
make test_run_all
```

This builds and runs `test_studio_backend` (pytest, excluding `heavier_processing`) and `test_studio_frontend` (yarn test:ci) via `docker-compose.test.yml`, then the lambda/infrastructure tests.

Other Makefile targets:

| Target | What it runs |
|---|---|
| `make test_backend` | Backend tests only -- `pytest studio/tests/app/ -m "not heavier_processing"` in Docker |
| `make test_backend_full` | Backend tests including `heavier_processing` |
| `make test_frontend` | Frontend tests only in Docker |

### Frontend only (local, from `frontend/`)

| Command | Use case |
|---|---|
| `yarn test` | Interactive watch mode -- best for dev |
| `yarn test:ci` | Single CI run (`CI=true`, no watch) |
| `yarn test-coverage` | With coverage report |
| `yarn test -- --testPathPattern="<pattern>"` | Run a specific file/pattern |

Stack: Jest + React Testing Library via `react-app-rewired` (CRA). Test files live in `__tests__/` directories or alongside source as `*.test.{ts,tsx}`.

### Backend only (local, from repo root)

```
cd studio && poetry run pytest tests/app/ -m "not heavier_processing"
```

Tests live in `studio/tests/` and `studio/app/optinist/microscopes/tests/`. Conftest at `studio/tests/app/conftest.py`. Backend tests may require env vars -- see `docker-compose.test.yml` for `PYTHONPATH`, `STRIPE_*`, etc.
