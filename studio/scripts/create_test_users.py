#!/usr/bin/env python3
"""
Script to create test users in the database using existing Firebase accounts.

Usage:
    python scripts/create_test_users.py

Prerequisites:
    - Firebase users must already be created
    - Database connection must be available
    - Organization must exist in database
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add the project root directory to the Python path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Import after path modification to avoid E402 linting errors
try:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from studio.app.common.core.users.crud_users import set_role
    from studio.app.common.models.subscription import UserStorageUsage, UserSubscription
    from studio.app.common.models.user import Organization
except ImportError as e:
    print(f"Import error: {e}")
    print("Make sure you're running from the studio directory")
    sys.exit(1)


def get_test_users():
    """Get test user data from unified configuration loader."""
    from test_user_config import load_test_users_for_db, print_configuration_help

    test_users = load_test_users_for_db()

    if not test_users:
        print_configuration_help()
        print("This script requires test user configuration.")
        return []

    return test_users


def get_database_url():
    """Get database URL from environment variables."""
    # Try common environment variable names
    db_url = (
        os.getenv("DATABASE_URL")
        or os.getenv("DB_URL")
        or os.getenv("SQLALCHEMY_DATABASE_URL")
    )

    if not db_url:
        # Construct from individual components if available
        host = os.getenv("DB_HOST", "localhost")
        port = os.getenv("DB_PORT", "3306")
        user = os.getenv("DB_USER", "root")
        password = os.getenv("DB_PASSWORD", "")
        database = os.getenv("DB_NAME", "optinist")

        db_url = f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}"

    return db_url


async def create_test_user_in_db(db, user_data, organization_id):
    """Create a test user directly in the database (bypassing Firebase creation)."""
    from studio.app.common.models.user import User as UserModel

    # Create user record directly (since Firebase user already exists)
    user_db = UserModel(
        uid=user_data["firebase_uid"],
        email=user_data["email"],
        name=user_data["name"],
        organization_id=organization_id,
        active=True,
    )

    db.add(user_db)
    db.flush()  # Get the user ID

    # Set user role
    await set_role(
        db, user_id=user_db.id, role_id=user_data["role_id"], auto_commit=False
    )

    # Create subscription
    # Set expiration based on subscription plan to test different scenarios
    if user_data["subscription_plan_id"] == 2:  # Premium plan
        if "expire" in user_data["email"]:
            # Only the "expire" user gets expired subscription
            # Expired 50 days ago (past grace period) to trigger warnings
            expiration_date = datetime.now(timezone.utc) - timedelta(days=50)
        else:
            # Other premium users get active subscriptions for priority testing
            expiration_date = datetime.now(timezone.utc) + timedelta(days=365)
    else:  # Free plan
        # For free plan users, set future expiration (no need to test warnings)
        expiration_date = datetime.now(timezone.utc) + timedelta(days=365)

    subscription = UserSubscription(
        plan_id=user_data["subscription_plan_id"],
        user_id=user_db.id,
        expiration=expiration_date,
    )
    db.add(subscription)

    # Create storage usage record
    storage_usage = UserStorageUsage(
        user_id=user_db.id,
        storage_usage_bytes=0,
        storage_quota_bytes=user_data["storage_quota_gb"]
        * 1024
        * 1024
        * 1024,  # Convert GB to bytes
    )
    db.add(storage_usage)

    db.commit()

    print(f"Created user: {user_data['name']} ({user_data['email']})")
    print(f"   - User ID: {user_db.id}")
    print(
        f"   - Plan: {'Premium' if user_data['subscription_plan_id'] == 2 else 'Free'}"
    )
    print(f"   - Storage: {user_data['storage_quota_gb']}GB")

    return user_db


async def main():
    print("Creating test users...")

    # Get database connection
    db_url = get_database_url()
    if not db_url:
        print(
            "Error: Could not determine database URL. "
            "Please set DATABASE_URL environment variable."
        )
        return

    print("📦 Connecting to database...")

    try:
        engine = create_engine(db_url)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        db = SessionLocal()

        # Get organization (assuming there's at least one)
        org = db.query(Organization).first()
        if not org:
            print(
                "Error: No organization found in database. "
                "Please create an organization first."
            )
            return

        print(f"🏢 Using organization: {org.name} (ID: {org.id})")

        # Get test users from environment variable
        test_users = get_test_users()
        if not test_users:
            print("No test users to create. Exiting.")
            return

        print(f"📝 Found {len(test_users)} test users to create")

        # Create test users
        created_users = []
        for user_data in test_users:
            try:
                # Check if user already exists
                existing_user = (
                    db.query(UserModel).filter_by(uid=user_data["firebase_uid"]).first()
                )

                if existing_user:
                    print(f"⚠️  User already exists: {user_data['name']} (skipping)")
                    continue

                user = await create_test_user_in_db(db, user_data, org.id)
                created_users.append(user)

            except Exception as e:
                print(f"Error creating user {user_data['name']}: {str(e)}")
                db.rollback()
                continue

        print(f"\nSuccessfully created {len(created_users)} test users!")
        print("\n📋 Test User Credentials:")
        print("=" * 60)
        for user_data in test_users:
            plan_name = "Premium" if user_data["subscription_plan_id"] == 2 else "Free"
            print(f"Email: {user_data['email']}")
            print(f"Name: {user_data['name']}")
            print(f"Plan: {plan_name}")
            print(f"Storage: {user_data['storage_quota_gb']}GB")
            print("-" * 40)

        db.close()

    except Exception as e:
        print(f"Database connection error: {str(e)}")
        print(
            "\n💡 Make sure your database is running and environment "
            "variables are set correctly."
        )


if __name__ == "__main__":
    # Need to import here to avoid circular imports
    from studio.app.common.models.user import User as UserModel

    asyncio.run(main())
