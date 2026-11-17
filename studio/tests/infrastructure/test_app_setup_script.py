"""Host file regression tests for infrastructure/scripts/app_setup.sh.

The script runs as root on every free-tier host on a schedule, against AWS and
the database, so it is checked as text, like the terraform sources. The admin
email verification step alone is also run, with secrets that fail before any
network call.

- Host configuration: the script writes none; each container fetches its own
  at startup. Each run removes the files earlier versions left on the host,
  before any step that can fail.
- Firebase service account: reaches the admin email verification on stdin
  only; an invalid one is reported as such, without its value.
- DB password: reaches mysql through the environment, not the command line.
- DB-initialisation SQL: created owner-only, removed on any exit.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# Same layout handling as test_git_ref_info.py: this file sits at
# studio/tests/infrastructure/, so the project root is four levels up.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
APP_SETUP = PROJECT_ROOT / "infrastructure" / "scripts" / "app_setup.sh"

LEFTOVER_CONFIG_DIR = "/opt/optinist/optinist-for-cloud/studio/config"
LEFTOVER_FILES = [
    "${LEFTOVER_CONFIG_DIR}/auth/firebase_config.json",
    "${LEFTOVER_CONFIG_DIR}/auth/firebase_private.json",
    "${LEFTOVER_CONFIG_DIR}/.env",
    "/tmp/init_optinist_db.sql",
]
REMOVE_LEFTOVERS = 'rm -f "${LEFTOVER_FILES[@]}"'

# From the directory assignment to the closing parenthesis of the file list
_LEFTOVER_DEFINITION_RE = re.compile(
    r"^LEFTOVER_CONFIG_DIR=.*?^\)$", re.DOTALL | re.MULTILINE
)
# A call, not the function definition (`name() {`)
_FALLIBLE_CALL_RE = re.compile(r"\b(?:retry_command|get_secret)\s+[^(\s]")
# The verification pipeline, from its printf to its `|| echo` fallback
_VERIFICATION_STEP_RE = re.compile(
    r'^printf \'%s\' "\$FIREBASE_PRIVATE_JSON" \| python3 -c "$.*?^" \|\| echo .*?$',
    re.DOTALL | re.MULTILINE,
)
SECRET_MARKER = "value-that-must-not-be-logged"


def _script() -> str:
    assert APP_SETUP.exists(), f"app_setup.sh not found at {APP_SETUP}"
    return APP_SETUP.read_text()


def _statements(source: str) -> list:
    """Return the non-blank lines that are not ``#`` comments, stripped."""
    lines = (line.strip() for line in source.splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def _leftover_definition(script: str) -> str:
    match = _LEFTOVER_DEFINITION_RE.search(script)
    assert match, "LEFTOVER_CONFIG_DIR / LEFTOVER_FILES definition not found"
    return match.group()


class TestHostConfiguration:
    def test_leftover_list_names_every_file_earlier_versions_wrote(self):
        definition = _leftover_definition(_script())
        assert f'LEFTOVER_CONFIG_DIR="{LEFTOVER_CONFIG_DIR}"' in definition
        assert re.findall(r'^\s+"([^"]+)"$', definition, re.MULTILINE) == (
            LEFTOVER_FILES
        )

    def test_leftover_files_are_only_removed(self):
        """Outside their definition, the paths appear only in the removal: no
        statement writes them or creates their directory."""
        script = _script()
        rest = _statements(script.replace(_leftover_definition(script), ""))
        for name in (
            "/opt/optinist",
            "LEFTOVER_CONFIG_DIR",
            "firebase_config.json",
            "firebase_private.json",
            "/tmp/init_optinist_db.sql",
        ):
            assert [line for line in rest if name in line] == [], name
        assert [line for line in rest if "LEFTOVER_FILES" in line] == [REMOVE_LEFTOVERS]
        assert [line for line in rest if re.search(r">\s*\S*\.env\b", line)] == []

    def test_leftovers_are_removed_before_any_step_that_can_fail(self):
        """A host whose ECS agent, Secrets Manager or database is failing still
        loses the files on its next run."""
        statements = _statements(_script())
        first_fallible = next(
            index
            for index, line in enumerate(statements)
            if _FALLIBLE_CALL_RE.search(line)
        )
        assert statements.index(REMOVE_LEFTOVERS) < first_fallible


class TestFirebaseServiceAccount:
    def test_service_account_reaches_the_verification_only_on_stdin(self):
        """Fetched once and used once, on a pipe into the verification, so no
        statement can put it in a file."""
        script = _script()
        uses = [line for line in _statements(script) if "FIREBASE_PRIVATE_JSON" in line]
        assert uses == [
            'FIREBASE_PRIVATE_JSON=$(get_secret "${ENV_PREFIX}-optinist/firebase/'
            'private-key")',
            'printf \'%s\' "$FIREBASE_PRIVATE_JSON" | python3 -c "',
        ]
        assert "json.load(sys.stdin)" in script

    @pytest.mark.skipif(shutil.which("bash") is None, reason="bash runs the step")
    @pytest.mark.parametrize(
        "secret",
        [
            "",
            "{not json",
            f'"{SECRET_MARKER}"',
            f'["{SECRET_MARKER}"]',
            f'{{"type": "{SECRET_MARKER}"}}',
        ],
        ids=["empty", "malformed", "json-string", "json-list", "wrong-type"],
    )
    def test_invalid_secret_is_reported_without_its_value(self, secret, tmp_path):
        """The step, run as the script runs it, names the secret as the problem
        rather than the SDK, and keeps going. A JSON string would otherwise be
        taken for a file path and echoed in the traceback."""
        pytest.importorskip("firebase_admin")
        match = _VERIFICATION_STEP_RE.search(_script())
        assert match, "verification step not found"
        # A wrapper, not a symlink: a venv's interpreter needs its own path
        python3 = tmp_path / "python3"
        python3.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
        python3.chmod(0o755)
        env = {
            **os.environ,
            "PATH": f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}",
            "FIREBASE_PRIVATE_JSON": secret,
            "OPTINIST_ADMIN_UID": "test-admin-uid",
            # Any network call fails fast instead of reaching Google
            "HTTPS_PROXY": "http://127.0.0.1:9",
        }
        result = subprocess.run(
            ["bash", "-c", match.group()],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = result.stdout + result.stderr
        assert result.returncode == 0, output
        assert (
            "ERROR: Firebase service account secret is not a valid certificate"
            in output
        )
        assert "default Firebase app does not exist" not in output
        assert SECRET_MARKER not in output
        assert "WARNING: Firebase email verification script encountered" in output


class TestDbPassword:
    def test_password_stays_off_the_mysql_command_line(self):
        """argv is readable by every user on the host; root's environment is
        not."""
        statements = _statements(_script())
        calls = [line for line in statements if re.search(r"\bmysql\s+-h\b", line)]
        assert calls, "mysql call not found"
        assert all('MYSQL_PWD="$MYSQL_PASSWORD" mysql ' in line for line in calls)
        assert [line for line in calls if re.search(r"\s-p\S", line)] == []


class TestDbInitSqlFile:
    def test_created_owner_only(self):
        """mktemp creates the file 0600 under a name nobody can predict, and
        nothing widens its mode afterwards."""
        statements = _statements(_script())
        assignments = [
            line for line in statements if line.startswith("DB_INIT_SQL_FILE=")
        ]
        assert assignments == [
            "DB_INIT_SQL_FILE=$(mktemp /tmp/init_optinist_db.XXXXXX)"
        ]
        assert [
            line
            for line in statements
            if line.startswith("chmod") and "DB_INIT_SQL_FILE" in line
        ] == []

    def test_removed_on_any_exit(self):
        """The EXIT trap is in place before the first write, so a failure at any
        later step (set -e) still removes the file."""
        statements = _statements(_script())
        trap = statements.index("trap 'rm -f \"$DB_INIT_SQL_FILE\"' EXIT")
        first_write = next(
            index
            for index, line in enumerate(statements)
            if re.search(r'>\s*"\$DB_INIT_SQL_FILE"', line)
        )
        created = statements.index(
            "DB_INIT_SQL_FILE=$(mktemp /tmp/init_optinist_db.XXXXXX)"
        )
        assert created < trap < first_write
