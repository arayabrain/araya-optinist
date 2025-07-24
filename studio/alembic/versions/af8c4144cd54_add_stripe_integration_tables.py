"""add stripe integration tables

Revision ID: af8c4144cd54
Revises: 0b3a8e2ca9c1
Create Date: 2025-07-22 14:45:36.895878

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = "af8c4144cd54"
down_revision = "0b3a8e2ca9c1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create subscription_plans table
    op.create_table(
        "subscription_plans",
        sa.Column("id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "price", mysql.BIGINT(unsigned=True), nullable=False
        ),  # Price in cents
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.Index("idx_subscription_plans_name", "name"),
    )

    # Create subscription_users table
    op.create_table(
        "subscription_users",
        sa.Column("id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("plan_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column("expiration", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["plan_id"], ["subscription_plans.id"], name="fk_subscription_users_plan"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_subscription_users_user"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.Index("idx_subscription_users_user_id", "user_id"),
        sa.Index("idx_subscription_users_plan_id", "plan_id"),
        sa.Index("idx_subscription_users_expiration", "expiration"),
        sa.Index("idx_subscription_users_user_plan", "user_id", "plan_id"),
    )

    # Create payment_customers table
    op.create_table(
        "payment_customers",
        sa.Column("id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column(
            "customer_id", sa.String(length=255), nullable=False
        ),  # Stripe Customer ID (cus_...)
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_user_stripe_customer_user"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("customer_id", name="idx_unique_stripe_customer_id"),
        sa.UniqueConstraint(
            "user_id", name="idx_unique_stripe_customer_user"
        ),  # One-to-one relationship
        sa.Index("idx_payment_customers", "customer_id"),
    )

    # Create subscription_user_payments table
    op.create_table(
        "subscription_user_payments",
        sa.Column("id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column(
            "payment_method_id", sa.String(length=255), nullable=False
        ),  # Stripe Payment Method ID (pm_...)
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column(
            "payment_method_used", sa.String(length=255), nullable=False
        ),  # Human-readable type like "Credit Card"
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_subscription_user_payments_user"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("payment_method_id", name="idx_unique_payment_method_id"),
        sa.Index("idx_subscription_user_payments_user_id", "user_id"),
    )

    # Create subscription_purchase_history table
    op.create_table(
        "subscription_purchase_history",
        sa.Column("id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("purchased_product", sa.String(length=255), nullable=False),
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_subscription_purchase_history_user"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.Index("idx_subscription_purchase_history_user_id", "user_id"),
        sa.Index("idx_subscription_purchase_history_product", "purchased_product"),
        sa.Index("idx_subscription_purchase_history_created", "created_at"),
    )


def downgrade() -> None:
    # Drop tables in reverse order (due to foreign key constraints)
    op.drop_table("subscription_purchase_history")
    op.drop_table("subscription_user_payments")
    op.drop_table("payment_customers")
    op.drop_table("subscription_users")
    op.drop_table("subscription_plans")
