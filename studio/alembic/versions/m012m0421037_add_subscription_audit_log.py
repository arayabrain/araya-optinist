"""add_subscription_audit_log

Audit log table for admin-initiated subscription changes.
Records old/new values and the reason for each manual edit.

Table created:
- subscription_audit_log: Tracks admin edits to user subscriptions

Revision ID: m012m0421037
Revises: l901l9310026
Create Date: 2026-03-13 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "m012m0421037"
down_revision = "l901l9310026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    if "subscription_audit_log" not in existing_tables:
        op.create_table(
            "subscription_audit_log",
            sa.Column(
                "id",
                mysql.BIGINT(unsigned=False),
                primary_key=True,
                autoincrement=True,
                nullable=False,
            ),
            sa.Column(
                "user_id",
                mysql.BIGINT(unsigned=False),
                nullable=False,
                comment="The user whose subscription was changed",
            ),
            sa.Column(
                "changed_by",
                mysql.BIGINT(unsigned=False),
                nullable=False,
                comment="Admin user ID who made the change",
            ),
            sa.Column(
                "old_value",
                sa.JSON(),
                nullable=False,
                comment="Subscription state before the change",
            ),
            sa.Column(
                "new_value",
                sa.JSON(),
                nullable=False,
                comment="Subscription state after the change",
            ),
            sa.Column(
                "reason",
                sa.Text(),
                nullable=False,
                comment="Admin-provided reason for the manual edit",
            ),
            sa.Column(
                "created_at",
                sa.TIMESTAMP(),
                server_default=sa.func.current_timestamp(),
                nullable=True,
            ),
        )
        op.create_index(
            "idx_subscription_audit_log_user_id",
            "subscription_audit_log",
            ["user_id"],
        )


def downgrade() -> None:
    op.drop_index(
        "idx_subscription_audit_log_user_id",
        table_name="subscription_audit_log",
    )
    op.drop_table("subscription_audit_log")
