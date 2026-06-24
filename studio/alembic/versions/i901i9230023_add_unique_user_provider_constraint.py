"""add unique constraint on subscription_user_accounts(user_id, provider_id)

Revision ID: i901i9230023
Revises: m012m0421037
Create Date: 2026-06-23 12:00:00.000000

"""

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "i901i9230023"
down_revision = "m012m0421037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Remove duplicate (user_id, provider_id) rows, keeping the one with the lowest id
    op.execute(
        text(
            "DELETE s1 FROM subscription_user_accounts s1 "
            "INNER JOIN subscription_user_accounts s2 "
            "ON s1.user_id = s2.user_id AND s1.provider_id = s2.provider_id "
            "AND s1.id > s2.id"
        )
    )
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
