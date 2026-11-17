"""A Firebase token for an unverified email is refused on every verification path.

Firebase signs a user in before the email is verified, so a client can hold a
valid ID token for an address it does not own. Only the login route checked
``email_verified``; a token taken straight from Firebase skipped it and could
read the billing data of a deleted user who had the same email.
"""

from unittest.mock import MagicMock, patch

import pytest

from studio.app.common.core.auth import auth_helper

MODULE = "studio.app.common.core.auth.auth_helper"


@pytest.fixture(params=[{}, {"email_verified": False}], ids=["missing", "false"])
def unverified(request):
    auth_helper._token_cache.clear()
    with patch(f"{MODULE}.firebase_auth.verify_id_token") as mock:
        mock.return_value = {"uid": "uid-1", **request.param}
        yield mock
    auth_helper._token_cache.clear()


class TestEveryPathRefusesAnUnverifiedEmail:
    def test_the_sync_path(self, unverified):
        assert auth_helper._verify_firebase_token_sync("t") is None

    def test_the_credential_path(self, unverified):
        uid, err = auth_helper.extract_uid_from_firebase_credential(
            MagicMock(credentials="t")
        )
        assert uid is None
        assert "not verified" in err

    def test_the_middleware_path(self, unverified):
        uid, err = auth_helper.extract_uid_from_firebase_jwt("t")
        assert uid is None and err

    def test_the_request_path(self, unverified):
        request = MagicMock()
        request.headers = {"Authorization": "Bearer t"}
        with patch(f"{MODULE}.AUTH_CONFIG") as config:
            config.USE_FIREBASE_TOKEN = True
            assert auth_helper.extract_uid_from_request(request) is None

    def test_a_refused_token_is_not_cached(self, unverified):
        auth_helper.extract_uid_from_firebase_jwt("t")
        assert auth_helper._get_cached_uid("t") is None


def test_a_verified_email_passes():
    with patch(f"{MODULE}.firebase_auth.verify_id_token") as mock:
        mock.return_value = {"uid": "uid-1", "email_verified": True}
        assert auth_helper._verify_id_token("t")["uid"] == "uid-1"
