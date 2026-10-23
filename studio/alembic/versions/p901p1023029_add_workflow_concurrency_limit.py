"""add per-plan workflow concurrency limit and workflow lease

- subscription_plans.max_concurrent_workflows: NULL = no limit, so existing
  plans keep today's behaviour
- {free,premium}_user_assignments.last_workflow_heartbeat: renewed by live runs;
  the run-start gate treats a stale one as no run alive

Revision ID: p901p1023029
Revises: o901o0716028
Create Date: 2026-10-23 12:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "p901p1023029"
down_revision = "o901o0716028"
branch_labels = None
depends_on = None

ASSIGNMENT_TABLES = ("free_user_assignments", "premium_user_assignments")


def upgrade() -> None:
    op.add_column(
        "subscription_plans",
        sa.Column(
            "max_concurrent_workflows",
            sa.INTEGER(),
            nullable=True,
            comment="Concurrent workflow runs per user of this plan; NULL = no limit",
        ),
    )
    for table in ASSIGNMENT_TABLES:
        op.add_column(
            table,
            sa.Column(
                "last_workflow_heartbeat",
                sa.TIMESTAMP(),
                nullable=True,
                comment="Last renewal by a live workflow; stale = no run alive",
            ),
        )


def downgrade() -> None:
    for table in ASSIGNMENT_TABLES:
        op.drop_column(table, "last_workflow_heartbeat")
    op.drop_column("subscription_plans", "max_concurrent_workflows")
