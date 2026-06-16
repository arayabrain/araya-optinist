"""merge_multi_plan_and_audit_log_heads

Merge the two alembic heads created when
``feature/multiple_plans_implementation`` branched from
``a5b9c8d7e6f5`` and develop-subscription grew its own chain from the
same point.

Heads merged:
- h001h0260022 (add_is_hidden_to_subscription_plans) -- PR #242
- m012m0421037 (add_subscription_audit_log)          -- develop-subscription

This is a topology-only merge; no schema changes.

Revision ID: n001n0430038
Revises: h001h0260022, m012m0421037
Create Date: 2026-04-22 00:00:00.000000

"""

# revision identifiers, used by Alembic.
revision = "n001n0430038"
down_revision = ("h001h0260022", "m012m0421037")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Merge migration — no schema changes."""
    pass


def downgrade() -> None:
    """Merge migration — no schema changes."""
    pass
