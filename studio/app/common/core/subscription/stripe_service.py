from typing import Optional

import stripe
from app.common.core.auth import current_user
from app.common.core.logger import logger
from fastapi import HTTPException, status


class PaymentMethodResponse:
    def __init__(
        self,
        id: str,
        last4: str,
        brand: str,
        exp_month: int,
        exp_year: int,
        is_default: bool,
    ):
        self.id = id
        self.last4 = last4
        self.brand = brand
        self.exp_month = exp_month
        self.exp_year = exp_year
        self.is_default = is_default


async def get_stripe_customer_by_email(email: str) -> Optional[stripe.Customer]:
    """Get Stripe customer by email"""
    try:
        stripe_customers = stripe.Customer.list(email=email, limit=1)
        return stripe_customers.data[0] if stripe_customers.data else None
    except stripe.error.StripeError as e:
        logger.error(f"Error fetching Stripe customer: {str(e)}")
        return None


async def get_default_payment_method():
    """
    Get user's default payment method
    """
    try:
        # Get user email to find Stripe customer
        user = current_user
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        logger.info(
            f"Fetching default pymt method for user {user.id} with email {user.email}"
        )

        # Find Stripe customer by email
        stripe_customer = await _get_stripe_customer_by_email(user.email)

        if not stripe_customer:
            logger.info(f"No Stripe customer found for user {user.id}")
            return None

        # Get default payment method
        default_pm_id = stripe_customer.invoice_settings.default_payment_method
        if not default_pm_id:
            logger.info(f"No default payment method set for user {user.id}")
            return None

        # Retrieve the payment method details
        payment_method = stripe.PaymentMethod.retrieve(default_pm_id)

        if payment_method.type != "card":
            logger.info(f"Default payment method is not a card for user {user.id}")
            return None

        card = payment_method.card
        return PaymentMethodResponse(
            id=payment_method.id,
            last4=card.last4,
            brand=card.brand,
            exp_month=card.exp_month,
            exp_year=card.exp_year,
            is_default=True,
        )

    except stripe.error.StripeError as e:
        logger.error(
            f"Stripe error when fetching default payment method for user "
            f"{user.id}: {str(e)}"
        )
        raise HTTPException(
            status_code=400,
            detail=f"Failed to fetch default payment method from Stripe: {str(e)}",
        )
    except Exception as e:
        logger.error(
            f"Error fetching default payment method for user {user.id}: {str(e)}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch default payment method: {str(e)}",
        )
