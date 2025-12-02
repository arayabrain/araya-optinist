from datetime import datetime
from typing import Optional

import stripe
from fastapi import HTTPException, status
from sqlmodel import Session

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.subscription.constants import (
    PAYMENT_METHOD_TYPE_CARD,
    SETUP_INTENT_USAGE_OFF_SESSION,
    SubscriptionCurrencyType,
    StripeSubscriptionStatus,
)
from studio.app.common.core.subscription.subscription_service import (
    SubscriptionService,
)
from studio.app.common.schemas.subscriptions import (
    CancelSubscriptionResponse,
    CreateSetupIntentResponse,
    PaymentMethodResponse,
    UpdatePaymentMethodResponse,
    UpdateSubscriptionRequest,
    UpdateSubscriptionResponse,
)
from studio.app.common.schemas.users import User

logger = AppLogger.get_logger()


async def get_stripe_customer_by_email(email: str) -> Optional[stripe.Customer]:
    """Get Stripe customer by email"""
    try:
        stripe_customers = stripe.Customer.list(email=email, limit=1)
        return stripe_customers.data[0] if stripe_customers.data else None
    except stripe.error.StripeError as e:
        logger.error(f"Error fetching Stripe customer: {str(e)}")
        return None


class StripeService:
    """Service class for Stripe API integration and payment provider operations.

    This service acts as the integration layer between the application and Stripe's
    payment processing platform. It handles all direct communication with Stripe's
    API for payment methods, customer management, and subscription operations
    within the Stripe ecosystem.

    Primary Responsibilities:
    - Interface with Stripe API for all payment-related operations
    - Synchronize payment and subscription data between Stripe and application
    - Handle Stripe-specific payment method and customer operations
    - Process Stripe webhook events for real-time updates
    - Manage Stripe subscription schedules and metadata

    Features:
    - Payment Method Management:
        * Retrieve user's default payment method with card details
        * Create setup intents for adding new payment methods
        * Update default payment method for customer and subscriptions
        * Delete payment methods with validation checks
        * List all payment methods for a user

    - Stripe Subscription Operations:
        * Update subscriptions to different Stripe plans with scheduling
        * Cancel subscriptions with period-end scheduling on Stripe
        * Handle subscription plan upgrades and downgrades in Stripe
        * Manage subscription schedules and metadata on Stripe platform

    - Stripe Customer Management:
        * Retrieve Stripe customers by email
        * Create new Stripe customers
        * Associate payment methods with Stripe customers

    - Error Handling:
        * Comprehensive Stripe API error handling
        * HTTP exception mapping for API responses
        * Detailed logging for debugging and monitoring

    Integration Points:
    - Stripe API for payment processing
    - Stripe webhooks for real-time subscription updates
    - SubscriptionService for business logic and database operations

    All methods are async and handle both successful operations and various
    error scenarios including missing customers, invalid payment methods,
    and Stripe API errors.

    Note: This service focuses on Stripe-specific operations. For internal
    subscription business logic and database operations, use SubscriptionService."""

    @staticmethod
    async def get_default_payment_method(
        user: User,
    ) -> Optional[PaymentMethodResponse]:
        """
        Get user's default payment method
        """
        try:
            # Get user email to find Stripe customer
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            logger.info(
                f"Fetching default pymt method for user {user.id} "
                f"with email {user.email}"
            )

            # Find Stripe customer by email
            stripe_customer = await get_stripe_customer_by_email(user.email)

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

            if payment_method.type != PAYMENT_METHOD_TYPE_CARD:
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

    @staticmethod
    async def create_setup_intent(user: User) -> CreateSetupIntentResponse:
        try:
            # Get or create Stripe customer
            customer = await get_stripe_customer_by_email(user.email)

            if not customer:
                # Create new Stripe customer
                customer = stripe.Customer.create(
                    email=user.email,
                    name=getattr(user, "name", ""),
                    metadata={"user_id": str(user.id)},
                )
                logger.info(f"Created new Stripe customer for user {user.id}")

            # Create SetupIntent
            setup_intent = stripe.SetupIntent.create(
                customer=customer.id,
                payment_method_types=[PAYMENT_METHOD_TYPE_CARD],
                usage=SETUP_INTENT_USAGE_OFF_SESSION,  # For future payments
            )

            return CreateSetupIntentResponse(
                success=True,
                client_secret=setup_intent.client_secret,
                setup_intent_id=setup_intent.id,
                message="Setup intent created successfully",
            )

        except stripe.error.StripeError as e:
            logger.error(f"Stripe error creating setup intent: {str(e)}")
            raise HTTPException(
                status_code=400, detail=f"Payment processing error: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Error creating setup intent: {str(e)}")
            raise HTTPException(status_code=500, detail="Failed to create setup intent")

    @staticmethod
    async def update_default_payment_method(
        user: User, payment_method_id: str
    ) -> UpdatePaymentMethodResponse:
        """
        Update the default payment method for a user's subscription
        """
        try:
            # Get Stripe customer
            customer = await get_stripe_customer_by_email(user.email)
            if not customer:
                raise HTTPException(
                    status_code=404, detail="No Stripe customer found for user"
                )

            # Verify the payment method exists and belongs to this customer
            try:
                payment_method = stripe.PaymentMethod.retrieve(payment_method_id)
            except stripe.error.InvalidRequestError:
                raise HTTPException(status_code=404, detail="Payment method not found")

            # Attach payment method to customer if not already attached
            if payment_method.customer != customer.id:
                stripe.PaymentMethod.attach(
                    payment_method_id,
                    customer=customer.id,
                )

            # Set as default payment method for customer
            stripe.Customer.modify(
                customer.id,
                invoice_settings={"default_payment_method": payment_method_id},
            )

            # Update default payment method for active subscriptions
            subscriptions = stripe.Subscription.list(
                customer=customer.id, status=StripeSubscriptionStatus.ACTIVE
            )

            updated_subscriptions = 0
            for subscription in subscriptions.data:
                stripe.Subscription.modify(
                    subscription.id, default_payment_method=payment_method_id
                )
                updated_subscriptions += 1

            logger.info(
                f"Updated payment method for user {user.id}, "
                f"updated {updated_subscriptions} subscriptions"
            )

            return UpdatePaymentMethodResponse(
                success=True,
                message=(
                    f"Payment method updated successfully. "
                    f"Updated {updated_subscriptions} active subscriptions."
                ),
                payment_method_id=payment_method_id,
            )

        except stripe.error.StripeError as e:
            logger.error(f"Stripe error updating payment method: {str(e)}")
            raise HTTPException(
                status_code=400, detail=f"Payment processing error: {str(e)}"
            )
        except HTTPException:
            raise HTTPException(
                status_code=500, detail="Failed to update payment method"
            )
        except Exception as e:
            logger.error(f"Error updating payment method: {str(e)}")
            raise HTTPException(
                status_code=500, detail="Failed to update payment method"
            )

    @staticmethod
    async def delete_payment_method(user: User, payment_method_id: str):
        """
        Delete a payment method (cannot delete if it's default for active subscriptions)
        """
        try:
            # Get Stripe customer
            customer = await get_stripe_customer_by_email(user.email)
            if not customer:
                raise HTTPException(
                    status_code=404, detail="No Stripe customer found for user"
                )

            # Verify the payment method exists and belongs to this customer
            try:
                payment_method = stripe.PaymentMethod.retrieve(payment_method_id)
                if payment_method.customer != customer.id:
                    raise HTTPException(
                        status_code=403,
                        detail="Payment method does not belong to this user",
                    )
            except stripe.error.InvalidRequestError:
                raise HTTPException(status_code=404, detail="Payment method not found")

            # Check if this is the default payment method for customer
            if customer.invoice_settings.default_payment_method == payment_method_id:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Cannot delete the default payment method. "
                        "Please set a new default first."
                    ),
                )

            # Check if this is the default payment method for any active subscriptions
            subscriptions = stripe.Subscription.list(
                customer=customer.id, status=StripeSubscriptionStatus.ACTIVE
            )

            for subscription in subscriptions.data:
                if subscription.default_payment_method == payment_method_id:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "Cannot delete payment method that is default for "
                            "active subscription"
                        ),
                    )

            # Detach the payment method
            stripe.PaymentMethod.detach(payment_method_id)

            return {"success": True, "message": "Payment method deleted successfully"}

        except stripe.error.StripeError as e:
            logger.error(f"Stripe error deleting payment method: {str(e)}")
            raise HTTPException(
                status_code=400, detail=f"Payment processing error: {str(e)}"
            )
        except HTTPException:
            raise HTTPException(
                status_code=400, detail="Failed to delete payment method"
            )
        except Exception as e:
            logger.error(f"Error deleting payment method: {str(e)}")
            raise HTTPException(
                status_code=500, detail="Failed to delete payment method"
            )

    @staticmethod
    async def handle_get_user_payment_methods(user: User):
        """
        Get user's payment methods with last 4 digits and card brand
        """
        try:
            # Get user email to find Stripe customer
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            logger.info(
                f"Fetching payment methods for user {user.id} with email {user.email}"
            )

            # Find Stripe customer by email
            stripe_customers = stripe.Customer.list(email=user.email, limit=1)

            if not stripe_customers.data:
                logger.info(f"No Stripe customer found for user {user.email}")
                return []

            customer = stripe_customers.data[0]

            # Get all payment methods for this customer
            payment_methods = stripe.PaymentMethod.list(
                customer=customer.id, type=PAYMENT_METHOD_TYPE_CARD
            )

            result = []
            for pm in payment_methods.data:
                card = pm.card
                payment_method_response = PaymentMethodResponse(
                    id=pm.id,
                    last4=card.last4,
                    brand=card.brand,
                    exp_month=card.exp_month,
                    exp_year=card.exp_year,
                    is_default=pm.id
                    == customer.invoice_settings.default_payment_method,
                )
                result.append(payment_method_response)

            return result

        except stripe.error.StripeError as e:
            logger.error(
                f"Stripe error fetching payment methods for user {user.id}: {str(e)}"
            )
            raise HTTPException(
                status_code=400,
                detail=f"Failed to fetch payment methods from Stripe: {str(e)}",
            )
        except Exception as e:
            logger.error(f"Error fetching payment methods for user {user.id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch payment methods: {str(e)}",
            )

    @staticmethod
    async def handle_update_user_subscription(
        db: Session, user: User, request: UpdateSubscriptionRequest
    ):
        """
        Update user's subscription to a different plan - webhook-driven database updates
        """
        try:
            # Get the new plan details
            new_plan = SubscriptionService.get_plan_by_id(db, request.new_plan_id)
            if not new_plan:
                raise HTTPException(
                    status_code=404, detail="New subscription plan not found"
                )

            # Get current user subscription
            current_subscription_result = SubscriptionService.get_user_subscription(
                db, user.id
            )
            if not current_subscription_result:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        "No active subscription found. Please create a new subscription"
                    ),
                )

            sub_data, current_plan = current_subscription_result

            # Check if user is trying to "update" to the same plan
            if current_plan.id == new_plan.id:
                raise HTTPException(
                    status_code=400, detail="User is already subscribed to this plan"
                )

            # Get Stripe customer
            customer = await get_stripe_customer_by_email(user.email)
            if not customer:
                raise HTTPException(
                    status_code=404, detail="No Stripe customer found for user"
                )

            # Get active or trial Stripe subscription
            # First try to find active subscription
            stripe_subscriptions = stripe.Subscription.list(
                customer=customer.id,
                status=StripeSubscriptionStatus.ACTIVE,
                limit=1,
            )

            # If no active subscription found, check for trial subscription
            if not stripe_subscriptions.data:
                stripe_subscriptions = stripe.Subscription.list(
                    customer=customer.id,
                    status=StripeSubscriptionStatus.TRIAL,
                    limit=1,
                )

            if not stripe_subscriptions.data:
                raise HTTPException(
                    status_code=404,
                    detail="No active or Trial Stripe subscription found",
                )

            stripe_subscription = stripe_subscriptions.data[0]

            # Prepare currency for Stripe
            currency = SubscriptionCurrencyType(new_plan.currency).get_currency_string()

            # Create new price in Stripe for the new plan
            stripe_price = stripe.Price.create(
                currency=currency,
                unit_amount=new_plan.price,
                recurring={"interval": "month"},
                product_data={
                    "name": new_plan.name,
                    "metadata": {"plan_id": str(new_plan.id)},
                },
            )

            logger.info(f"Processing scheduled subscription change for user {user.id}")

            # Get the current period end from the subscription items
            current_period_end = stripe_subscription["items"]["data"][0][
                "current_period_end"
            ]

            logger.info(f"Current period end timestamp: {current_period_end}")
            logger.info(
                f"Current period end date: {datetime.fromtimestamp(current_period_end)}"
            )

            # Schedule change at period end using proper Stripe schedules
            current_period_end = stripe_subscription["items"]["data"][0][
                "current_period_end"
            ]

            # Cancel current subscription at period end
            stripe.Subscription.modify(
                stripe_subscription.id, cancel_at_period_end=True
            )

            # Create a new subscription schedule that starts when current ends
            stripe.SubscriptionSchedule.create(
                customer=stripe_subscription.customer,
                start_date=current_period_end,  # Start when current subscription ends
                phases=[
                    {
                        # New phase - switch to new plan
                        "items": [
                            {
                                "price": stripe_price.id,
                                "quantity": 1,
                            }
                        ],
                        # This phase continues indefinitely
                    },
                ],
                metadata={
                    "user_id": str(user.id),
                    "new_plan_id": str(new_plan.id),
                    "old_plan_id": str(current_plan.id),
                    "scheduled_change": "true",
                },
            )

            change_date = datetime.fromtimestamp(current_period_end)
            message = (
                f"Subscription will change to {new_plan.name} on "
                f"{change_date.strftime('%Y-%m-%d')}"
            )
            effective_date = int(current_period_end)

            # Database will be updated via subscription_schedule.updated and
            # subscription_schedule.released webhooks

            # Determine if this is an upgrade or downgrade
            plan_change_type = (
                "upgrade" if new_plan.price > current_plan.price else "downgrade"
            )

            logger.info(
                f"subscription change for user {user.id} "
                f"from plan {current_plan.id} to plan {new_plan.id} "
                f"({plan_change_type})"
            )

            return UpdateSubscriptionResponse(
                success=True,
                message=message,
                old_plan_name=current_plan.name,
                new_plan_name=new_plan.name,
                change_type=plan_change_type,
                effective_date=effective_date,
                next_billing_date=int(current_period_end),
                prorated_amount=("Check latest invoice for proration details"),
            )

        except stripe.error.StripeError as e:
            logger.error(f"Stripe error updating subscription: {str(e)}")
            raise HTTPException(
                status_code=400, detail=f"Payment processing error: {str(e)}"
            )
        except HTTPException:
            raise HTTPException(status_code=500, detail="Failed to update subscription")
        except Exception as e:
            logger.error(f"Error updating subscription for user {user.id}: {str(e)}")
            raise HTTPException(
                status_code=500, detail=f"Failed to update subscription: {str(e)}"
            )

    @staticmethod
    async def handle_cancel_user_subscription(
        db: Session, user: User
    ) -> CancelSubscriptionResponse:
        # Get current user subscription
        current_subscription_result = SubscriptionService.get_user_subscription(
            db, user.id
        )
        if not current_subscription_result:
            raise HTTPException(
                status_code=404, detail="No active subscription found to cancel"
            )

        # Get Stripe customer
        customer = await get_stripe_customer_by_email(user.email)
        if not customer:
            raise HTTPException(
                status_code=404, detail="No Stripe customer found for user"
            )

        # Get active or trial Stripe subscription
        # First try to find active subscription
        stripe_subscriptions = stripe.Subscription.list(
            customer=customer.id, status=StripeSubscriptionStatus.ACTIVE, limit=1
        )

        # If no active subscription found, check for trial subscription
        if not stripe_subscriptions.data:
            stripe_subscriptions = stripe.Subscription.list(
                customer=customer.id, status=StripeSubscriptionStatus.TRIAL, limit=1
            )

        if not stripe_subscriptions.data:
            raise HTTPException(
                status_code=404,
                detail="No active or trial Stripe subscription found",
            )

        stripe_subscription = stripe_subscriptions.data[0]

        logger.info(f"Scheduling cancellation at period end for user {user.id}")

        current_period_end = stripe_subscription["items"]["data"][0][
            "current_period_end"
        ]

        # Handle existing schedule if present
        existing_schedule_id = stripe_subscription.get("schedule")
        if existing_schedule_id:
            try:
                # Cancel any existing schedule
                stripe.SubscriptionSchedule.cancel(existing_schedule_id)
                logger.info(f"Cancelled existing schedule: {existing_schedule_id}")

                # Get the subscription again after cancelling schedule
                stripe_subscription = stripe.Subscription.retrieve(
                    stripe_subscription.id
                )
            except Exception as e:
                logger.warning(f"Could not cancel schedule: {e}")

        # Set subscription to cancel at period end
        stripe.Subscription.modify(
            stripe_subscription.id,
            cancel_at_period_end=True,
            metadata={
                **stripe_subscription.metadata,
                "cancellation_requested": "true",
                "cancellation_requested_at": str(
                    int(SubscriptionService.get_current_datetime().timestamp())
                ),
            },
        )

        SubscriptionService.update_scheduled_downgrade(db, user.id, True)

        # Database will be updated via customer.subscription.updated webhook

        access_until_date = datetime.fromtimestamp(current_period_end)
        message = (
            f"Subscription will be cancelled on "
            f"{access_until_date.strftime('%Y-%m-%d')}. "
            f"You will retain access until then."
        )

        logger.info(
            f"Successfully scheduled cancellation for user {user.id} " f"at period end"
        )

        return CancelSubscriptionResponse(
            success=True,
            message=message,
            cancellation_date=access_until_date.strftime("%Y-%m-%d"),
            access_until=access_until_date.strftime("%Y-%m-%d %H:%M:%S"),
        )
