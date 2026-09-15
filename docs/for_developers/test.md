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
| `make test_backend_native` | The same backend tests, outside Docker |
| `make test_frontend` | Frontend tests only in Docker |

### Frontend only (local, from `frontend/`)

| Command | Use case |
|---|---|
| `yarn test` | Interactive watch mode -- best for dev |
| `yarn test:ci` | Single CI run (`CI=true`, no watch) |
| `yarn test-coverage` | With coverage report |
| `yarn test -- --testPathPattern="<pattern>"` | Run a specific file/pattern |

Stack: Jest + React Testing Library via `react-app-rewired` (CRA). Test files live in `__tests__/` directories or alongside source as `*.test.{ts,tsx}`.

### Backend only (local, outside Docker)

From the repo root:

```
make test_backend_native
```

`PYTEST_NATIVE` is the pytest command and defaults to `python3 -m pytest -s`, so
point it at whatever environment has the dependencies installed:

```
make test_backend_native PYTEST_NATIVE="poetry run pytest -s"
make test_backend_native PYTEST_NATIVE="~/miniforge3/envs/<env>/bin/python3 -m pytest -s"
```

`poetry install --with test` provides one (the `test` group is `optional = true`,
so a plain `poetry install` skips it). Run pytest directly when you want to
narrow the run to one file.

**Budget five minutes and over a gigabyte for the first run in a new checkout.**
The lccd snakemake tests are `lighter_processing`, so both lanes run them, and
they execute snakemake with `use_conda`. `DIRPATH.SNAKEMAKE_CONDA_ENV_DIR` is
`<checkout>/.snakemake/conda`, so a fresh checkout builds the lccd and optinist
environments -- conda or mamba on `PATH` and network access are required.
Measured on a pristine worktree: 320 s for the first run against ~40 s for every
run after, and 1.2-1.4 GB depending on the platform.

Docker pays that cost too, and separately. `Dockerfile.test` pre-builds no
environment, and compose bind-mounts `.:/app`, so `make test_backend` on a fresh
clone builds into that clone's own `.snakemake/conda`. The two lanes cannot
share the result: the env address hashes the absolute env dir, which is
`/app/.snakemake/conda` in the container and `<checkout>/.snakemake/conda`
natively. A checkout exercised both ways therefore holds two full copies of each
environment.

There is no native equivalent of `make test_backend_full`. The two
`heavier_processing` tests it adds run a suite2p workflow end to end. They are
deselected by both lanes above and are not run outside the container, so treat
them as unverified natively.

Two rules keep this lane equivalent to the Docker one, and are worth knowing
before you add a test:

- **Run it from the repo root.** `testpaths` and the `make` targets are written
  relative to it. Running from `studio/` happens to work -- pytest finds the
  rootdir from the ini file, not the working directory, so `pythonpath = "."` and
  the root `conftest.py` still resolve against the repo root -- but nothing keeps
  it working, so prefer the root.
- **The test environment comes from the root `conftest.py`, not your shell.**
  `OPTINIST_DIR`, `IS_TEST`, `IS_STANDALONE`, the storage settings, `TZ=UTC` and
  dummy `STRIPE_*` credentials are all set there, before any `studio` module is
  imported, and they overwrite whatever the shell exported. Add new test env vars
  there rather than to `docker-compose.test.yml` alone, or the native lane
  silently diverges. `TZ` in particular is load-bearing: subscription
  expirations round-trip through the DB as naive datetimes, so a JST machine
  shifts them by nine hours.

Tests live in `studio/tests/` and `studio/app/optinist/microscopes/tests/`. Two
conftests: the root one sets the environment described above, and
`studio/tests/app/conftest.py` holds the app fixtures (`client`, dependency
overrides, session teardown).

#### Avoid hardcoding absolute paths into fixtures

The suite should not care where the checkout lives. The trap is snakemake's
conda env addressing: `Env.address` is `<env_dir>/<md5>_`, and the md5 covers the
**absolute path** of the env dir along with the env yaml. A marker directory
committed under `studio/test_data` is therefore findable only from the checkout
that generated it, which is why two of them used to live there, one per
platform, and why neither matched an arbitrary checkout. `test_smk_utils.py`
builds its own env fixture under `tmp_path` instead, and pins the ported hash
function against the container-path hash by passing that path explicitly.

#### The suite writes into `studio/test_data`, and deletes part of it

The root `conftest.py` points `OPTINIST_DIR` at `studio/test_data`, so the run
uses that tree as its data directory rather than `/tmp/studio`. Two consequences
worth knowing before you chase a phantom failure:

- **`studio/test_data/output` is deleted at session teardown.** The session
  fixture in `studio/tests/app/conftest.py` ends with `shutil.rmtree`. That
  directory is generated, not a fixture, so this is intended -- but it means a
  tree you have run several partial suites against can leave `test_experiment.py`,
  `test_outputs.py`, `test_workflow.py`, `test_filepath_creater.py` and
  `test_workflow_reader.py` failing with `FileNotFoundError` on the *next* run.
  Those failures are local state, not your change. To confirm, re-run in a clean
  checkout: `git worktree add /tmp/baseline HEAD`, then run the suite there. A
  bare worktree is enough -- the tests no longer need the gitignored
  `studio/config/.env` copied across.
- **Lock files are left behind and are gitignored.** `InputFileLock` creates
  `input/<workspace>/.locks/<file>.lock` and the logger's
  `ConcurrentRotatingFileHandler` creates `logs/.__studio.lock`. Both are covered
  by the `.gitignore` in those directories; if you add a new runtime artefact
  under `studio/test_data`, ignore it there rather than at the repo root, because
  118 files in that tree are tracked fixtures.

### E2E release tests (Playwright, from `frontend/`)

Browser tests automating release verification, with stable per-feature test
IDs (`AUTH-01`, `WF-04`, ...). They need a running environment and a test
account:

```
yarn test:e2e
```

Setup, credentials, running, and troubleshooting: `frontend/e2e/README.md`.

### Test-sheet coverage maps

Which manual test-sheet rows are already automated is tracked in two documents,
one per sheet family:

| Document | Sheet family |
|---|---|
| `infrastructure/documentation/RELEASE_TEST_COVERAGE.md` | `Araya-OptiNiSt Release Test Cases Template` (`BT-1xx` .. `BT-11xx`), almost all Playwright |
| `infrastructure/documentation/SYSTEM_TEST_COVERAGE.md` | `Araya-Optinist System Test Cases Template`, a larger scheme covered mostly by the jest and pytest suites above |
