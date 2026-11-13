"""PUT /users/me refuses an email change.

No ownership check runs on that route, and the Stripe customer lookup falls
back to email, so a changed email could pick up a deleted user's customer.
The Account page only edits the name and sends the current email back.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from studio.app.common.routers.users_me import update_me
from studio.app.common.schemas.users import SelfUserUpdate

MODULE = "studio.app.common.routers.users_me"


def _user(email="owner@example.com"):
    return SimpleNamespace(id=7, email=email, organization=SimpleNamespace(id=1))


@pytest.mark.asyncio
async def test_a_different_email_is_refused():
    with patch(f"{MODULE}.crud_users.update_user", new=AsyncMock()) as update:
        with pytest.raises(HTTPException) as exc:
            await update_me(
                SelfUserUpdate(name="n", email="deleted@example.com"),
                current_user=_user(),
                db=MagicMock(),
            )
    assert exc.value.status_code == 400
    update.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stored, sent",
    [
        ("owner@example.com", "owner@example.com"),
        # EmailStr lowercases the domain, so the name edit sends it back changed.
        ("owner@Example.COM", "owner@Example.COM"),
        ("owner@example.com", None),
    ],
    ids=["same", "domain-case", "omitted"],
)
async def test_the_name_edit_still_saves(stored, sent):
    with patch(f"{MODULE}.crud_users.update_user", new=AsyncMock()) as update:
        await update_me(
            SelfUserUpdate(name="new name", email=sent),
            current_user=_user(stored),
            db=MagicMock(),
        )
    update.assert_awaited_once()
