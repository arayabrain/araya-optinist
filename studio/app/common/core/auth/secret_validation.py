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
RECOMMENDED_MIN_SECRET_LENGTH = 32


class SecretFindings(NamedTuple):
    # Placeholder or empty values: forgeable tokens.
    blocking: List[str]
    # Usable but weaker than recommended.
    warnings: List[str]


def find_weak_secrets() -> SecretFindings:
    blocking: List[str] = []
    warnings: List[str] = []

    secret_key = AUTH_CONFIG.SECRET_KEY or ""
    if secret_key in ("", INSECURE_DEFAULT_SECRET_KEY):
        blocking.append("SECRET_KEY")
    elif len(secret_key) < RECOMMENDED_MIN_SECRET_LENGTH:
        warnings.append("SECRET_KEY")

    routing_key = os.environ.get("ROUTING_SECRET_KEY", "")
    if routing_key in ("", DEFAULT_ROUTING_SECRET_KEY):
        blocking.append("ROUTING_SECRET_KEY")

    return SecretFindings(blocking=blocking, warnings=warnings)


def is_cloud_deployment() -> bool:
    """Cloud deployments are the ones backed by S3 storage (set by Terraform)."""
    return RemoteStorageType.get_activated_type() == RemoteStorageType.S3


def validate_signing_secrets() -> None:
    """Raise in a cloud deployment that runs on placeholder secrets.

    Standalone mode authenticates nobody, so there is nothing to check.
    """
    if MODE.IS_STANDALONE:
        return

    findings = find_weak_secrets()

    if findings.warnings:
        logger.warning(
            "Signing secret shorter than %d characters: %s",
            RECOMMENDED_MIN_SECRET_LENGTH,
            ", ".join(findings.warnings),
        )

    if not findings.blocking:
        return

    names = ", ".join(findings.blocking)
    if is_cloud_deployment():
        message = (
            f"Refusing to start: {names} is unset or a built-in placeholder. "
            "Set a random value of at least "
            f"{RECOMMENDED_MIN_SECRET_LENGTH} characters."
        )
        logger.error(message)
        raise RuntimeError(message)

    logger.warning(
        "%s is unset or a built-in placeholder. "
        "Acceptable for local development only.",
        names,
    )
