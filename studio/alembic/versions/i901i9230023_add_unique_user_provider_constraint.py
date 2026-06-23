"""add unique constraint on subscription_user_accounts(user_id, provider_id)

Revision ID: i901i9230023
Revises: h901h9270022
Create Date: 2026-06-23 12:00:00.000000

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "i901i9230023"
down_revision = "h901h9270022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_sub_user_account_user_provider",
        "subscription_user_accounts",
        ["user_id", "provider_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_sub_user_account_user_provider",
        "subscription_user_accounts",
        type_="unique",
    )
