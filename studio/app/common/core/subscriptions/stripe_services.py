"""
Stripe Services Module
Contains all business logic and database operations for Stripe integration
"""

import stripe
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import and_, desc

# Import your database models
from studio.app.common import models as common_model
from studio.app.common.core.logger import AppLogger

# Configure logging
logger = AppLogger.get_logger()

# Constants
STRIPE_PROVIDER_NAME = "stripe"

# Initialize Stripe
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")


# Custom Exceptions
class PlanValidationError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(self.message)


class SubscriptionError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(self.message)


# ============ DATABASE OPERATIONS ============


def get_plan_from_db(db: Session, plan_id: int):
    """Get plan details from subscription_plans table"""
    plan = (
        db.query(common_model.SubscriptionPlans)
        .filter(
            common_model.SubscriptionPlans.id == plan_id,
            common_model.SubscriptionPlans.status is True,  # Active plans only
        )
        .first()
    )

    if not plan:
        raise PlanValidationError(f"Plan with ID {plan_id} not found or inactive")

    return plan


def get_stripe_provider(db: Session):
    """Get or create Stripe provider record"""
    provider = (
        db.query(common_model.SubscriptionProviders)
        .filter(common_model.SubscriptionProviders.name == STRIPE_PROVIDER_NAME)
        .first()
    )

    if not provider:
        # Create Stripe provider if it doesn't exist
        provider = common_model.SubscriptionProviders(
            name=STRIPE_PROVIDER_NAME,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        db.add(provider)
        db.commit()
        db.refresh(provider)

    return provider


def get_or_create_user_account(db: Session, user_id: int, stripe_customer_id: str):
    """Get or create user account record for Stripe"""
    provider = get_stripe_provider(db)

    # Check if user account already exists
    user_account = (
        db.query(common_model.SubscriptionUserAccounts)
        .filter(
            and_(
                common_model.SubscriptionUserAccounts.user_id == user_id,
                common_model.SubscriptionUserAccounts.provider_id == provider.id,
            )
        )
        .first()
    )

    if not user_account:
        # Create new user account
        user_account = common_model.SubscriptionUserAccounts(
            user_id=user_id,
            provider_id=provider.id,
            provider_customer_id=stripe_customer_id,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        db.add(user_account)
        db.commit()
        db.refresh(user_account)

    return user_account


def create_user_subscription(
    db: Session, user_id: int, plan_id: int, expiration: datetime
):
    """Create a user subscription record"""
    user_subscription = common_model.SubscriptionUsers(
        plan_id=plan_id,
        user_id=user_id,
        expiration=expiration,
        sync_status="pending",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db.add(user_subscription)
    db.commit()
    db.refresh(user_subscription)
    return user_subscription


def create_purchase_record(db: Session, user_id: int, plan_id: int):
    """Create a purchase history record"""
    purchase = common_model.SubscriptionUserPurchases(
        plan_id=plan_id,
        user_id=user_id,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db.add(purchase)
    db.commit()
    db.refresh(purchase)
    return purchase


def create_cancellation_record(
    db: Session,
    cancelled_by_user_id: int,
    purchases_id: int,
    reason: str,
    notes: Optional[str] = None,
):
    """Create a cancellation record"""
    cancellation = common_model.SubscriptionCancellations(
        cancelled_by_user_id=cancelled_by_user_id,
        purchases_id=purchases_id,
        cancelled_at=datetime.now(),
        reason=reason,
        notes=notes,
    )
    db.add(cancellation)
    db.commit()
    db.refresh(cancellation)
    return cancellation


def get_user_stripe_customer_id(db: Session, user_id: int) -> Optional[str]:
    """Get user's Stripe customer ID from database"""
    provider = get_stripe_provider(db)
    user_account = (
        db.query(common_model.SubscriptionUserAccounts)
        .filter(
            and_(
                common_model.SubscriptionUserAccounts.user_id == user_id,
                common_model.SubscriptionUserAccounts.provider_id == provider.id,
            )
        )
        .first()
    )

    return user_account.provider_customer_id if user_account else None


def find_recent_purchase(db: Session, user_id: int, plan_id: int):
    """Find the most recent purchase for cancellation tracking"""
    return (
        db.query(common_model.SubscriptionUserPurchases)
        .filter(
            and_(
                common_model.SubscriptionUserPurchases.user_id == user_id,
                common_model.SubscriptionUserPurchases.plan_id == plan_id,
            )
        )
        .order_by(desc(common_model.SubscriptionUserPurchases.created_at))
        .first()
    )


def update_subscription_sync_status(
    db: Session, user_id: int, plan_id: int, status: str
):
    """Update subscription sync status"""
    subscription_user = (
        db.query(common_model.SubscriptionUsers)
        .filter(
            and_(
                common_model.SubscriptionUsers.user_id == user_id,
                common_model.SubscriptionUsers.plan_id == plan_id,
                common_model.SubscriptionUsers.sync_status == "pending",
            )
        )
        .order_by(desc(common_model.SubscriptionUsers.created_at))
        .first()
    )

    if subscription_user:
        subscription_user.sync_status = status
        subscription_user.last_synced = datetime.now()
        subscription_user.updated_at = datetime.now()
        db.commit()
        return subscription_user
    return None


# ============ BUSINESS LOGIC ============


def calculate_amount_from_plan(plan) -> int:
    """Convert plan price to cents for Stripe"""
    return int(plan.price)  # Assuming price is already in cents


def calculate_expiration_date(plan, billing_cycles: int = 1) -> datetime:
    """Calculate subscription expiration date based on billing cycle"""
    now = datetime.now()
    if plan.billing_cycle == 1:  # Monthly
        return now + timedelta(days=30 * billing_cycles)
    elif plan.billing_cycle == 2:  # Annually
        return now + timedelta(days=365 * billing_cycles)
    else:
        # Default to monthly if unknown billing cycle
        return now + timedelta(days=30 * billing_cycles)


def get_stripe_price_id(plan) -> Optional[str]:
    """Get Stripe price ID from environment variables"""
    env_var = f"STRIPE_PRICE_ID_PLAN_{plan.id}"
    return os.getenv(env_var)


def format_plan_display(plan) -> str:
    """Format plan display string"""
    return f"${plan.price/100:.2f}/{'month' if plan.billing_cycle == 1 else 'year'}"


def format_plan_description(plan) -> str:
    """Format plan description"""
    return f"{plan.name} - {'monthly' if plan.billing_cycle == 1 else 'annually'}"


# ============ STRIPE API OPERATIONS ============


def get_or_create_stripe_customer(email: str, user_id: int) -> str:
    """Get or create a Stripe customer"""
    try:
        customers = stripe.Customer.list(email=email, limit=1)
        if customers.data:
            return customers.data[0].id
        else:
            customer = stripe.Customer.create(
                email=email, metadata={"user_id": str(user_id)}
            )
            return customer.id
    except stripe.error.StripeError as e:
        logger.error(f"Error creating/retrieving customer: {e}")
        raise SubscriptionError(f"Failed to create customer: {str(e)}")


def create_stripe_payment_intent(
    amount: int,
    currency: str,
    metadata: Dict[str, str],
    customer_id: Optional[str] = None,
) -> stripe.PaymentIntent:
    """Create a Stripe payment intent"""
    try:
        payment_intent_params = {
            "amount": amount,
            "currency": currency,
            "metadata": metadata,
            "automatic_payment_methods": {"enabled": True},
        }

        if customer_id:
            payment_intent_params["customer"] = customer_id

        return stripe.PaymentIntent.create(**payment_intent_params)

    except stripe.error.StripeError as e:
        logger.error(f"Error creating payment intent: {e}")
        raise SubscriptionError(f"Failed to create payment intent: {str(e)}")


def create_stripe_subscription(
    customer_id: str, price_id: str, payment_method_id: str, metadata: Dict[str, str]
) -> stripe.Subscription:
    """Create a Stripe subscription"""
    try:
        # Attach payment method to customer
        stripe.PaymentMethod.attach(payment_method_id, customer=customer_id)

        # Set as default payment method
        stripe.Customer.modify(
            customer_id, invoice_settings={"default_payment_method": payment_method_id}
        )

        # Create subscription
        subscription_params = {
            "customer": customer_id,
            "items": [{"price": price_id}],
            "metadata": metadata,
            "payment_behavior": "default_incomplete",
            "payment_settings": {"save_default_payment_method": "on_subscription"},
            "expand": ["latest_invoice.payment_intent"],
        }

        return stripe.Subscription.create(**subscription_params)

    except stripe.error.StripeError as e:
        logger.error(f"Error creating subscription: {e}")
        raise SubscriptionError(f"Failed to create subscription: {str(e)}")


def cancel_stripe_subscription(
    subscription_id: str, cancel_at_period_end: bool = True
) -> stripe.Subscription:
    """Cancel a Stripe subscription"""
    try:
        if cancel_at_period_end:
            return stripe.Subscription.modify(
                subscription_id, cancel_at_period_end=True
            )
        else:
            return stripe.Subscription.cancel(subscription_id)

    except stripe.error.StripeError as e:
        logger.error(f"Error canceling subscription: {e}")
        raise SubscriptionError(f"Failed to cancel subscription: {str(e)}")


def get_stripe_payment_methods(customer_id: str) -> list:
    """Get customer's payment methods from Stripe"""
    try:
        payment_methods = stripe.PaymentMethod.list(customer=customer_id, type="card")
        return payment_methods.data

    except stripe.error.StripeError as e:
        logger.error(f"Error retrieving payment methods: {e}")
        raise SubscriptionError(f"Failed to retrieve payment methods: {str(e)}")


# ============ WEBHOOK UTILITIES ============


def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    """Verify Stripe webhook signature"""
    if not STRIPE_WEBHOOK_SECRET:
        logger.warning("STRIPE_WEBHOOK_SECRET not set")
        return False

    try:
        stripe.Webhook.construct_event(payload, signature, STRIPE_WEBHOOK_SECRET)
        return True
    except ValueError:
        logger.error("Invalid webhook payload")
        return False
    except stripe.error.SignatureVerificationError:
        logger.error("Invalid webhook signature")
        return False


# ============ WEBHOOK EVENT HANDLERS ============


async def handle_payment_intent_succeeded(payment_intent: Dict[str, Any], db: Session):
    """Handle successful payment intent"""
    try:
        user_id = int(payment_intent.get("metadata", {}).get("user_id", 0))
        plan_id = int(payment_intent.get("metadata", {}).get("plan_id", 0))

        logger.info(f"Payment succeeded for user {user_id}, plan ID: {plan_id}")

        if user_id and plan_id:
            update_subscription_sync_status(db, user_id, plan_id, "synced")

    except Exception as e:
        logger.error(f"Error handling payment success: {e}")


async def handle_invoice_payment_failed(invoice: Dict[str, Any], db: Session):
    """Handle failed payment"""
    try:
        customer_id = invoice.get("customer")
        subscription_id = invoice.get("subscription")

        logger.warning(
            f"Payment failed for customer {customer_id}, subscription {subscription_id}"
        )

        # Find user by customer ID and update subscription status
        provider = get_stripe_provider(db)
        user_account = (
            db.query(common_model.SubscriptionUserAccounts)
            .filter(
                and_(
                    common_model.SubscriptionUserAccounts.provider_id == provider.id,
                    common_model.SubscriptionUserAccounts.provider_customer_id
                    == customer_id,
                )
            )
            .first()
        )

        if user_account:
            # Update sync status to failed
            subscription_users = (
                db.query(common_model.SubscriptionUsers)
                .filter(common_model.SubscriptionUsers.user_id == user_account.user_id)
                .all()
            )

            for sub_user in subscription_users:
                sub_user.sync_status = "failed"
                sub_user.updated_at = datetime.now()

            db.commit()

    except Exception as e:
        logger.error(f"Error handling failed payment: {e}")


async def handle_subscription_deleted(subscription: Dict[str, Any], db: Session):
    """Handle subscription cancellation"""
    try:
        user_id = int(subscription.get("metadata", {}).get("user_id", 0))
        plan_id = int(subscription.get("metadata", {}).get("plan_id", 0))

        logger.info(f"Subscription deleted for user {user_id}, plan ID: {plan_id}")

    except Exception as e:
        logger.error(f"Error handling subscription deletion: {e}")


async def handle_subscription_updated(subscription: Dict[str, Any], db: Session):
    """Handle subscription updates"""
    try:
        user_id = int(subscription.get("metadata", {}).get("user_id", 0))
        status = subscription.get("status")

        logger.info(f"Subscription updated for user {user_id}, new status: {status}")

        # Update subscription status in database based on Stripe status

    except Exception as e:
        logger.error(f"Error handling subscription update: {e}")


# ============ VALIDATION FUNCTIONS ============


def validate_plan_exists(db: Session, plan_id: int):
    """Validate that plan exists and is active"""
    plan = get_plan_from_db(db, plan_id)
    logger.info(f"Validated plan: {plan.name} (ID: {plan.id}) - ${plan.price/100:.2f}")
    return plan


def validate_subscription_ownership(
    subscription_metadata: Dict[str, str], user_id: int
) -> bool:
    """Validate that subscription belongs to the user"""
    return subscription_metadata.get("user_id") == str(user_id)


def validate_stripe_price_configured(plan) -> str:
    """Validate that Stripe price ID is configured for plan"""
    stripe_price_id = get_stripe_price_id(plan)
    if not stripe_price_id:
        raise SubscriptionError(f"No Stripe price ID configured for plan {plan.name}")
    return stripe_price_id
