"""Add registration columns to users table

Revision ID: d438483eb821
Revises: af8c4144cd54
Create Date: 2025-10-31 03:31:20.832700

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "d438483eb821"
down_revision = "af8c4144cd54"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add required columns to existing users table
    op.add_column(
        "users",
        sa.Column(
            "master_key",
            sa.String(length=32),
            nullable=True,
            comment="Main registration ID (auto-generated)",
        ),
    )

    op.add_column(
        "users",
        sa.Column(
            "registration_source",
            sa.String(length=50),
            nullable=True,
            comment="Registration source (two_step_registration, etc.)",
        ),
    )

    op.add_column(
        "users",
        sa.Column(
            "firebase_uid", sa.String(length=128), nullable=True, comment="Firebase UID"
        ),
    )

    # Add indexes
    op.create_index("idx_users_master_key", "users", ["master_key"])
    op.create_index("idx_users_firebase_uid", "users", ["firebase_uid"])


def downgrade() -> None:
    # Remove indexes
    op.drop_index("idx_users_firebase_uid", "users")
    op.drop_index("idx_users_master_key", "users")

    # Remove added columns from users table
    op.drop_column("users", "firebase_uid")
    op.drop_column("users", "registration_source")
    op.drop_column("users", "master_key")
