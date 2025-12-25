"""allow_null_user_id_for_standby

Revision ID: h901h9270022
Revises: g901g9260021
Create Date: 2025-12-25 11:54:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "h901h9270022"
down_revision = "g901g9260021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Allow NULL user_id for standby premium instances."""

    # Drop the existing unique constraint on user_id
    op.drop_constraint("uq_premium_user_id", "premium_user_assignments", type_="unique")

    # Drop the existing foreign key constraint
    op.drop_constraint(
        "fk_premium_user", "premium_user_assignments", type_="foreignkey"
    )

    # Modify user_id column to allow NULL
    op.alter_column(
        "premium_user_assignments",
        "user_id",
        existing_type=sa.BIGINT(unsigned=True),
        nullable=True,
    )

    # Re-create the foreign key constraint (now allows NULL)
    op.create_foreign_key(
        "fk_premium_user", "premium_user_assignments", "users", ["user_id"], ["id"]
    )

    # Create a new conditional unique constraint (only for non-NULL user_id)
    # This ensures each real user can only have one assignment
    # Multiple standby instances can have NULL user_id
    op.create_index(
        "idx_unique_user_assignment",
        "premium_user_assignments",
        ["user_id"],
        unique=True,
        mysql_length={"user_id": None},
    )


def downgrade() -> None:
    """Revert to NOT NULL user_id."""

    # Drop the conditional unique index
    op.drop_index("idx_unique_user_assignment", "premium_user_assignments")

    # Drop foreign key
    op.drop_constraint(
        "fk_premium_user", "premium_user_assignments", type_="foreignkey"
    )

    # Change user_id back to NOT NULL (this will fail if NULL values exist)
    op.alter_column(
        "premium_user_assignments",
        "user_id",
        existing_type=sa.BIGINT(unsigned=True),
        nullable=False,
    )

    # Re-create original foreign key
    op.create_foreign_key(
        "fk_premium_user", "premium_user_assignments", "users", ["user_id"], ["id"]
    )

    # Re-create original unique constraint
    op.create_unique_constraint(
        "uq_premium_user_id", "premium_user_assignments", ["user_id"]
    )
