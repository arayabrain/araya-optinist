from datetime import datetime, timezone
from typing import List, Optional

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from studio.app.common.core.auth.auth_dependencies import get_current_user
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.subscription.checkout_service import CheckoutService
from studio.app.common.core.subscription.stripe_service import (
    StripeService,
    StripeSubscriptionStatus,
    get_stripe_customer_by_email,
)
from studio.app.common.core.subscription.subscription_service import SubscriptionService
from studio.app.common.core.subscription.webhook_service import WebhookService
from studio.app.common.db.database import get_db
from studio.app.common.schemas.checkouts import CheckoutSessionRequest
from studio.app.common.schemas.subscriptions import (
    CancelSubscriptionResponse,
    CreateCheckoutSessionRequest,
    CreateCheckoutSessionResponse,
    CreateSetupIntentResponse,
    PaymentMethodResponse,
    SubscriptionPlanResponse,
    UpdatePaymentMethodResponse,
    UpdateSubscriptionRequest,
    UpdateSubscriptionResponse,
    UserSubscriptionResponse,
)
from studio.app.common.schemas.users import User

stripe.api_key = SubscriptionService.get_stripe_key()

router = APIRouter(prefix="/api/subsc", tags=["Subscriptions"])
webhook_router = APIRouter(prefix="/api/subsc/webhooks", tags=["Subscription Webhooks"])
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
    # return await StripeService.update_user_subscription(db, current_user, request)
    """
    This endpoint is currently not in use
    """
    raise HTTPException(
        status_code=501,
        detail="This API endpoint is not implemented and currently not in use",
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

        # Get Stripe customer
        customer = await get_stripe_customer_by_email(current_user.email)
        if not customer:
            raise HTTPException(
                status_code=404, detail="No Stripe customer found for user"
            )

        # Get active Stripe subscription
        stripe_subscriptions = stripe.Subscription.list(
            customer=customer.id, status=StripeSubscriptionStatus.ACTIVE.value, limit=1
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
                "cancellation_requested_at": str(
                    int(datetime.now(timezone.utc).timestamp())
                ),
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


@router.get("/payment-methods/default", response_model=Optional[PaymentMethodResponse])
async def get_user_default_payment_method(
    current_user: User = Depends(get_current_user),
):
    return await StripeService.get_default_payment_method(current_user)


@router.get("/payment-methods", response_model=List[PaymentMethodResponse])
async def get_user_payment_methods(
    current_user: User = Depends(get_current_user),
):
    # return await StripeService.handle_get_user_payment_methods(current_user)
    """
    This endpoint is currently not in use
    """
    raise HTTPException(
        status_code=501,
        detail="This API endpoint is not implemented and currently not in use",
    )


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
    return await CheckoutService.handle_checkout_session(db, request, current_user)


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
