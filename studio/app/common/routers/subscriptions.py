from datetime import datetime, timezone
from enum import StrEnum
from typing import List, Optional

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from studio.app.common.core.auth.auth_dependencies import get_current_user
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.subscription.checkout_service import CheckoutService
from studio.app.common.core.subscription.stripe_service import (
    StripeService,
    get_stripe_customer_by_email,
)
from studio.app.common.core.subscription.subscription_service import (
    SubscriptionService,
    SubscriptionUserStatus,
)
from studio.app.common.core.subscription.webhook_service import WebhookService
from studio.app.common.db.database import get_db
from studio.app.common.models.subscription import SubscriptionPlans
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

stripe.api_key = SubscriptionService.get_stripe_key()

router = APIRouter(prefix="/api/subsc", tags=["Subscriptions"])
webhook_router = APIRouter(prefix="/api/subsc/webhooks", tags=["Subscription Webhooks"])
logger = AppLogger.get_logger()


class StripeCheckoutSessionStatus(StrEnum):
    COMPLETE = "complete"
    EXPIRED = "expired"
    OPEN = "open"


class StripeCheckoutPaymentStatus(StrEnum):
    PAID = "paid"
    UNPAID = "unpaid"
    NO_PAYMENT_REQUIRED = "no_payment_required"


@router.get("/mgmts/plans", response_model=List[SubscriptionPlanResponse])
def get_subscription_plans(db: Session = Depends(get_db)):
    try:
        plans: List[SubscriptionPlans] = SubscriptionService.get_active_plans(db)

        if not plans:
            logger.warning("No subscription plans found")
            return []

        result: List[SubscriptionPlanResponse] = []
        for plan in plans:
            try:
                # SQLModel inherits from Pydantic, so .dict() should work
                plan_dict = plan.dict()
                plan_response = SubscriptionPlanResponse(**plan_dict)
                result.append(plan_response)
            except Exception as plan_error:
                logger.error(f"Error processing plan {plan.id}: {plan_error}")
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
        user_purchase = SubscriptionService.get_user_subscription_purchase(
            db, current_user.id
        )
        logger.info(f"Fetched subscription for user {current_user.id}: {subscription}")

        if subscription is None:
            # Check if user has any expired subscriptions
            expired_subscription = SubscriptionService.get_user_expired_subscription(
                db, current_user.id
            )

            if expired_subscription and user_purchase:
                try:
                    sub_data, plan_data, _ = expired_subscription
                    subscription_dict = {
                        **sub_data.dict(),
                        "plan_name": plan_data.name,
                        "plan_price": plan_data.price,
                        "is_expired": True,
                        "status": SubscriptionUserStatus.EXPIRED.value,
                    }
                    subscription_response = UserSubscriptionResponse(
                        **subscription_dict
                    )
                    return subscription_response
                except Exception as sub_error:
                    logger.error(
                        f"Error processing expired subscription for user "
                        f"{current_user.id}: {sub_error}"
                    )
                    return None

            return None

        # If we get here, result is not None, so we can safely unpack
        try:
            subscription, subscription_plans = subscription
            sub_data, plan_data = subscription, subscription_plans

            # Check subscription status based on plan ID and cancellation state
            is_cancelled = SubscriptionService.is_subscription_cancelled(
                db, current_user.id
            )

            subscription_status = SubscriptionService.get_subscription_status(
                plan_data.id, is_cancelled
            )

            # Ensure both datetimes are timezone-aware for comparison
            current_time = SubscriptionService.get_current_datetime()
            expiration_time = sub_data.expiration

            # If expiration is naive, make it timezone-aware
            if expiration_time.tzinfo is None:
                expiration_time = expiration_time.replace(tzinfo=timezone.utc)

            # If current_time is naive, make it timezone-aware
            if current_time.tzinfo is None:
                current_time = current_time.replace(tzinfo=timezone.utc)

            subscription_dict = {
                **sub_data.dict(),
                "plan_name": plan_data.name,
                "plan_price": plan_data.price,
                "is_expired": expiration_time < current_time,
                "status": subscription_status,
            }
            subscription_response = UserSubscriptionResponse(**subscription_dict)
            logger.info(f"Subscription response: {subscription_response}")
            return subscription_response
        except Exception as sub_error:
            logger.error(
                f"Error processing active subscription for user "
                f"{current_user.id}: {sub_error}"
            )
            return None
    except Exception as e:
        logger.error(
            f"Error fetching subscription for user {current_user.id}: {str(e)}"
        )
        raise HTTPException(
            status_code=subscription_status.HTTP_500_INTERNAL_SERVER_ERROR,
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


@router.get("/mgmts/server-time")
async def get_server_time():
    utc_time = datetime.utcnow()
    return {"server_time": utc_time.isoformat()}


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
        result = await StripeService.handle_cancel_user_subscription(db, current_user)
        return CancelSubscriptionResponse(success=result)

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error cancelling subscription: {str(e)}")
        raise HTTPException(
            status_code=400, detail=f"Payment processing error: {str(e)}"
        )
    except HTTPException:
        raise HTTPException(status_code=404, detail="Subscription not found")
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
        current_subscription_result = SubscriptionService.get_user_subscription(
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
        logger.info(f"Validating checkout session ID: {request.session_id}")
        session = stripe.checkout.Session.retrieve(request.session_id)

        # Check if the session is complete and paid
        if (
            session.payment_status == StripeCheckoutPaymentStatus.PAID
            and session.status == StripeCheckoutSessionStatus.COMPLETE
        ):
            logger.info(f"Checkout session {request.session_id} is valid")
            return True
        else:
            logger.warning(f"Checkout session {request.session_id} is invalid")
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
        if session.status == StripeCheckoutSessionStatus.EXPIRED or (
            session.status == StripeCheckoutSessionStatus.OPEN
            and session.payment_status == StripeCheckoutPaymentStatus.UNPAID
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

        WebhookService.dispatch_webhook_event(db, event_type, data)

        logger.info(f"Successfully processed {event_type}")
        return {"received": True, "processed": event_type}

    except HTTPException:
        # Re-raise HTTP exceptions
        raise HTTPException(status_code=400, detail="Webhook processing failed")
    except Exception as e:
        logger.error(f"Webhook processing error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Webhook processing failed")
