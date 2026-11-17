"""list_users.sh against stub aws and jq returning mariadb -t text."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT = PROJECT_ROOT / "infrastructure" / "scripts" / "list_users.sh"

STUB_AWS = """#!/usr/bin/env bash
echo "$*" >> "$AWS_LOG"
case "$1 $2" in
  "rds describe-db-proxies") echo proxy.example ;;
  "ec2 describe-instances") echo i-0stub ;;
  "ssm send-command") echo cmd-1 ;;
  "ssm get-command-invocation")
    case "$*" in
      *StandardOutputContent*) cat "$STUB_OUT" ;;
      *Status*) echo Success ;;
    esac ;;
esac
"""

STUB_JQ = """#!/usr/bin/env bash
cat > "$JQ_IN"
echo '{}'
"""

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("awk") is None,
    reason="bash and awk are required to run the script",
)


def table(header: str, rows: list[str]) -> str:
    width = max(len(v) for v in [header, *rows])
    border = "+" + "-" * (width + 2) + "+"
    lines = [border, f"| {header.ljust(width)} |", border]
    lines += [f"| {r.ljust(width)} |" for r in rows]
    return "\n".join([*lines, border]) + "\n"


def full_output(emails: list[str]) -> str:
    return table("email", emails) + table("marker", ["END_OF_LIST"])


def run(tmp_path: Path, stdout: str, *args: str):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in (("aws", STUB_AWS), ("jq", STUB_JQ)):
        stub = bin_dir / name
        stub.write_text(body)
        stub.chmod(0o755)
    out = tmp_path / "ssm_stdout.txt"
    out.write_text(stdout)
    log = tmp_path / "aws.log"
    log.write_text("")
    jq_in = tmp_path / "jq_in.txt"
    result = subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "AWS_REGION": "ap-northeast-1",
            "AWS_LOG": str(log),
            "STUB_OUT": str(out),
            "JQ_IN": str(jq_in),
        },
    )
    return result, log.read_text(), jq_in.read_text() if jq_in.exists() else ""


def test_full_result_prints_only_deliverable_addresses(tmp_path):
    emails = ["a@example.com", "b.long.name@example.org", "M.Tester@example.com"]
    result, log, sql = run(tmp_path, full_output(emails))
    assert result.returncode == 0, result.stderr
    assert result.stdout == "a@example.com\nb.long.name@example.org\n"
    assert "excluded as a test address: M.Tester@example.com" in result.stderr
    assert "subscr-optinist-rds-proxy" in log
    assert "NOT LIKE '%@test.com'" in sql
    assert "'%test%'" not in sql


def test_truncated_output_aborts(tmp_path):
    emails = [f"user{i:04d}@example-domain.com" for i in range(1000)]
    result, _, _ = run(tmp_path, full_output(emails)[:24000])
    assert result.returncode == 1
    assert result.stdout == ""
    assert "ABORT" in result.stderr and "truncated" in result.stderr


@pytest.mark.parametrize(
    "stdout",
    [
        "email\na@example.com\nmarker\nEND_OF_LIST\n",
        full_output(["qa.test@example.com"]),
    ],
    ids=["no-table-rows", "only-test-like-rows"],
)
def test_no_deliverable_address_aborts(tmp_path, stdout):
    result, _, _ = run(tmp_path, stdout, "development")
    assert result.returncode == 1
    assert result.stdout == ""
    assert "ABORT: no deliverable address" in result.stderr
