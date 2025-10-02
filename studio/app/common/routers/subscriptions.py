from datetime import datetime
from typing import List, Optional

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from studio.app.common.core.auth.auth_dependencies import get_current_user
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.subscription.stripe_service import (
    StripeService,
    StripeSubscriptionStatus,
    get_stripe_customer_by_email,
)
from studio.app.common.core.subscription.subscription_service import (
    SubscriptionCurrency,
    SubscriptionCurrencyType,
    SubscriptionService,
)
from studio.app.common.core.subscription.webhook_service import WebhookService
from studio.app.common.db.database import get_db
from studio.app.common.schemas.checkouts import CheckoutSessionRequest
from studio.app.common.schemas.subscriptions import (
    CancelSubscriptionResponse,
    CreateCheckoutSessionRequest,
    CreateCheckoutSessionResponse,
    CreateSetupIntentResponse,
    InvoiceResponse,
    PaymentMethodResponse,
    SubscriptionPlanResponse,
    UpdatePaymentMethodResponse,
    UpdateSubscriptionRequest,
    UpdateSubscriptionResponse,
    UserSubscriptionResponse,
)
from studio.app.common.schemas.users import User

# Lazy initialization of Stripe configuration
_stripe_initialized = False


def _ensure_stripe_initialized():
    """Lazy initialization of Stripe API key"""
    global _stripe_initialized
    if not _stripe_initialized:
        try:
            stripe.api_key = SubscriptionService.get_stripe_key()
            _stripe_initialized = True
        except ValueError as e:
            logger.warning(f"Stripe not initialized: {e}")
            # Don't raise here - allow module to load for tests


# Load callback URL at module level (doesn't require secrets for module import)
try:
    STRIPE_CALLBACK_URL = SubscriptionService.get_base_url()
except ValueError:
    STRIPE_CALLBACK_URL = None  # Will be set when needed


def stripe_dependency():
    """Dependency to ensure Stripe is initialized before handling requests"""
    _ensure_stripe_initialized()


router = APIRouter(
    prefix="/api/subsc",
    tags=["Subscriptions"],
    dependencies=[Depends(stripe_dependency)],
)
webhook_router = APIRouter(
    prefix="/api/subsc/webhooks",
    tags=["Subscription Webhooks"],
    dependencies=[Depends(stripe_dependency)],
)
logger = AppLogger.get_logger()


@router.get("/mgmts/plans", response_model=List[SubscriptionPlanResponse])
def get_subscription_plans(db: Session = Depends(get_db)):
    try:
        plans = SubscriptionService.get_active_plans(db)

        if not plans:
            logger.warning("No subscription plans found")
            return []

        result: List[SubscriptionPlanResponse] = []
        for plan in plans:
            try:
                plan_response = SubscriptionPlanResponse(
                    id=plan.id,
                    name=plan.name,
                    price=plan.price,
                    billing_cycle=plan.billing_cycle,
                    features=plan.features,
                    currency=plan.currency,
                    status=plan.status,
                    created_at=plan.created_at,
                )
                result.append(plan_response)

            except Exception as plan_error:
                logger.error(f"Error processing plan {plan.id}: {plan_error}")
                # Skip this plan and continue with others
                continue

        return result

    except Exception as e:
        logger.error(f"Error fetching subscription plans: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch subscription plans: {str(e)}"
        )


@router.get(
    "/mgmts",
    response_model=Optional[UserSubscriptionResponse],
)
async def get_user_subscription(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get user's current active subscription
    """
    try:
        # Get the most recent active subscription
        subscription = SubscriptionService.get_user_subscription(db, current_user.id)
        logger.info(f"Fetched subscription for user {current_user.id}: {subscription}")

        if subscription is None:
            # Check if user has any expired subscriptions
            expired_subscription = SubscriptionService.get_user_expired_subscription(
                db, current_user.id
            )

            if expired_subscription:
                sub_data, plan_data, _ = expired_subscription
                return UserSubscriptionResponse(
                    id=sub_data.id,
                    user_id=sub_data.user_id,
                    plan_id=sub_data.plan_id,
                    created_at=sub_data.created_at,
                    updated_at=sub_data.updated_at,
                    plan_name=plan_data.name,
                    plan_price=plan_data.price,
                    expiration=sub_data.expiration,
                    is_expired=True,
                    scheduled_downgrade=sub_data.scheduled_downgrade,
                )

            return None

        # If we get here, result is not None, so we can safely unpack
        subscription, subscription_plans = subscription
        sub_data, plan_data = subscription, subscription_plans
        return UserSubscriptionResponse(
            id=sub_data.id,
            user_id=sub_data.user_id,
            plan_id=sub_data.plan_id,
            created_at=sub_data.created_at,
            updated_at=sub_data.updated_at,
            plan_name=plan_data.name,
            plan_price=plan_data.price,
            expiration=sub_data.expiration,
            is_expired=False,
            scheduled_downgrade=sub_data.scheduled_downgrade,
        )
    except Exception as e:
        logger.error(
            f"Error fetching subscription for user {current_user.id}: {str(e)}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch user subscription: {str(e)}",
        )


@router.put("/mgmts", response_model=UpdateSubscriptionResponse)
async def update_user_subscription(
    request: UpdateSubscriptionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
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
            db, current_user.id
        )
        if not current_subscription_result:
            raise HTTPException(
                status_code=404,
                detail="No active subscription found. Please create a new subscription",
            )

        sub_data, current_plan = current_subscription_result

        # Check if user is trying to "update" to the same plan
        if current_plan.id == new_plan.id:
            raise HTTPException(
                status_code=400, detail="User is already subscribed to this plan"
            )

        # Get Stripe customer
        customer = await get_stripe_customer_by_email(current_user.email)
        if not customer:
            raise HTTPException(
                status_code=404, detail="No Stripe customer found for user"
            )

        # Get active Stripe subscription
        stripe_subscriptions = stripe.Subscription.list(
            customer=customer.id, status=StripeSubscriptionStatus.ACTIVE, limit=1
        )

        if not stripe_subscriptions.data:
            raise HTTPException(
                status_code=404, detail="No active Stripe subscription found"
            )

        stripe_subscription = stripe_subscriptions.data[0]

        # Prepare currency for Stripe
        currency = new_plan.currency
        if currency == SubscriptionCurrencyType.USD.value:
            currency = SubscriptionCurrency.USD.value
        elif currency == SubscriptionCurrencyType.JPY.value:
            currency = SubscriptionCurrency.JPY.value
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

        logger.info(
            f"Processing scheduled subscription change for user {current_user.id}"
        )

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
        stripe.Subscription.modify(stripe_subscription.id, cancel_at_period_end=True)

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
                "user_id": str(current_user.id),
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
            f"subscription change for user {current_user.id} "
            f"from plan {current_plan.id} to plan {new_plan.id} ({plan_change_type})"
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
        raise
    except Exception as e:
        logger.error(
            f"Error updating subscription for user {current_user.id}: {str(e)}"
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to update subscription: {str(e)}"
        )


@router.delete("/mgmts/cancel", response_model=CancelSubscriptionResponse)
async def cancel_user_subscription(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Cancel user's subscription at period end
    - Subscription will be cancelled when current billing period ends
    - User retains access until then
    - Database updates handled via webhook
    """
    try:
        # Get current user subscription
        current_subscription_result = SubscriptionService.get_user_subscription(
            db, current_user.id
        )
        if not current_subscription_result:
            raise HTTPException(
                status_code=404, detail="No active subscription found to cancel"
            )

        sub_data, current_plan = current_subscription_result

        # Get Stripe customer
        customer = await get_stripe_customer_by_email(current_user.email)
        if not customer:
            raise HTTPException(
                status_code=404, detail="No Stripe customer found for user"
            )

        # Get active Stripe subscription
        stripe_subscriptions = stripe.Subscription.list(
            customer=customer.id, status=StripeSubscriptionStatus.ACTIVE, limit=1
        )

        if not stripe_subscriptions.data:
            raise HTTPException(
                status_code=404, detail="No active Stripe subscription found"
            )

        stripe_subscription = stripe_subscriptions.data[0]

        logger.info(f"Scheduling cancellation at period end for user {current_user.id}")

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
                "cancellation_requested_at": str(int(datetime.utcnow().timestamp())),
            },
        )

        SubscriptionService.update_scheduled_downgrade(db, current_user.id, True)

        # Database will be updated via customer.subscription.updated webhook

        access_until_date = datetime.fromtimestamp(current_period_end)
        message = (
            f"Subscription will be cancelled on "
            f"{access_until_date.strftime('%Y-%m-%d')}. "
            f"You will retain access until then."
        )

        logger.info(
            f"Successfully scheduled cancellation for user {current_user.id} "
            f"at period end"
        )

        return CancelSubscriptionResponse(
            success=True,
            message=message,
            cancellation_date=access_until_date.strftime("%Y-%m-%d"),
            access_until=access_until_date.strftime("%Y-%m-%d %H:%M:%S"),
        )

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error cancelling subscription: {str(e)}")
        raise HTTPException(
            status_code=400, detail=f"Payment processing error: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error cancelling subscription for user {current_user.id}: {str(e)}"
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to cancel subscription: {str(e)}"
        )


@router.post("/mgmts/reactivate/{user_id}", response_model=CancelSubscriptionResponse)
async def reactivate_user_subscription(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Cancel the scheduled cancellation of user's subscription
    - Removes the cancellation scheduled at period end
    - User subscription will continue normally
    """
    try:
        # Verify user access
        if current_user.id != user_id:
            raise HTTPException(
                status_code=403,
                detail="Access denied: Can only manage your own subscription",
            )

        # Get current user subscription
        current_subscription_result = SubscriptionService.get_user_subscription_plan(
            db, user_id
        )
        if not current_subscription_result:
            raise HTTPException(status_code=404, detail="No active subscription found")

        sub_data, current_plan = current_subscription_result

        # Get Stripe customer
        customer = await get_stripe_customer_by_email(current_user.email)
        if not customer:
            raise HTTPException(
                status_code=404, detail="No Stripe customer found for user"
            )

        # Get active Stripe subscription
        stripe_subscriptions = stripe.Subscription.list(
            customer=customer.id, status="active", limit=1
        )

        if not stripe_subscriptions.data:
            raise HTTPException(
                status_code=404, detail="No active Stripe subscription found"
            )

        stripe_subscription = stripe_subscriptions.data[0]

        # Check if subscription is scheduled for cancellation
        if not stripe_subscription.cancel_at_period_end:
            raise HTTPException(
                status_code=400, detail="Subscription is not scheduled for cancellation"
            )

        logger.info(f"Cancelling scheduled cancellation for user {user_id}")

        # Remove the scheduled cancellation
        stripe.Subscription.modify(
            stripe_subscription.id,
            cancel_at_period_end=False,
            metadata={
                **stripe_subscription.metadata,
                "cancellation_requested": "false",
                "reactivation_requested_at": str(int(datetime.utcnow().timestamp())),
            },
        )

        # Update database to remove scheduled downgrade
        SubscriptionService.update_scheduled_downgrade(db, user_id, False)

        message: str = (
            "Subscription cancellation has been cancelled. "
            "Your subscription will continue normally."
        )

        logger.info(f"Successfully cancelled cancellation for user {user_id}")

        return CancelSubscriptionResponse(
            success=True,
            message=message,
            cancellation_date="",
            access_until="Subscription will continue normally",
        )

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error cancelling cancellation: {str(e)}")
        raise HTTPException(
            status_code=400, detail=f"Payment processing error: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cancelling cancellation for user {user_id}: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Failed to cancel cancellation: {str(e)}"
        )


@router.get("/payment-methods", response_model=List[PaymentMethodResponse])
async def get_user_payment_methods(
    current_user: User = Depends(get_current_user),
):
    """
    Get user's payment methods with last 4 digits and card brand
    """
    try:
        # Get user email to find Stripe customer
        user = current_user
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
        payment_methods = stripe.PaymentMethod.list(customer=customer.id, type="card")

        result = []
        for pm in payment_methods.data:
            card = pm.card
            payment_method_response = PaymentMethodResponse(
                id=pm.id,
                last4=card.last4,
                brand=card.brand,
                exp_month=card.exp_month,
                exp_year=card.exp_year,
                is_default=pm.id == customer.invoice_settings.default_payment_method,
            )
            result.append(payment_method_response)

        return result

    except stripe.error.StripeError as e:
        logger.error(
            f"Stripe error when fetching payment methods for user {user.id}: {str(e)}"
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


@router.get("/payment-methods/default", response_model=Optional[PaymentMethodResponse])
async def get_user_default_payment_method(
    current_user: User = Depends(get_current_user),
):
    return await StripeService.get_default_payment_method(current_user)


@router.post("/payment-methods/setup-intent", response_model=CreateSetupIntentResponse)
async def setup_intent(
    current_user: User = Depends(get_current_user),
):
    # await StripeService.create_setup_intent(current_user)
    """
    This endpoint is currently not in use
    """
    raise HTTPException(
        status_code=501,
        detail="This API endpoint is not implemented and currently not in use",
    )


@router.put("/payment-methods", response_model=UpdatePaymentMethodResponse)
async def update_default_payment_method(
    payment_method_id: str,
    current_user: User = Depends(get_current_user),
):
    # return await StripeService.update_default_payment_method(
    #     current_user, payment_method_id
    # )
    """
    This endpoint is currently not in use
    """
    raise HTTPException(
        status_code=501,
        detail="This API endpoint is not implemented and currently not in use",
    )


@router.delete("/payment-methods/{payment_method_id}")
async def delete_payment_method(
    payment_method_id: str,
    current_user: User = Depends(get_current_user),
):
    # return await StripeService.delete_payment_method(current_user, payment_method_id)
    """
    This endpoint is currently not in use
    """
    raise HTTPException(
        status_code=501,
        detail="This API endpoint is not implemented and currently not in use",
    )


@router.post(
    "/checkout/create-checkout-session", response_model=CreateCheckoutSessionResponse
)
async def create_checkout_session(
    request: CreateCheckoutSessionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await StripeService.handle_checkout_session(db, request, current_user)


@router.post("/checkout/validate-checkout-session", response_model=bool)
async def validate_checkout_session(
    request: CheckoutSessionRequest,
):
    """
    Validate a Stripe checkout session ID
    """
    try:
        # Retrieve the session from Stripe
        session = stripe.checkout.Session.retrieve(request.session_id)

        # Check if the session is complete and paid
        if session.payment_status == "paid" and session.status == "complete":
            return True
        else:
            return False

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error validating checkout session: {str(e)}")
        raise HTTPException(
            status_code=400, detail=f"Failed to validate checkout session: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Error validating checkout session: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/checkout/failed-checkout-session", response_model=bool)
async def validate_failed_checkout_session(
    request: CheckoutSessionRequest,
):
    """
    Validate a Stripe checkout session ID for FAILED page
    Returns True if session exists and is in a failed/incomplete state
    """
    try:
        # Retrieve the session from Stripe
        session = stripe.checkout.Session.retrieve(request.session_id)

        # Check if the session exists and is in a legitimate failed state
        # Valid failed states: expired, open with unpaid status
        if session.status == "expired" or (
            session.status == "open" and session.payment_status == "unpaid"
        ):
            return True
        else:
            return False

    except stripe.error.InvalidRequestError:
        # Session doesn't exist or is invalid
        return False
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error validating failed checkout session: {str(e)}")
        raise HTTPException(
            status_code=400, detail=f"Failed to validate checkout session: {str(e)}"
        )


@router.get("/invoices/{user_id}", response_model=List[InvoiceResponse])
async def get_user_invoices(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get user's invoices from Stripe
    """
    if current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this user's invoices",
        )

    try:
        # Get user email to find Stripe customer
        subscription_user, user = SubscriptionService.get_user_subscription_by_user_id(
            db, user_id
        )
        logger.info(f"Fetched user subscription record: {subscription_user}")
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        logger.info(f"Fetching invoices for user {user_id} with email {user.email}")

        # Find Stripe customer by email
        stripe_customers = stripe.Customer.list(email=user.email, limit=1)

        if not stripe_customers.data:
            logger.info(f"No Stripe customer found for user {user_id}")
            return []

        customer = stripe_customers.data[0]

        # Get all invoices for this customer
        invoices = stripe.Invoice.list(
            customer=customer.id,
            limit=100,  # Adjust limit as needed
            expand=["data.subscription"],  # Expand subscription data for more details
        )

        result = []
        for invoice in invoices.data:
            # Convert Stripe invoice to our response format
            invoice_response = InvoiceResponse(
                id=invoice.id,
                date=datetime.fromtimestamp(invoice.created).isoformat(),
                total=f"${(invoice.total / 100):.2f}",  # Convert cents to dollars
                status=invoice.status.title(),  # Capitalize status
                invoice_url=invoice.hosted_invoice_url or invoice.invoice_pdf or "",
                amount_paid=invoice.amount_paid,
                amount_due=invoice.amount_due,
                currency=invoice.currency.upper(),
                description=invoice.description or "Subscription payment",
                period_start=(
                    datetime.fromtimestamp(invoice.period_start).isoformat()
                    if invoice.period_start
                    else None
                ),
                period_end=(
                    datetime.fromtimestamp(invoice.period_end).isoformat()
                    if invoice.period_end
                    else None
                ),
            )
            result.append(invoice_response)

        # Sort by date (newest first)
        result.sort(key=lambda x: x.date, reverse=True)

        return result

    except stripe.error.StripeError as e:
        logger.error(
            f"Stripe error when fetching invoices for user {user_id}: {str(e)}"
        )
        raise HTTPException(
            status_code=400,
            detail=f"Failed to fetch invoices from Stripe: {str(e)}",
        )
    except Exception as e:
        logger.error(f"Error fetching invoices for user {user_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch invoices: {str(e)}",
        )
    except Exception as e:
        logger.error(f"Error validating failed checkout session: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@webhook_router.post("/stripe")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    """Handle Stripe webhooks for subscription events"""
    try:
        # Get raw body and signature header
        body = await request.body()
        sig_header = request.headers.get("stripe-signature")

        logger.info(f"Webhook received - Body length: {len(body)}")
        logger.info(f"Signature header: {sig_header[:50] if sig_header else 'None'}...")

        # Your webhook endpoint secret from Stripe Dashboard
        endpoint_secret = WebhookService.get_webhook_secret()

        secret_display = "***" + endpoint_secret[-4:] if endpoint_secret else "None"
        logger.info(f"Using webhook secret: {secret_display}")

        # Verify the webhook signature
        try:
            event = stripe.Webhook.construct_event(body, sig_header, endpoint_secret)
            logger.info("Webhook signature verified successfully")
        except ValueError as e:
            logger.error(f"Invalid payload: {str(e)}")
            raise HTTPException(status_code=400, detail="Invalid payload")
        except stripe.error.SignatureVerificationError as e:
            logger.error(f"Invalid signature: {str(e)}")
            raise HTTPException(status_code=400, detail="Invalid signature")

        # Now use the verified event data
        event_type = event["type"]
        data = event["data"]["object"]

        logger.info(f"Processing event type: {event_type}")

        if event_type == "checkout.session.completed":
            logger.info("Handling checkout.session.completed")
            WebhookService.handle_checkout_completed(db, data)

        elif event_type == "invoice.payment_failed":
            logger.info("Handling invoice.payment_failed")
            WebhookService.handle_payment_failed(db, data)

        elif event_type == "customer.subscription.deleted":
            logger.info("Handling customer.subscription.deleted")
            WebhookService.handle_subscription_cancelled(db, data)

        elif event_type == "subscription_schedule.released":
            logger.info("Handling subscription_schedule.released")
            WebhookService.handle_subscription_schedule_released(db, data)

        elif event_type == "invoice.payment_succeeded":
            logger.info("Handling invoice.payment_succeeded")
            WebhookService.handle_subscription_payment_succeeded(db, data)

        else:
            logger.info(f"Unhandled webhook event type: {event_type}")

        logger.info(f"Successfully processed {event_type}")
        return {"received": True, "processed": event_type}

    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"Webhook processing error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Webhook processing failed")
