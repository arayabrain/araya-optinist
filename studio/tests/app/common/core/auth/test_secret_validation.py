"""Startup refuses a placeholder or short signing key where tokens can be forged.

Tokens signed with a key published in this repository can be forged by anyone.
Cloud deployments are recognised by S3 remote storage. Without Firebase the
ExToken alone authenticates, so SECRET_KEY is checked there too. Elsewhere the
same findings are only logged so local multiuser development keeps working.
"""

from unittest.mock import patch

import pytest

from studio.app.common.core.auth import secret_validation
from studio.app.common.core.auth.auth_config import (
    AUTH_CONFIG,
    INSECURE_DEFAULT_SECRET_KEY,
)
from studio.app.common.core.middleware.constants import DEFAULT_ROUTING_SECRET_KEY
from studio.app.common.core.mode import MODE

STRONG_KEY = "k" * 64
S3 = "2"
MOCK_STORAGE = "1"


@pytest.fixture
def multiuser(monkeypatch):
    monkeypatch.setattr(MODE, "IS_STANDALONE", False)
    monkeypatch.setattr(AUTH_CONFIG, "USE_FIREBASE_TOKEN", True)
    monkeypatch.setattr(AUTH_CONFIG, "SECRET_KEY", STRONG_KEY)
    monkeypatch.setenv("ROUTING_SECRET_KEY", STRONG_KEY)
    monkeypatch.setenv("REMOTE_STORAGE_TYPE", S3)
    return monkeypatch


def test_cloud_with_strong_secrets_starts(multiuser):
    secret_validation.validate_signing_secrets()


@pytest.mark.parametrize(
    "value",
    [
        INSECURE_DEFAULT_SECRET_KEY,
        "",
        "   ",
        "12345678",  # studio/config/.env.example
        "<PLACEHOLDER-generate-with-openssl-rand-hex-32>",  # *.tfvars.example
    ],
)
def test_cloud_refuses_placeholder_secret_key(multiuser, value):
    multiuser.setattr(AUTH_CONFIG, "SECRET_KEY", value)

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        secret_validation.validate_signing_secrets()


@pytest.mark.parametrize(
    "value", [DEFAULT_ROUTING_SECRET_KEY, "", "<PLACEHOLDER-generate-new>"]
)
def test_cloud_refuses_placeholder_routing_key(multiuser, value):
    multiuser.setenv("ROUTING_SECRET_KEY", value)

    with pytest.raises(RuntimeError, match="ROUTING_SECRET_KEY"):
        secret_validation.validate_signing_secrets()


def test_cloud_refuses_unset_routing_key(multiuser):
    multiuser.delenv("ROUTING_SECRET_KEY")

    with pytest.raises(RuntimeError, match="ROUTING_SECRET_KEY"):
        secret_validation.validate_signing_secrets()


def test_error_never_contains_the_secret_value(multiuser):
    multiuser.setattr(AUTH_CONFIG, "SECRET_KEY", INSECURE_DEFAULT_SECRET_KEY)

    with pytest.raises(RuntimeError) as excinfo:
        secret_validation.validate_signing_secrets()

    assert INSECURE_DEFAULT_SECRET_KEY not in str(excinfo.value)


@pytest.mark.parametrize("name", ["SECRET_KEY", "ROUTING_SECRET_KEY"])
@pytest.mark.parametrize(
    "value",
    [
        "optinist_secret_key",  # old *.tfvars.example value
        "test-secret-key-12345",  # infrastructure/scripts/test_constants.py
        "k" * 31,
    ],
)
def test_cloud_refuses_short_keys(multiuser, name, value):
    if name == "SECRET_KEY":
        multiuser.setattr(AUTH_CONFIG, "SECRET_KEY", value)
    else:
        multiuser.setenv("ROUTING_SECRET_KEY", value)

    with pytest.raises(RuntimeError, match=name):
        secret_validation.validate_signing_secrets()

    assert secret_validation.find_weak_signing_keys().short == [name]


def test_local_multiuser_only_warns(multiuser):
    multiuser.setenv("REMOTE_STORAGE_TYPE", MOCK_STORAGE)
    multiuser.setattr(AUTH_CONFIG, "SECRET_KEY", INSECURE_DEFAULT_SECRET_KEY)
    multiuser.delenv("ROUTING_SECRET_KEY")

    secret_validation.validate_signing_secrets()

    assert secret_validation.find_weak_signing_keys().placeholders == [
        "SECRET_KEY",
        "ROUTING_SECRET_KEY",
    ]


@pytest.mark.parametrize("value", [INSECURE_DEFAULT_SECRET_KEY, "12345678", "short"])
def test_without_firebase_refuses_weak_secret_key_outside_cloud(multiuser, value):
    """The ExToken alone authenticates, so a known key makes anyone an admin."""
    multiuser.setenv("REMOTE_STORAGE_TYPE", MOCK_STORAGE)
    multiuser.setattr(AUTH_CONFIG, "USE_FIREBASE_TOKEN", False)
    multiuser.setattr(AUTH_CONFIG, "SECRET_KEY", value)
    multiuser.delenv("ROUTING_SECRET_KEY")

    with pytest.raises(RuntimeError) as excinfo:
        secret_validation.validate_signing_secrets()

    assert "SECRET_KEY" in str(excinfo.value)
    assert "ROUTING_SECRET_KEY" not in str(excinfo.value)


def test_without_firebase_outside_cloud_ignores_routing_key(multiuser):
    multiuser.setenv("REMOTE_STORAGE_TYPE", MOCK_STORAGE)
    multiuser.setattr(AUTH_CONFIG, "USE_FIREBASE_TOKEN", False)
    multiuser.delenv("ROUTING_SECRET_KEY")

    secret_validation.validate_signing_secrets()


def test_standalone_is_not_checked(multiuser):
    multiuser.setattr(MODE, "IS_STANDALONE", True)
    multiuser.setattr(AUTH_CONFIG, "SECRET_KEY", INSECURE_DEFAULT_SECRET_KEY)

    secret_validation.validate_signing_secrets()


def test_main_checks_before_starting_uvicorn(multiuser, monkeypatch):
    """With several workers, uvicorn's supervisor keeps running when a worker's
    lifespan fails, so main() must refuse before uvicorn.run is ever called."""
    from studio import __main_unit__

    multiuser.setattr(AUTH_CONFIG, "SECRET_KEY", INSECURE_DEFAULT_SECRET_KEY)
    monkeypatch.setattr("sys.argv", ["main.py", "--workers", "3"])

    with patch.object(__main_unit__.uvicorn, "run") as uvicorn_run:
        with pytest.raises(RuntimeError, match="SECRET_KEY"):
            __main_unit__.main()

    uvicorn_run.assert_not_called()
