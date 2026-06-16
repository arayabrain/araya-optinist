#!/usr/bin/env python3
"""
Script to seed subscription plans in the database from SUBSCRIPTION_PLANS_CONFIG.

Usage:
    python scripts/seed_subscription_plans.py

Prerequisites:
    - Database connection must be available
    - SUBSCRIPTION_PLANS_CONFIG environment variable must be set (JSON format)
"""

import json
import os
import sys
from pathlib import Path

# Add the project root directory to the Python path
# In Docker: /app/scripts/ -> parent.parent = /app
# Locally: <repo>/infrastructure/scripts/ -> parent.parent.parent = <repo>
project_root = Path(__file__).parent.parent
if not (project_root / "studio").exists():
    project_root = project_root.parent
sys.path.insert(0, str(project_root))

try:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from studio.app.common.models.subscription import SubscriptionPlans
except ImportError as e:
    print(f"Import error: {e}")
    print("Make sure you're running from the project root directory")
    sys.exit(1)


def get_database_url():
    """Get database URL from environment variables."""
    from studio.app.common.db.config import build_mysql_url

    db_url = (
        os.getenv("DATABASE_URL")
        or os.getenv("DB_URL")
        or os.getenv("SQLALCHEMY_DATABASE_URL")
    )
    if db_url:
        return db_url

    return build_mysql_url(
        user=os.getenv("DB_USER", os.getenv("MYSQL_USER", "root")),
        password=os.getenv("DB_PASSWORD", os.getenv("MYSQL_PASSWORD", "")),
        host=os.getenv("DB_HOST", os.getenv("MYSQL_SERVER", "localhost")),
        database=os.getenv("DB_NAME", os.getenv("MYSQL_DATABASE", "optinist")),
        port=int(os.getenv("DB_PORT", "3306")),
    )


def get_subscription_plans():
    """Load subscription plans from SUBSCRIPTION_PLANS_CONFIG env var."""
    config_json = os.getenv("SUBSCRIPTION_PLANS_CONFIG")
    if not config_json:
        return []

    try:
        plans = json.loads(config_json)
        return plans if isinstance(plans, list) else []
    except json.JSONDecodeError as e:
        print(f"Failed to parse SUBSCRIPTION_PLANS_CONFIG: {e}")
        return []


def main():
    plans_data = get_subscription_plans()
    if not plans_data:
        print(
            "No subscription plans configured "
            "(SUBSCRIPTION_PLANS_CONFIG not set or empty). Skipping."
        )
        return

    db_url = get_database_url()
    if not db_url:
        print("Error: Could not determine database URL.")
        return

    print(f"Seeding {len(plans_data)} subscription plans...")

    try:
        from studio.app.common.db.config import get_ssl_creator

        kwargs = {}
        creator = get_ssl_creator()
        if creator:
            kwargs["creator"] = creator
        engine = create_engine(db_url, **kwargs)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        db = SessionLocal()

        for plan_data in plans_data:
            plan_id = plan_data.get("id")
            if plan_id is None:
                print(
                    f"  Skipping plan without id: "
                    f"{plan_data.get('name', 'unknown')}"
                )
                continue

            # Check if plan already exists
            existing = db.query(SubscriptionPlans).filter_by(id=plan_id).first()

            # Map tfvars fields to DB model fields
            price = plan_data.get("price", 0)
            plan_fields = {
                "name": plan_data.get("name"),
                "price": price,
                "billing_cycle": plan_data.get("billing_cycle", 1),
                "currency": plan_data.get("currency", 1),
                "status": bool(plan_data.get("status", 1)),
                "stripe_product_id": plan_data.get("stripe_product_id"),
                "stripe_price_id": plan_data.get("stripe_price_id"),
                "features": plan_data.get("features"),
                "display_order": plan_data.get("display_order", 0),
                "is_featured": bool(plan_data.get("is_featured", False)),
                "tier": plan_data.get("tier", "free" if price == 0 else "premium"),
                "is_hidden": bool(plan_data.get("is_hidden", False)),
            }

            if existing:
                # Update existing plan
                for key, value in plan_fields.items():
                    if value is not None:
                        setattr(existing, key, value)
                print(f"  Updated plan: {plan_fields['name']} (id={plan_id})")
            else:
                # Insert new plan with explicit id
                plan = SubscriptionPlans(id=plan_id, **plan_fields)
                db.add(plan)
                print(f"  Created plan: {plan_fields['name']} (id={plan_id})")

        db.commit()
        db.close()

        print(f"Subscription plans seeded successfully ({len(plans_data)} plans)")

    except Exception as e:
        print(f"Error seeding subscription plans: {e}")
        raise


if __name__ == "__main__":
    main()
