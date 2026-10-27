"""add premium_assign_claims

Per-user claim row that serializes concurrent premium assigns. The premium
manager Lambda inserts it when an assign starts and deletes it when the
assign ends; a row whose expires_at has passed is reclaimable.

Revision ID: p901p1020029
Revises: o901o0716028
Create Date: 2026-10-20 12:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = "p901p1020029"
down_revision = "o901o0716028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "premium_assign_claims",
        sa.Column(
            "user_id",
            mysql.BIGINT(unsigned=True),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column("claim_token", sa.VARCHAR(32), nullable=False),
        sa.Column(
            "claimed_at",
            sa.TIMESTAMP(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.TIMESTAMP(), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_premium_assign_claim_user",
            ondelete="CASCADE",
        ),
    )


def downgrade() -> None:
    op.drop_table("premium_assign_claims")
