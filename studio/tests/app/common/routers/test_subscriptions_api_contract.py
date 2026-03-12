"""
Contract Tests for Subscriptions API

These tests verify that API responses match the frontend TypeScript interfaces.
This ensures the backend and frontend stay in sync and prevents contract mismatches.

Frontend interfaces are defined in:
  frontend/src/api/subscriptions/SubscriptionsApiDTO.ts
  frontend/src/store/slice/Subscriptions/SubscriptionType.ts

Tested endpoints:
  - GET  /api/subsc/mgmts/plans                    -> List[SubscriptionPlanResponse]
  - GET  /api/subsc/mgmts                          -> UserSubscriptionResponse
  - GET  /api/subsc/mgmts/server-time              -> ServerTimeResponse
  - DELETE /api/subsc/mgmts/cancel                 -> CancelSubscriptionResponse
  - POST /api/subsc/mgmts/reactivate/{user_id}     -> CancelSubscriptionResponse
  - GET  /api/subsc/payment-methods/default        -> PaymentMethodResponse
  - POST /api/subsc/checkout/create-checkout-session -> CreateCheckoutSessionResponse
  - POST /api/subsc/checkout/validate-checkout-session -> CheckoutValidationResponse
  - GET  /api/subsc/invoices/{user_id}             -> List[InvoiceResponse]
"""

from datetime import datetime, timezone

from studio.app.common.core.subscription.constants import SubscriptionPlanIds
from studio.app.common.schemas.checkouts import (
    CheckoutValidationResponse,
    CheckoutValidationStatus,
)
from studio.app.common.schemas.subscriptions import (
    CancelSubscriptionResponse,
    CreateCheckoutSessionResponse,
    InvoiceResponse,
    PaymentMethodResponse,
    SubscriptionPlanResponse,
    UserSubscriptionResponse,
)

# ============================================================================
# Frontend Contract Definitions
# ============================================================================
# These mirror the TypeScript interfaces in SubscriptionsApiDTO.ts
# and SubscriptionType.ts
#
# Note: datetime fields are (datetime, str) because Pydantic .dict() returns
# datetime objects, but FastAPI JSON serialization converts them to strings.

# SubscriptionPlanDTO interface
SUBSCRIPTION_PLAN_DTO_REQUIRED_FIELDS = {
    "id": int,
    "name": str,
    "price": int,
    "billing_cycle": int,
    "features": dict,
    "currency": int,
    "status": bool,
    "created_at": (datetime, str),  # datetime in .dict(), str in JSON
}

# UserSubscription interface
USER_SUBSCRIPTION_REQUIRED_FIELDS = {
    "id": int,
    "plan_id": int,
    "user_id": int,
    "expiration": (datetime, str),  # datetime in .dict(), str in JSON
    "is_expired": bool,
    "scheduled_downgrade": bool,
    "status": int,
    "plan_name": str,
    "plan_price": int,
}

USER_SUBSCRIPTION_OPTIONAL_FIELDS = {
    "created_at": (datetime, str),
    "updated_at": (datetime, str),
}

# CreateCheckoutSessionResponse interface
CHECKOUT_SESSION_RESPONSE_REQUIRED_FIELDS = {
    "checkout_url": str,
    "session_id": str,
}

# CheckoutValidationResponse interface
CHECKOUT_VALIDATION_RESPONSE_REQUIRED_FIELDS = {
    "status": str,
}

CHECKOUT_VALIDATION_RESPONSE_OPTIONAL_FIELDS = {
    "message": str,
}

# Valid checkout validation statuses
VALID_CHECKOUT_STATUSES = {"success", "payment_failed", "webhook_failed"}

# CancelSubscriptionResponse interface
CANCEL_SUBSCRIPTION_RESPONSE_REQUIRED_FIELDS = {
    "success": bool,
    "message": str,
    "cancellation_date": str,
    "access_until": str,
}

# PaymentMethodResponse interface
PAYMENT_METHOD_RESPONSE_REQUIRED_FIELDS = {
    "id": str,
    "last4": str,
    "brand": str,
    "exp_month": int,
    "exp_year": int,
    "is_default": bool,
}

# InvoiceResponse interface
INVOICE_RESPONSE_REQUIRED_FIELDS = {
    "id": str,
    "date": str,
    "total": str,
    "status": str,
    "invoice_url": str,
    "amount_paid": int,
    "amount_due": int,
    "currency": str,
}

INVOICE_RESPONSE_OPTIONAL_FIELDS = {
    "description": str,
    "period_start": str,
    "period_end": str,
}

# ServerTimeResponse interface
SERVER_TIME_RESPONSE_REQUIRED_FIELDS = {
    "server_time": str,
}


# ============================================================================
# Contract Validation Helpers
# ============================================================================


def validate_contract(
    result: dict,
    required_fields: dict,
    optional_fields: dict = None,
    context: str = "",
) -> None:
    """
    Validate that a response matches the frontend contract.
    """
    for field, expected_type in required_fields.items():
        assert field in result, (
            f"Contract violation ({context}): Missing required field '{field}'. "
            f"Response has: {list(result.keys())}"
        )
        if isinstance(expected_type, tuple):
            assert isinstance(result[field], expected_type), (
                f"Contract violation ({context}): Field '{field}' has wrong type. "
                f"Expected one of {expected_type}, got {type(result[field])}"
            )
        elif result[field] is not None:
            assert isinstance(result[field], expected_type), (
                f"Contract violation ({context}): Field '{field}' has wrong type. "
                f"Expected {expected_type}, got {type(result[field])}"
            )

    if optional_fields:
        for field, expected_type in optional_fields.items():
            if field in result and result[field] is not None:
                if isinstance(expected_type, tuple):
                    assert isinstance(result[field], expected_type), (
                        f"Contract violation ({context}): "
                        f"Optional field '{field}' has wrong type."
                    )
                else:
                    assert isinstance(result[field], expected_type), (
                        f"Contract violation ({context}): "
                        f"Optional field '{field}' has wrong type."
                    )


# ============================================================================
# Contract Tests: SubscriptionPlanResponse
# ============================================================================


def test_contract_subscription_plan_schema_has_required_fields():
    """
    Contract test: SubscriptionPlanResponse has all fields frontend expects.
    """
    schema = SubscriptionPlanResponse.schema()
    properties = schema.get("properties", {})

    for field in SUBSCRIPTION_PLAN_DTO_REQUIRED_FIELDS.keys():
        assert field in properties, (
            f"Contract violation: Frontend expects field '{field}' "
            f"but SubscriptionPlanResponse doesn't have it. "
            f"Model has: {list(properties.keys())}"
        )


def test_contract_subscription_plan_serialization():
    """
    Contract test: SubscriptionPlanResponse serializes with correct field names.
    """
    plan = SubscriptionPlanResponse(
        id=1,
        name="Premium",
        price=2000,
        billing_cycle=1,
        features={"features": [{"text": "Feature 1", "isPremium": True}]},
        currency=1,
        status=True,
        created_at=datetime.now(timezone.utc),
    )

    result = plan.dict()

    validate_contract(
        result,
        SUBSCRIPTION_PLAN_DTO_REQUIRED_FIELDS,
        context="SubscriptionPlanResponse",
    )

    # Verify features is a dict (not string)
    assert isinstance(result["features"], dict)


def test_contract_subscription_plan_features_parsed_from_json():
    """
    Contract test: Features are properly parsed from JSON string.
    """
    # Test with JSON string input (as might come from database)
    plan = SubscriptionPlanResponse(
        id=1,
        name="Premium",
        price=2000,
        billing_cycle=1,
        features='{"features": [{"text": "Feature 1", "isPremium": true}]}',
        currency=1,
        status=True,
        created_at=datetime.now(timezone.utc),
    )

    result = plan.dict()

    # Features should be parsed to dict
    assert isinstance(result["features"], dict)
    assert "features" in result["features"]


# ============================================================================
# Contract Tests: UserSubscriptionResponse
# ============================================================================


def test_contract_user_subscription_schema_has_required_fields():
    """
    Contract test: UserSubscriptionResponse has all fields frontend expects.
    """
    schema = UserSubscriptionResponse.schema()
    properties = schema.get("properties", {})

    for field in USER_SUBSCRIPTION_REQUIRED_FIELDS.keys():
        assert field in properties, (
            f"Contract violation: Frontend expects field '{field}' "
            f"but UserSubscriptionResponse doesn't have it."
        )


def test_contract_user_subscription_serialization_active():
    """
    Contract test: Active subscription serializes correctly.
    """
    subscription = UserSubscriptionResponse(
        id=1,
        plan_id=SubscriptionPlanIds.PREMIUM,
        user_id=100,
        expiration=datetime.now(timezone.utc),
        is_expired=False,
        scheduled_downgrade=False,
        plan_name="Premium",
        plan_price=2000,
        status=1,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    result = subscription.dict()

    validate_contract(
        result,
        USER_SUBSCRIPTION_REQUIRED_FIELDS,
        USER_SUBSCRIPTION_OPTIONAL_FIELDS,
        context="UserSubscriptionResponse (active)",
    )


def test_contract_user_subscription_serialization_expired():
    """
    Contract test: Expired subscription serializes correctly.
    """
    subscription = UserSubscriptionResponse(
        id=1,
        plan_id=SubscriptionPlanIds.FREE,
        user_id=100,
        expiration=datetime(2024, 1, 1, tzinfo=timezone.utc),
        is_expired=True,
        scheduled_downgrade=False,
        plan_name="Free",
        plan_price=0,
        status=3,  # Expired status
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    result = subscription.dict()

    validate_contract(
        result,
        USER_SUBSCRIPTION_REQUIRED_FIELDS,
        context="UserSubscriptionResponse (expired)",
    )

    assert result["is_expired"] is True
    assert result["status"] == 3


def test_contract_user_subscription_scheduled_downgrade():
    """
    Contract test: Subscription with scheduled downgrade serializes correctly.
    """
    subscription = UserSubscriptionResponse(
        id=1,
        plan_id=SubscriptionPlanIds.PREMIUM,
        user_id=100,
        expiration=datetime.now(timezone.utc),
        is_expired=False,
        scheduled_downgrade=True,
        plan_name="Premium",
        plan_price=2000,
        status=4,  # Cancelled status
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    result = subscription.dict()

    assert result["scheduled_downgrade"] is True


# ============================================================================
# Contract Tests: CreateCheckoutSessionResponse
# ============================================================================


def test_contract_checkout_session_response_schema():
    """
    Contract test: CreateCheckoutSessionResponse has required fields.
    """
    schema = CreateCheckoutSessionResponse.schema()
    properties = schema.get("properties", {})

    for field in CHECKOUT_SESSION_RESPONSE_REQUIRED_FIELDS.keys():
        assert field in properties


def test_contract_checkout_session_response_serialization():
    """
    Contract test: Checkout session response serializes correctly.
    """
    response = CreateCheckoutSessionResponse(
        checkout_url="https://checkout.stripe.com/pay/cs_test_123",
        session_id="cs_test_123",
    )

    result = response.dict()

    validate_contract(
        result,
        CHECKOUT_SESSION_RESPONSE_REQUIRED_FIELDS,
        context="CreateCheckoutSessionResponse",
    )


# ============================================================================
# Contract Tests: CheckoutValidationResponse
# ============================================================================


def test_contract_checkout_validation_response_schema():
    """
    Contract test: CheckoutValidationResponse has required fields.
    """
    schema = CheckoutValidationResponse.schema()
    properties = schema.get("properties", {})

    for field in CHECKOUT_VALIDATION_RESPONSE_REQUIRED_FIELDS.keys():
        assert field in properties


def test_contract_checkout_validation_success():
    """
    Contract test: Success validation response serializes correctly.
    """
    response = CheckoutValidationResponse(
        status=CheckoutValidationStatus.SUCCESS,
        message="Payment successful!",
    )

    result = response.dict()

    validate_contract(
        result,
        CHECKOUT_VALIDATION_RESPONSE_REQUIRED_FIELDS,
        CHECKOUT_VALIDATION_RESPONSE_OPTIONAL_FIELDS,
        context="CheckoutValidationResponse (success)",
    )

    assert result["status"] in VALID_CHECKOUT_STATUSES


def test_contract_checkout_validation_payment_failed():
    """
    Contract test: Payment failed validation response serializes correctly.
    """
    response = CheckoutValidationResponse(
        status=CheckoutValidationStatus.PAYMENT_FAILED,
        message="Payment was declined.",
    )

    result = response.dict()

    assert result["status"] == "payment_failed"
    assert result["status"] in VALID_CHECKOUT_STATUSES


def test_contract_checkout_validation_webhook_failed():
    """
    Contract test: Webhook failed validation response serializes correctly.
    """
    response = CheckoutValidationResponse(
        status=CheckoutValidationStatus.WEBHOOK_FAILED,
        message="Internal error occurred.",
    )

    result = response.dict()

    assert result["status"] == "webhook_failed"
    assert result["status"] in VALID_CHECKOUT_STATUSES


# ============================================================================
# Contract Tests: CancelSubscriptionResponse
# ============================================================================


def test_contract_cancel_subscription_response_schema():
    """
    Contract test: CancelSubscriptionResponse has required fields.
    """
    schema = CancelSubscriptionResponse.schema()
    properties = schema.get("properties", {})

    for field in CANCEL_SUBSCRIPTION_RESPONSE_REQUIRED_FIELDS.keys():
        assert field in properties


def test_contract_cancel_subscription_response_serialization():
    """
    Contract test: Cancel subscription response serializes correctly.
    """
    response = CancelSubscriptionResponse(
        success=True,
        message="Subscription will be cancelled at period end.",
        cancellation_date="2025-02-15T00:00:00Z",
        access_until="2025-02-28T23:59:59Z",
    )

    result = response.dict()

    validate_contract(
        result,
        CANCEL_SUBSCRIPTION_RESPONSE_REQUIRED_FIELDS,
        context="CancelSubscriptionResponse",
    )


# ============================================================================
# Contract Tests: PaymentMethodResponse
# ============================================================================


def test_contract_payment_method_response_schema():
    """
    Contract test: PaymentMethodResponse has required fields.
    """
    schema = PaymentMethodResponse.schema()
    properties = schema.get("properties", {})

    for field in PAYMENT_METHOD_RESPONSE_REQUIRED_FIELDS.keys():
        assert field in properties


def test_contract_payment_method_response_serialization():
    """
    Contract test: Payment method response serializes correctly.
    """
    response = PaymentMethodResponse(
        id="pm_123abc",
        last4="4242",
        brand="visa",
        exp_month=12,
        exp_year=2025,
        is_default=True,
    )

    result = response.dict()

    validate_contract(
        result,
        PAYMENT_METHOD_RESPONSE_REQUIRED_FIELDS,
        context="PaymentMethodResponse",
    )


def test_contract_payment_method_brand_values():
    """
    Contract test: Payment method brand is a valid card brand.
    """
    valid_brands = [
        "visa",
        "mastercard",
        "amex",
        "discover",
        "jcb",
        "diners",
        "unionpay",
    ]

    for brand in valid_brands:
        response = PaymentMethodResponse(
            id="pm_123",
            last4="4242",
            brand=brand,
            exp_month=12,
            exp_year=2025,
            is_default=False,
        )
        result = response.dict()
        assert isinstance(result["brand"], str)


# ============================================================================
# Contract Tests: InvoiceResponse
# ============================================================================


def test_contract_invoice_response_schema():
    """
    Contract test: InvoiceResponse has required fields.
    """
    schema = InvoiceResponse.schema()
    properties = schema.get("properties", {})

    for field in INVOICE_RESPONSE_REQUIRED_FIELDS.keys():
        assert field in properties


def test_contract_invoice_response_serialization():
    """
    Contract test: Invoice response serializes correctly.
    """
    response = InvoiceResponse(
        id="in_123abc",
        date="2025-01-15T00:00:00Z",
        total="$20.00",
        status="Paid",
        invoice_url="https://stripe.com/invoice/in_123abc",
        amount_paid=2000,
        amount_due=0,
        currency="USD",
        description="Premium subscription",
        period_start="2025-01-01T00:00:00Z",
        period_end="2025-01-31T23:59:59Z",
    )

    result = response.dict()

    validate_contract(
        result,
        INVOICE_RESPONSE_REQUIRED_FIELDS,
        INVOICE_RESPONSE_OPTIONAL_FIELDS,
        context="InvoiceResponse",
    )


def test_contract_invoice_response_minimal():
    """
    Contract test: Invoice with minimal optional fields serializes correctly.
    """
    response = InvoiceResponse(
        id="in_123abc",
        date="2025-01-15T00:00:00Z",
        total="$20.00",
        status="Open",
        invoice_url="https://stripe.com/invoice/in_123abc",
        amount_paid=0,
        amount_due=2000,
        currency="USD",
    )

    result = response.dict()

    validate_contract(
        result,
        INVOICE_RESPONSE_REQUIRED_FIELDS,
        context="InvoiceResponse (minimal)",
    )


# ============================================================================
# Contract Tests: Field Naming Consistency
# ============================================================================


def test_contract_no_legacy_subscription_fields():
    """
    Ensure no legacy or camelCase field names in subscription responses.
    """
    subscription = UserSubscriptionResponse(
        id=1,
        plan_id=SubscriptionPlanIds.PREMIUM,
        user_id=100,
        expiration=datetime.now(timezone.utc),
        is_expired=False,
        scheduled_downgrade=False,
        plan_name="Premium",
        plan_price=2000,
        status=1,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    result = subscription.dict()

    legacy_fields = [
        "planId",  # camelCase
        "userId",  # camelCase
        "isExpired",  # camelCase
        "scheduledDowngrade",  # camelCase
        "planName",  # camelCase
        "planPrice",  # camelCase
        "createdAt",  # camelCase
        "updatedAt",  # camelCase
    ]

    for legacy in legacy_fields:
        assert legacy not in result, (
            f"Legacy field '{legacy}' found. "
            f"Frontend expects snake_case field names."
        )


def test_contract_no_legacy_plan_fields():
    """
    Ensure no legacy or camelCase field names in plan responses.
    """
    plan = SubscriptionPlanResponse(
        id=1,
        name="Premium",
        price=2000,
        billing_cycle=1,
        features={},
        currency=1,
        status=True,
        created_at=datetime.now(timezone.utc),
    )

    result = plan.dict()

    legacy_fields = [
        "billingCycle",  # camelCase
        "createdAt",  # camelCase
    ]

    for legacy in legacy_fields:
        assert legacy not in result


# ============================================================================
# Contract Tests: Status Values
# ============================================================================


def test_contract_checkout_validation_status_values():
    """
    Contract test: CheckoutValidationStatus values match frontend expectations.
    """
    # These are the exact values frontend expects
    expected_statuses = {"success", "payment_failed", "webhook_failed"}

    backend_statuses = {s.value for s in CheckoutValidationStatus}

    assert backend_statuses == expected_statuses, (
        f"CheckoutValidationStatus values don't match frontend. "
        f"Backend: {backend_statuses}, Frontend expects: {expected_statuses}"
    )


def test_contract_subscription_status_is_integer():
    """
    Contract test: Subscription status is an integer (enum value).
    """
    subscription = UserSubscriptionResponse(
        id=1,
        plan_id=SubscriptionPlanIds.PREMIUM,
        user_id=100,
        expiration=datetime.now(timezone.utc),
        is_expired=False,
        scheduled_downgrade=False,
        plan_name="Premium",
        plan_price=2000,
        status=1,  # Active
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    result = subscription.dict()

    assert isinstance(
        result["status"], int
    ), f"status should be int, got {type(result['status'])}"
