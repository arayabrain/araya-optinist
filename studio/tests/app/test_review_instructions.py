"""The Copilot review skill and instruction files name real paths and symbols.

A rule pointing at a renamed helper is applied with full confidence and is
wrong, so a rename that orphans a rule fails here, in the PR that caused it.
"""

import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
DOCS = sorted(
    [
        *(ROOT / ".github/skills/code-review").rglob("*.md"),
        *(ROOT / ".github/instructions").glob("*.instructions.md"),
    ]
)
SEARCHED = ["studio/app", "studio/tests", "studio/alembic", "frontend/src"]
SEARCHED += ["frontend/e2e", "infrastructure", ".github", "docs"]
PRUNED = {"node_modules", ".terraform", ".build", "__pycache__", ".git"}
SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".yml", ".yaml", ".toml", ".ini", ".md"}
PATH_PREFIXES = ("studio/", "frontend/", "infrastructure/", ".github/", "docs/")
FILE_NAME = re.compile(r"^[\w.-]+\.(py|ts|tsx|yml|yaml|md)$")
SYMBOL = re.compile(r"^[A-Za-z_][A-Za-z0-9]*_[A-Za-z0-9_]*$")


def _tokens(doc: Path) -> list:
    return re.findall(r"`([^`\n]+)`", doc.read_text())


def _source_files():
    for top in SEARCHED:
        for dirpath, dirnames, filenames in os.walk(ROOT / top):
            dirnames[:] = [d for d in dirnames if d not in PRUNED]
            for name in filenames:
                if Path(name).suffix in SOURCE_SUFFIXES:
                    yield Path(dirpath, name)
    yield from (ROOT / name for name in ("conftest.py", "Makefile", "pyproject.toml"))


@pytest.fixture(scope="module")
def corpus():
    files = [p for p in _source_files() if p.is_file()]
    code = [p for p in files if p.suffix != ".md" and ".github" not in p.parts[-4:]]
    text = "\n".join(p.read_text(errors="ignore") for p in code)
    return {p.name for p in files}, text


def test_the_review_docs_exist():
    assert (ROOT / ".github/skills/code-review/SKILL.md") in DOCS


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_named_paths_files_and_symbols_resolve(doc, corpus):
    names, text = corpus
    missing = []
    for token in _tokens(doc):
        if token.startswith(PATH_PREFIXES):
            pattern = token.split("<")[0]
            found = (
                any(ROOT.glob(pattern))
                if "*" in pattern
                else ((ROOT / pattern).exists())
            )
        elif FILE_NAME.match(token):
            found = token in names
        elif SYMBOL.match(token):
            found = re.search(rf"\b{re.escape(token)}\b", text) is not None
        else:
            continue
        if not found:
            missing.append(token)
    assert not missing, f"{doc.name} names what no longer exists: {missing}"


@pytest.mark.parametrize(
    "doc",
    [d for d in DOCS if d.name.endswith(".instructions.md")],
    ids=lambda p: p.name,
)
def test_every_apply_to_glob_matches_a_file(doc):
    match = re.search(r'^applyTo:\s*"([^"]+)"', doc.read_text(), re.MULTILINE)
    assert match, f"{doc.name} has no applyTo"
    empty = [g for g in match.group(1).split(",") if not any(ROOT.glob(g))]
    assert not empty, f"{doc.name} applyTo matches nothing: {empty}"
