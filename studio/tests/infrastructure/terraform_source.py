"""Helpers for configuration tests that read the terraform source as text.

Shared by ``test_compute_config.py`` and ``test_public_instance_config.py``.
Regex-based, not an HCL parser: each helper assumes ``terraform fmt`` layout,
with top-level blocks starting at column 0.
"""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
TERRAFORM_DIR = PROJECT_ROOT / "infrastructure" / "terraform"

# Lookahead for the start of the next top-level block, which ends the current one
NEXT_TOP_LEVEL_BLOCK = (
    r"(?=^(?:resource|data|output|locals|module|variable|moved|import|terraform)\s|\Z)"
)


def read(path: Path) -> str:
    assert path.exists(), f"terraform source not found at {path}"
    return path.read_text()


def resource_block(tf: str, resource_type: str, name: str) -> str:
    """Return the source of one ``resource "<type>" "<name>"`` block."""
    match = re.search(
        r'resource\s+"%s"\s+"%s"\s*\{.*?%s'
        % (re.escape(resource_type), re.escape(name), NEXT_TOP_LEVEL_BLOCK),
        tf,
        re.DOTALL | re.MULTILINE,
    )
    assert match, f'resource "{resource_type}" "{name}" not found'
    return match.group()


def attribute(source: str, name: str) -> str:
    """Return one ``<name> = <value>`` right-hand side, anchored at line start
    so ``healthy_threshold`` cannot match inside ``unhealthy_threshold``."""
    match = re.search(
        r"^\s*%s\s*=\s*(.+?)\s*(?:#.*)?$" % re.escape(name), source, re.MULTILINE
    )
    assert match, f"attribute {name} not found"
    return match.group(1)


def string_list(value: str) -> list:
    """Return the quoted items of a one-line list literal, e.g. ``["a", "b"]``."""
    assert value.startswith("[") and value.endswith("]"), f"not a list: {value}"
    return re.findall(r'"([^"]*)"', value)
