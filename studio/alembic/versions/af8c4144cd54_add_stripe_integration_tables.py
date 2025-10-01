"""add stripe integration tables

Revision ID: af8c4144cd54
Revises: 4df5949c42ef
Create Date: 2025-07-22 14:45:36.895878

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = "af8c4144cd54"
down_revision = "4df5949c42ef"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create subscription_plans table
    op.create_table(
        "subscription_plans",
        sa.Column(
            "id",
            mysql.BIGINT(unsigned=True),
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("price", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("billing_cycle", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("features", sa.JSON(), nullable=False),
        sa.Column("currency", mysql.TINYINT(unsigned=True), nullable=False),
        sa.Column("status", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Index("idx_subscription_plans_name", "name"),
    )

    # Create subscription_users table
    op.create_table(
        "subscription_users",
        sa.Column(
            "id",
            mysql.BIGINT(unsigned=True),
            nullable=False,
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column("plan_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("expiration", sa.DateTime(), nullable=False),
        sa.Column(
            "scheduled_downgrade",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "sync_status",
            sa.Enum("pending", "synced", "failed", name="sync_status_enum"),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "last_synced",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
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

    op.create_table(
        "subscription_providers",
        sa.Column(
            "id",
            mysql.BIGINT(unsigned=True),
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        sa.Column(
            "name", sa.String(length=50), nullable=False
        ),  # e.g "stripe", "paypal"
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(),
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )

    # Create subscription_user_accounts table
    op.create_table(
        "subscription_user_accounts",
        sa.Column(
            "id",
            mysql.BIGINT(unsigned=True),
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column(
            "provider_id", mysql.BIGINT(unsigned=True), nullable=False
        ),  # FK to subscription_providers.id
        sa.Column(
            "provider_customer_id", sa.String(length=255), nullable=False
        ),  # Provider's customer ID
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(),
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_subscription_user_accounts_user"
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["subscription_providers.id"],
            name="fk_subscription_user_accounts_provider",
        ),
        sa.UniqueConstraint(
            "provider_customer_id", name="idx_unique_provider_customer_id"
        ),
        sa.Index("idx_subscription_user_accounts_user", "user_id"),
        sa.Index("idx_subscription_user_accounts_provider", "provider_id"),
    )

    # Create subscription_user_purchases table
    op.create_table(
        "subscription_user_purchases",
        sa.Column(
            "id",
            mysql.BIGINT(unsigned=True),
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        sa.Column(
            "plan_id", mysql.BIGINT(unsigned=True), nullable=False
        ),  # 1=FREE, 2=Premium
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(),
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["subscription_plans.id"],
            name="fk_subscription_user_purchases_plan",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_subscription_purchase_history_user"
        ),
        sa.Index("idx_subscription_purchase_history_user_id", "user_id"),
        sa.Index("idx_subscription_purchase_history_plan", "plan_id"),
        sa.Index("idx_subscription_purchase_history_created", "created_at"),
    )

    # Create subscription_cancellations table
    op.create_table(
        "subscription_cancellations",
        sa.Column(
            "id",
            mysql.BIGINT(unsigned=True),
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("cancelled_by_user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("purchases_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column(
            "cancelled_at",
            sa.TIMESTAMP(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "reason",
            sa.Enum(
                "user_request",
                "payment_failed",
                "admin_action",
                "refund",
                name="cancellation_reason_enum",
            ),
            nullable=True,
        ),  # Reason for cancellation
        sa.Column(
            "notes",
            sa.Text(),
            nullable=True,
        ),  # Additional notes or comments
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["cancelled_by_user_id"],
            ["users.id"],
            name="fk_subscription_cancellations_cancelled_by_user",
        ),
        sa.ForeignKeyConstraint(
            ["purchases_id"],
            ["subscription_user_purchases.id"],
            name="fk_subscription_cancellations_purchase",
        ),
        sa.Index("idx_subscription_cancellations_user", "cancelled_by_user_id"),
        sa.Index("idx_subscription_cancellations_purchase", "purchases_id"),
        sa.Index("idx_subscription_cancellations_cancelled_at", "cancelled_at"),
    )

    # Insert initial data
    # Insert subscription plans
    op.execute(
        """
INSERT INTO subscription_plans
(id, name, price, billing_cycle, features, currency, status, created_at)
VALUES
(1, 'Free', 0, 1, JSON_OBJECT(
    'Free', JSON_ARRAY(
        JSON_OBJECT('text', 'Basic compute access with fair-use limitations',
                   'isPremium', false),
        JSON_OBJECT('text', 'Standard support through documentation and community',
                   'isPremium', false),
        JSON_OBJECT('text', 'Basic data storage of 5GB', 'isPremium', false),
        JSON_OBJECT('text', 'Standard processing speed', 'isPremium', false)
    )
), 1, 1, NOW()),
(2, 'Premium', 2000, 1, JSON_OBJECT(
    'Premium', JSON_ARRAY(
        JSON_OBJECT('text', 'Basic compute access with fair-use limitations',
                   'isPremium', false),
        JSON_OBJECT('text', 'Standard support through documentation and community',
                   'isPremium', false),
        JSON_OBJECT('text', 'Priority compute access with guaranteed allocation',
                   'isPremium', true),
        JSON_OBJECT('text', 'Upgraded data storage of 200GB', 'isPremium', true),
        JSON_OBJECT('text', 'Enhanced support including direct assistance',
                   'isPremium', true),
        JSON_OBJECT('text', 'Advanced features like extended job history',
                   'isPremium', true)
    )
), 1, 1, NOW())
"""
    )

    # Create taxes table
    op.create_table(
        "taxes",
        sa.Column(
            "id",
            mysql.BIGINT(unsigned=True),
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("tax_type", sa.String(length=50), nullable=False),
        sa.Column("tax_name", sa.String(length=100), nullable=False),
        sa.Column("tax_rate", sa.DECIMAL(precision=5, scale=4), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(),
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Index("idx_taxes_type", "tax_type"),
        sa.Index("idx_taxes_active", "is_active"),
    )


def downgrade() -> None:
    op.drop_table("taxes")
    op.drop_table("subscription_cancellations")
    op.drop_table("subscription_user_purchases")
    op.drop_table("subscription_user_accounts")
    op.drop_table("subscription_users")
    op.drop_table("subscription_providers")
    op.drop_table("subscription_plans")
