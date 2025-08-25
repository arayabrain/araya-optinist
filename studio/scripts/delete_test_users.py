#!/usr/bin/env python3
"""
Script to delete specific test users from the database.

Usage:
    python delete_specific_test_users.py

Deletes the test users created by create_test_users.py
"""

import asyncio
import os
import sys
from pathlib import Path

# Add the project root directory to the Python path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Import after path modification to avoid E402 linting errors
try:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from studio.app.common.models.experiment import ExperimentRecord
    from studio.app.common.models.subscription import UserStorageUsage, UserSubscription
    from studio.app.common.models.user import User as UserModel
    from studio.app.common.models.workspace import Workspace
except ImportError as e:
    print(f"❌ Import error: {e}")
    print("Make sure you're running from the studio directory")
    sys.exit(1)


def get_database_url():
    """Get database URL from environment variables."""
    db_url = (
        os.getenv("DATABASE_URL")
        or os.getenv("DB_URL")
        or os.getenv("SQLALCHEMY_DATABASE_URL")
    )

    if not db_url:
        host = os.getenv("DB_HOST", "localhost")
        port = os.getenv("DB_PORT", "3306")
        user = os.getenv("DB_USER", "root")
        password = os.getenv("DB_PASSWORD", "")
        database = os.getenv("DB_NAME", "optinist")

        db_url = f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}"

    return db_url


def get_test_users():
    """Get test user data from environment variable (set by Terraform)."""
    import json

    # Get test users from environment variable (set by Terraform)
    test_users_json = os.getenv("TEST_USERS_CONFIG")

    if not test_users_json:
        print("❌ Error: TEST_USERS_CONFIG environment variable not set.")
        print("This script requires test user configuration from Terraform.")
        return []

    try:
        return json.loads(test_users_json)
    except json.JSONDecodeError as e:
        print(f"❌ Error: Could not parse TEST_USERS_CONFIG: {e}")
        return []


async def delete_test_user_from_db(db, user_email):
    """Delete a test user and all related data from the database."""

    user_db = db.query(UserModel).filter_by(email=user_email).first()
    if not user_db:
        print(f"⚠️  User not found: {user_email} (skipping)")
        return False

    user_id = user_db.id
    user_name = user_db.name

    print(f"🗑️  Deleting user: {user_name} ({user_email})")
    print(f"   - User ID: {user_id}")

    try:
        # Get workspaces for this user
        workspaces = db.query(Workspace).filter_by(user_id=user_id).all()
        workspace_ids = [ws.id for ws in workspaces]

        # Delete in correct order to respect foreign key constraints

        # 1. Delete experiment records (references workspaces)
        experiment_count = 0
        if workspace_ids:
            experiments = (
                db.query(ExperimentRecord)
                .filter(ExperimentRecord.workspace_id.in_(workspace_ids))
                .all()
            )
            experiment_count = len(experiments)
            for experiment in experiments:
                db.delete(experiment)

        # 2. Delete workspaces (references users)
        workspace_count = len(workspaces)
        for workspace in workspaces:
            db.delete(workspace)

        # 3. Delete storage usage (references users)
        storage_records = db.query(UserStorageUsage).filter_by(user_id=user_id).all()
        storage_count = len(storage_records)
        for storage in storage_records:
            db.delete(storage)

        # 4. Delete subscriptions (references users)
        subscriptions = db.query(UserSubscription).filter_by(user_id=user_id).all()
        subscription_count = len(subscriptions)
        for subscription in subscriptions:
            db.delete(subscription)

        # 5. Finally delete the user
        db.delete(user_db)

        db.commit()

        print(f"   - Deleted {experiment_count} experiments")
        print(f"   - Deleted {workspace_count} workspaces")
        print(f"   - Deleted {storage_count} storage records")
        print(f"   - Deleted {subscription_count} subscriptions")
        print(f"✅ Successfully deleted user: {user_name}")

        return True

    except Exception as e:
        print(f"❌ Error deleting user {user_name}: {str(e)}")
        db.rollback()
        return False


async def main():
    print("🚀 Deleting specific test users...")

    db_url = get_database_url()
    if not db_url:
        print("❌ Error: Could not determine database URL.")
        return

    print("📦 Connecting to database...")

    try:
        engine = create_engine(db_url)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        db = SessionLocal()

        # Get test users from environment variable
        test_users = get_test_users()
        if not test_users:
            print("❌ No test users to delete. Exiting.")
            return

        print(f"📝 Found {len(test_users)} test users to delete")

        # Delete test users
        deleted_count = 0
        for user_data in test_users:
            try:
                success = await delete_test_user_from_db(db, user_data["email"])
                if success:
                    deleted_count += 1

            except Exception as e:
                print(f"❌ Error deleting user {user_data['email']}: {str(e)}")
                db.rollback()
                continue

        print(f"\n✅ Successfully deleted {deleted_count} test users!")

        db.close()

    except Exception as e:
        print(f"❌ Database connection error: {str(e)}")


if __name__ == "__main__":
    asyncio.run(main())
