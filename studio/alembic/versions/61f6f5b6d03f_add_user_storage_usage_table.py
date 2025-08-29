"""add_user_storage_usage_table

Revision ID: 61f6f5b6d03f
Revises: af8c4144cd54
Create Date: 2025-08-19 13:07:24.738189

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "61f6f5b6d03f"
down_revision = "af8c4144cd54"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create user_storage_usage table
    op.create_table(
        "user_storage_usage",
        sa.Column(
            "id",
            sa.BIGINT,
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("user_id", sa.BIGINT, nullable=False, unique=True),
        sa.Column("storage_usage_bytes", sa.BIGINT, nullable=False, default=0),
        sa.Column("storage_quota_bytes", sa.BIGINT, nullable=False),
        sa.Column(
            "last_updated",
            sa.DateTime,
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("id", name="idx_id"),
        sa.Index("idx_user_storage_usage_user_id", "user_id"),
    )

    # Insert initial storage usage records for existing users
    # Set default quota based on subscription: Free = 5GB, Premium = 100GB
    op.execute(
        """
INSERT INTO
    user_storage_usage
        (user_id, storage_usage_bytes, storage_quota_bytes, last_updated, created_at)
SELECT
    u.id as user_id,
    0 as storage_usage_bytes,
    CASE
        WHEN COALESCE(su.plan_id, 1) = 1 THEN 5368709120    -- 5GB for Free plan
        WHEN COALESCE(su.plan_id, 1) = 2 THEN 107374182400  -- 100GB for Premium plan
        ELSE 5368709120                                      -- Default to 5GB
    END as storage_quota_bytes,
    NOW() as last_updated,
    NOW() as created_at
FROM users u
LEFT JOIN subscription_users su ON u.id = su.user_id
    AND su.expiration > NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM user_storage_usage usu WHERE usu.user_id = u.id
)
"""
    )


def downgrade() -> None:
    op.drop_table("user_storage_usage")
