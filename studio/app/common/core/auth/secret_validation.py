"""Startup checks for the signing secrets of a multiuser deployment.

SECRET_KEY signs the locally issued access, refresh and beacon tokens, and
ROUTING_SECRET_KEY signs the premium routing IDs. Both ship with placeholder
defaults so a fresh checkout runs without setup. Startup refuses a placeholder
or short key wherever a forged token would be accepted: both keys in the
cloud, and SECRET_KEY wherever USE_FIREBASE_TOKEN is off (the ExToken alone
then authenticates). Elsewhere (local development with Firebase) it only warns.

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

# HS256 keys shorter than this can be brute-forced offline from one token.
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
    placeholders: List[str]
    # Set, but short enough to brute-force.
    short: List[str]


def is_placeholder(value: str) -> bool:
    value = value.strip()
    return (
        not value
        or value in PUBLISHED_PLACEHOLDERS
        or value.upper().startswith(PLACEHOLDER_MARKER_PREFIX)
    )


PLACEHOLDER, SHORT, OK = "placeholder", "short", "ok"


def _strength(value: str) -> str:
    if is_placeholder(value):
        return PLACEHOLDER
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
        placeholders=[name for name, s in strength.items() if s == PLACEHOLDER],
        short=[name for name, s in strength.items() if s == SHORT],
    )


def is_cloud_deployment() -> bool:
    """Cloud deployments are the ones backed by S3 storage (set by Terraform)."""
    return RemoteStorageType.get_activated_type() == RemoteStorageType.S3


def validate_signing_secrets() -> None:
    """Raise where a weak key lets anyone forge tokens; warn elsewhere.

    Standalone mode authenticates nobody, so there is nothing to check.
    """
    if MODE.IS_STANDALONE:
        return

    findings = find_weak_signing_keys()
    weak = findings.placeholders + findings.short
    if not weak:
        return

    if is_cloud_deployment():
        refused = weak
    elif not AUTH_CONFIG.USE_FIREBASE_TOKEN:
        # The ExToken alone authenticates; routing IDs are only used in the cloud.
        refused = [name for name in weak if name == "SECRET_KEY"]
    else:
        refused = []

    if refused:
        message = (
            f"Refusing to start: placeholder or short value in {', '.join(refused)}. "
            f"Set a random value of at least {MIN_KEY_LENGTH} characters."
        )
        logger.error(message)
        raise RuntimeError(message)

    logger.warning(
        "Placeholder or short value in %s. Acceptable for local development only.",
        ", ".join(weak),
    )
