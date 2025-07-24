# subscription_routes.py
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

# Import your database models and dependencies
from studio.app.common import models as common_model
from studio.app.common.core.auth.auth_dependencies import get_current_user
from studio.app.common.db.database import get_db
from studio.app.common.schemas.subscriptions import SubscriptionPlanResponse
from studio.app.common.schemas.users import User

router = APIRouter(tags=["subscriptions"])


@router.get(
    "/subscriptions/plans",
    response_model=List[SubscriptionPlanResponse],
    description="""Get all available subscription plans""",
)
async def get_subscription_plans(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get all available subscription plans
    """
    try:
        plans = (
            db.query(common_model.SubscriptionPlans)
            .order_by(common_model.SubscriptionPlans.price)
            .all()
        )
        return plans
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch subscription plans: {str(e)}",
        )


# @router.get(
#     "/users/{user_id}/subscription", response_model=Optional[UserSubscriptionResponse]
# )
# async def get_user_subscription(
#     user_id: int,
#     db: Session = Depends(get_db),
#     current_user: Users = Depends(get_current_user),
# ):
#     """
#     Get user's current active subscription
#     """
#     # Check if user can access this data (either own data or admin)
#     if (
#         current_user.id != user_id and not current_user.is_admin
#     ):  # Assuming you have admin check
#         raise HTTPException(
#             status_code=status.HTTP_403_FORBIDDEN,
#             detail="Not authorized to access this user's subscription data",
#         )

#     try:
#         # Get the most recent active subscription
#         subscription = (
#             db.query(SubscriptionUsers, SubscriptionPlans)
#             .join(SubscriptionPlans, SubscriptionUsers.plan_id == SubscriptionPlans.id)
#             .filter(
#                 and_(
#                     SubscriptionUsers.user_id == user_id,
#                     SubscriptionUsers.expiration > datetime.now(),
#                 )
#             )
#             .order_by(SubscriptionUsers.expiration.desc())
#             .first()
#         )

#         if not subscription:
#             # Check if user has any expired subscriptions
#             expired_subscription = (
#                 db.query(SubscriptionUsers, SubscriptionPlans)
#                 .join(
#                     SubscriptionPlans, SubscriptionUsers.plan_id == SubscriptionPlans.id
#                 )
#                 .filter(SubscriptionUsers.user_id == user_id)
#                 .order_by(SubscriptionUsers.expiration.desc())
#                 .first()
#             )

#             if expired_subscription:
#                 sub_data, plan_data = expired_subscription
#                 return UserSubscriptionResponse(
#                     id=sub_data.id,
#                     plan_id=sub_data.plan_id,
#                     user_id=sub_data.user_id,
#                     expiration=sub_data.expiration,
#                     plan_name=plan_data.name,
#                     plan_price=plan_data.price,
#                     created_at=sub_data.created_at,
#                     updated_at=sub_data.updated_at,
#                 )

#             return None

#         sub_data, plan_data = subscription
#         return UserSubscriptionResponse(
#             id=sub_data.id,
#             plan_id=sub_data.plan_id,
#             user_id=sub_data.user_id,
#             expiration=sub_data.expiration,
#             plan_name=plan_data.name,
#             plan_price=plan_data.price,
#             created_at=sub_data.created_at,
#             updated_at=sub_data.updated_at,
#         )

#     except Exception as e:
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail=f"Failed to fetch user subscription: {str(e)}",
#         )


# @router.get(
#     "/users/{user_id}/subscription-summary", response_model=UserSubscriptionSummary
# )
# async def get_user_subscription_summary(
#     user_id: int,
#     db: Session = Depends(get_db),
#     current_user: Users = Depends(get_current_user),
# ):
#     """
#     Get comprehensive subscription summary for a user
#     """
#     # Check permissions
#     if current_user.id != user_id and not current_user.is_admin:
#         raise HTTPException(
#             status_code=status.HTTP_403_FORBIDDEN,
#             detail="Not authorized to access this user's data",
#         )

#     try:
#         # Get user details
#         user = db.query(Users).filter(Users.id == user_id).first()
#         if not user:
#             raise HTTPException(
#                 status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
#             )

#         # Get current active subscription
#         current_subscription = (
#             db.query(SubscriptionUsers, SubscriptionPlans)
#             .join(SubscriptionPlans, SubscriptionUsers.plan_id == SubscriptionPlans.id)
#             .filter(
#                 and_(
#                     SubscriptionUsers.user_id == user_id,
#                     SubscriptionUsers.expiration > datetime.now(),
#                 )
#             )
#             .order_by(SubscriptionUsers.expiration.desc())
#             .first()
#         )

#         # Check for Stripe customer
#         stripe_customer = (
#             db.query(PaymentCustomers)
#             .filter(PaymentCustomers.user_id == user_id)
#             .first()
#         )

#         # Build response
#         summary = UserSubscriptionSummary(
#             user_id=user.id,
#             user_name=user.name,
#             user_email=user.email,
#             has_stripe_customer=stripe_customer is not None,
#             stripe_customer_id=stripe_customer.customer_id if stripe_customer else None,
#         )

#         if current_subscription:
#             sub_data, plan_data = current_subscription
#             summary.current_plan = plan_data.name
#             summary.plan_price = plan_data.price
#             summary.expiration = sub_data.expiration
#             summary.is_active = True
#         else:
#             # Default to Free plan if no active subscription
#             free_plan = (
#                 db.query(SubscriptionPlans)
#                 .filter(SubscriptionPlans.name == "Free")
#                 .first()
#             )
#             if free_plan:
#                 summary.current_plan = free_plan.name
#                 summary.plan_price = free_plan.price
#                 summary.is_active = True

#         return summary

#     except HTTPException:
#         raise
#     except Exception as e:
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail=f"Failed to fetch user subscription summary: {str(e)}",
#         )


# @router.get("/users/{user_id}/subscription-history")
# async def get_user_subscription_history(
#     user_id: int,
#     db: Session = Depends(get_db),
#     current_user: Users = Depends(get_current_user),
# ):
#     """
#     Get user's subscription history
#     """
#     if current_user.id != user_id and not current_user.is_admin:
#         raise HTTPException(
#             status_code=status.HTTP_403_FORBIDDEN,
#             detail="Not authorized to access this user's data",
#         )

#     try:
#         history = (
#             db.query(SubscriptionUsers, SubscriptionPlans)
#             .join(SubscriptionPlans, SubscriptionUsers.plan_id == SubscriptionPlans.id)
#             .filter(SubscriptionUsers.user_id == user_id)
#             .order_by(SubscriptionUsers.created_at.desc())
#             .all()
#         )

#         return [
#             {
#                 "id": sub.id,
#                 "plan_name": plan.name,
#                 "plan_price": plan.price,
#                 "created_at": sub.created_at,
#                 "updated_at": sub.updated_at,
#                 "expiration": sub.expiration,
#                 "is_active": sub.expiration > datetime.now(),
#                 "is_expired": sub.expiration <= datetime.now(),
#             }
#             for sub, plan in history
#         ]

#     except Exception as e:
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail=f"Failed to fetch subscription history: {str(e)}",
#         )


# # Admin endpoints
# @router.get("/admin/subscriptions", response_model=List[UserSubscriptionSummary])
# async def get_all_user_subscriptions(
#     db: Session = Depends(get_db), current_user: Users = Depends(get_current_user)
# ):
#     """
#     Admin endpoint to get all user subscriptions
#     """
#     if not current_user.is_admin:  # Assuming you have admin check
#         raise HTTPException(
#             status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required"
#         )

#     try:
#         # Get all users with their current subscriptions
#         users_with_subs = db.query(Users).all()

#         summaries = []
#         for user in users_with_subs:
#             # Get current active subscription
#             current_subscription = (
#                 db.query(SubscriptionUsers, SubscriptionPlans)
#                 .join(
#                     SubscriptionPlans, SubscriptionUsers.plan_id == SubscriptionPlans.id
#                 )
#                 .filter(
#                     and_(
#                         SubscriptionUsers.user_id == user.id,
#                         SubscriptionUsers.expiration > datetime.now(),
#                     )
#                 )
#                 .order_by(SubscriptionUsers.expiration.desc())
#                 .first()
#             )

#             # Check for Stripe customer
#             stripe_customer = (
#                 db.query(PaymentCustomers)
#                 .filter(PaymentCustomers.user_id == user.id)
#                 .first()
#             )

#             summary = UserSubscriptionSummary(
#                 user_id=user.id,
#                 user_name=user.name,
#                 user_email=user.email,
#                 has_stripe_customer=stripe_customer is not None,
#                 stripe_customer_id=(
#                     stripe_customer.customer_id if stripe_customer else None
#                 ),
#             )

#             if current_subscription:
#                 sub_data, plan_data = current_subscription
#                 summary.current_plan = plan_data.name
#                 summary.plan_price = plan_data.price
#                 summary.expiration = sub_data.expiration
#                 summary.is_active = True
#             else:
#                 summary.current_plan = "Free"
#                 summary.plan_price = 0
#                 summary.is_active = False

#             summaries.append(summary)

#         return summaries

#     except Exception as e:
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail=f"Failed to fetch all subscriptions: {str(e)}",
#         )
