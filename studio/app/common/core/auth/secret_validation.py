"""Startup checks for the signing secrets of a multiuser deployment.

SECRET_KEY signs the locally issued access, refresh and beacon tokens, and
ROUTING_SECRET_KEY signs the premium routing IDs. Both ship with placeholder
defaults so a fresh checkout runs without setup. A cloud deployment left on
those placeholders would accept tokens anyone can forge, so startup refuses
to run there; elsewhere (local multiuser development) it only warns.

Only variable names are ever logged, never their values.
"""

import os
from typing import List, NamedTuple

from studio.app.common.core.auth.auth_config import (
    AUTH_CONFIG,
    INSECURE_DEFAULT_SECRET_KEY,
)
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.middleware.constants import DEFAULT_ROUTING_SECRET_KEY
from studio.app.common.core.mode import MODE
from studio.app.common.core.storage.remote_storage_controller import RemoteStorageType

logger = AppLogger.get_logger()

# Shorter keys are reported but not blocked: an existing deployment may run on
# one, and refusing to start would turn a hardening change into an outage.
MIN_KEY_LENGTH = 32


# Values published in this repository: the code defaults, studio/config/
# .env.example, and the "<PLACEHOLDER-...>" markers in the Terraform
# *.tfvars.example files.
PUBLISHED_PLACEHOLDERS = frozenset(
    {INSECURE_DEFAULT_SECRET_KEY, DEFAULT_ROUTING_SECRET_KEY, "12345678"}
)
PLACEHOLDER_MARKER_PREFIX = "<PLACEHOLDER"


class KeyFindings(NamedTuple):
    # Unset, empty or published values: forgeable tokens.
    blocking: List[str]
    # Usable but shorter than recommended.
    warnings: List[str]


def is_placeholder(value: str) -> bool:
    value = value.strip()
    return (
        not value
        or value in PUBLISHED_PLACEHOLDERS
        or value.upper().startswith(PLACEHOLDER_MARKER_PREFIX)
    )


BLOCKING, SHORT, OK = "blocking", "short", "ok"


def _strength(value: str) -> str:
    if is_placeholder(value):
        return BLOCKING
    if len(value) < MIN_KEY_LENGTH:
        return SHORT
    return OK


def find_weak_signing_keys() -> KeyFindings:
    # Only names and constant verdicts leave this function, never the values.
    strength = {
        "SECRET_KEY": _strength(AUTH_CONFIG.SECRET_KEY or ""),
        "ROUTING_SECRET_KEY": _strength(os.environ.get("ROUTING_SECRET_KEY", "")),
    }
    return KeyFindings(
        blocking=[name for name, s in strength.items() if s == BLOCKING],
        warnings=[name for name, s in strength.items() if s == SHORT],
    )


def is_cloud_deployment() -> bool:
    """Cloud deployments are the ones backed by S3 storage (set by Terraform)."""
    return RemoteStorageType.get_activated_type() == RemoteStorageType.S3


def validate_signing_secrets() -> None:
    """Raise in a cloud deployment that runs on placeholder secrets.

    Standalone mode authenticates nobody, so there is nothing to check.
    """
    if MODE.IS_STANDALONE:
        return

    findings = find_weak_signing_keys()

    if findings.warnings:
        logger.warning(
            "Signing secret shorter than %d characters: %s",
            MIN_KEY_LENGTH,
            ", ".join(findings.warnings),
        )

    if not findings.blocking:
        return

    names = ", ".join(findings.blocking)
    if is_cloud_deployment():
        message = (
            f"Refusing to start: unset or placeholder value in {names}. "
            "Set a random value of at least "
            f"{MIN_KEY_LENGTH} characters."
        )
        logger.error(message)
        raise RuntimeError(message)

    logger.warning(
        "Unset or placeholder value in %s. Acceptable for local development only.",
        names,
    )
