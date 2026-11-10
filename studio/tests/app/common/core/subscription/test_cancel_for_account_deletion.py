"""Account deletion cancels what Stripe can still bill, whatever the local row says.

After deletion nobody can sign in to cancel, so a subscription left live here
keeps charging the card.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import stripe
from fastapi import HTTPException

from studio.app.common.core.subscription.stripe_service import StripeService

MODULE = "studio.app.common.core.subscription.stripe_service"


def subscription(sub_id, status, schedule=None):
    return stripe.Subscription.construct_from(
        {"id": sub_id, "status": status, "schedule": schedule}, "sk_test_unit"
    )


@pytest.fixture
def user():
    user = MagicMock()
    user.id = 7
    user.email = "deleted@example.com"
    return user


@pytest.fixture
def stripe_calls():
    with patch(f"{MODULE}.get_stripe_customer", new=AsyncMock()) as customer, patch(
        f"{MODULE}.get_or_create_stripe_customer", new=AsyncMock()
    ) as get_or_create, patch(f"{MODULE}.stripe.Subscription.list") as listing, patch(
        f"{MODULE}.stripe.Subscription.cancel"
    ) as cancel, patch(
        f"{MODULE}.stripe.SubscriptionSchedule.cancel"
    ) as cancel_schedule, patch(
        f"{MODULE}.SubscriptionService.get_user_subscription", return_value=None
    ), patch(
        f"{MODULE}.SubscriptionService.update_scheduled_downgrade"
    ):
        yield {
            "customer": customer,
            "get_or_create": get_or_create,
            "list": listing,
            "cancel": cancel,
            "cancel_schedule": cancel_schedule,
        }


@pytest.mark.asyncio
async def test_every_billable_subscription_is_cancelled_without_a_local_row(
    user, stripe_calls
):
    """A failed renewal leaves Stripe past_due while the local expiration has
    passed; Stripe's retry would then charge the deleted account."""
    stripe_calls["customer"].return_value = MagicMock(id="cus_1")
    stripe_calls["list"].return_value = MagicMock(
        data=[
            subscription("sub_past_due", "past_due"),
            subscription("sub_active", "active", schedule="sub_sched_1"),
            subscription("sub_done", "canceled"),
            subscription("sub_expired", "incomplete_expired"),
        ]
    )

    await StripeService.handle_cancel_user_subscription(
        MagicMock(), user, immediate=True
    )

    stripe_calls["list"].assert_called_once_with(
        customer="cus_1", status="all", limit=100
    )
    cancelled = [c.args[0] for c in stripe_calls["cancel"].call_args_list]
    assert cancelled == ["sub_past_due", "sub_active"]
    stripe_calls["cancel_schedule"].assert_called_once_with("sub_sched_1")


@pytest.mark.asyncio
async def test_a_user_without_a_stripe_customer_creates_none(user, stripe_calls):
    """An admin-granted plan has a local row but no Stripe history; deletion
    must not create a Stripe customer for an account it is removing."""
    stripe_calls["customer"].return_value = None

    with pytest.raises(HTTPException) as raised:
        await StripeService.handle_cancel_user_subscription(
            MagicMock(), user, immediate=True
        )

    assert raised.value.status_code == 404
    stripe_calls["get_or_create"].assert_not_called()
    stripe_calls["list"].assert_not_called()
    stripe_calls["cancel"].assert_not_called()
