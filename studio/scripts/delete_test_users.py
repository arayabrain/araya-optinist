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
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from studio.app.common.models.experiment import ExperimentRecord
    from studio.app.common.models.subscription import (
        SubscriptionCancellation,
        SubscriptionUserAccount,
        SubscriptionUserPurchase,
        UserStorageUsage,
        UserSubscription,
    )
    from studio.app.common.models.user import User as UserModel
    from studio.app.common.models.user import UserRole
    from studio.app.common.models.workspace import Workspace, WorkspacesShareUser
except ImportError as e:
    print(f"Import error: {e}")
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
    """Get test user data from unified configuration loader."""
    from test_user_config import load_test_users_for_db, print_configuration_help

    test_users = load_test_users_for_db()

    if not test_users:
        print_configuration_help()
        print("This script requires test user configuration.")
        return []

    return test_users


async def delete_test_user_from_db(db, user_email):
    """Delete a test user and all related data from the database."""
    from studio.app.common.core.storage.remote_storage_controller import (
        RemoteStorageController,
        RemoteStorageSimpleWriter,
    )
    from studio.app.common.core.workspace.workspace_services import WorkspaceService

    user_db = db.query(UserModel).filter_by(email=user_email).first()
    if not user_db:
        print(f"User not found: {user_email} (skipping)")
        return False

    user_id = user_db.id
    user_name = user_db.name
    remote_bucket_name = user_db.remote_bucket_name

    print(f"Deleting user: {user_name} ({user_email})")
    print(f"User ID: {user_id}")
    if remote_bucket_name:
        print(f"S3 Bucket: {remote_bucket_name}")

    try:
        # ----------------------------------------
        # Delete a User workspace contents (with S3 cleanup)
        # ----------------------------------------

        # Get workspaces for this user (non-deleted ones for proper cleanup)
        workspaces = (
            db.query(Workspace)
            .filter(Workspace.user_id == user_id, Workspace.deleted.is_(False))
            .all()
        )
        workspace_ids = [ws.id for ws in workspaces]

        # Delete owned workspaces using WorkspaceService (handles S3 cleanup)
        workspace_count = 0
        if workspace_ids:
            for workspace_id in workspace_ids:
                try:
                    await WorkspaceService.process_workspace_deletion(
                        db, remote_bucket_name, workspace_id, user_id
                    )
                    workspace_count += 1
                except Exception as ws_error:
                    print(
                        f"Warning: Error deleting workspace {workspace_id}: {ws_error}"
                    )

        # ----------------------------------------
        # Delete remaining database records
        # ----------------------------------------
        # Note: WorkspaceService.process_workspace_deletion above should have handled
        # most workspace-related cleanup, but we clean up any remaining records here

        # 1. Delete any remaining experiment records (references workspaces)
        all_workspaces = db.query(Workspace).filter_by(user_id=user_id).all()
        all_workspace_ids = [ws.id for ws in all_workspaces]

        experiment_count = 0
        if all_workspace_ids:
            experiments = (
                db.query(ExperimentRecord)
                .filter(ExperimentRecord.workspace_id.in_(all_workspace_ids))
                .all()
            )
            experiment_count = len(experiments)
            for experiment in experiments:
                db.delete(experiment)

        # 2. Delete workspace share records (references workspaces and users)
        workspace_share_count = 0
        if all_workspace_ids:
            workspace_shares = (
                db.query(WorkspacesShareUser)
                .filter(WorkspacesShareUser.workspace_id.in_(all_workspace_ids))
                .all()
            )
            workspace_share_count = len(workspace_shares)
            for share in workspace_shares:
                db.delete(share)

        # 3. Delete all workspaces (both deleted and non-deleted)
        for workspace in all_workspaces:
            db.delete(workspace)

        # 4. Delete storage usage (references users)
        storage_records = db.query(UserStorageUsage).filter_by(user_id=user_id).all()
        storage_count = len(storage_records)
        for storage in storage_records:
            db.delete(storage)

        # 5. Delete subscription user accounts (references users)
        user_accounts = (
            db.query(SubscriptionUserAccount).filter_by(user_id=user_id).all()
        )
        user_account_count = len(user_accounts)
        for account in user_accounts:
            db.delete(account)

        # 6. Delete subscription cancellations (references purchases)
        # First get all purchase IDs for this user
        purchases = db.query(SubscriptionUserPurchase).filter_by(user_id=user_id).all()
        purchase_ids = [p.id for p in purchases]

        cancellation_count = 0
        if purchase_ids:
            cancellations = (
                db.query(SubscriptionCancellation)
                .filter(SubscriptionCancellation.purchases_id.in_(purchase_ids))
                .all()
            )
            cancellation_count = len(cancellations)
            for cancellation in cancellations:
                db.delete(cancellation)

        # 7. Delete subscription purchases (references users)
        purchase_count = len(purchases)
        for purchase in purchases:
            db.delete(purchase)

        # 8. Delete subscriptions (references users)
        subscriptions = db.query(UserSubscription).filter_by(user_id=user_id).all()
        subscription_count = len(subscriptions)
        for subscription in subscriptions:
            db.delete(subscription)

        # 9. Delete user roles (references users)
        user_roles = db.query(UserRole).filter_by(user_id=user_id).all()
        user_role_count = len(user_roles)
        for role in user_roles:
            db.delete(role)

        # 10. Delete premium user assignments (stored in separate table, not ORM)
        assignment_result = db.execute(
            text("DELETE FROM premium_user_assignments WHERE user_id = :user_id"),
            {"user_id": user_id},
        )
        assignment_count = assignment_result.rowcount

        # 11. Finally delete the user
        db.delete(user_db)

        db.commit()

        # ----------------------------------------
        # Delete a User remote storage bucket (S3)
        # ----------------------------------------
        # Do this after database commit so bucket deletion
        # failure doesn't rollback DB changes

        if remote_bucket_name and RemoteStorageController.is_available():
            try:
                print(f"Deleting S3 bucket: {remote_bucket_name}")
                async with RemoteStorageSimpleWriter(
                    remote_bucket_name
                ) as remote_storage_controller:
                    await remote_storage_controller.delete_bucket(force_delete=True)
                print("S3 bucket deleted successfully")
            except Exception as s3_error:
                print(
                    f"Warning: Error deleting S3 bucket "
                    f"{remote_bucket_name}: {s3_error}"
                )
                print("(Continuing with user deletion)")

        print(f"Deleted {experiment_count} experiments")
        print(f"Deleted {workspace_share_count} workspace shares")
        print(f"Deleted {workspace_count} workspaces")
        print(f"Deleted {storage_count} storage records")
        print(f"Deleted {user_account_count} subscription user accounts")
        print(f"Deleted {cancellation_count} subscription cancellations")
        print(f"Deleted {purchase_count} subscription purchases")
        print(f"Deleted {subscription_count} subscriptions")
        print(f"Deleted {user_role_count} user roles")
        print(f"Deleted {assignment_count} premium assignments")
        print(f"Successfully deleted user: {user_name}")

        return True

    except Exception as e:
        print(f"Error deleting user {user_name}: {str(e)}")
        db.rollback()
        return False


async def main():
    print("Deleting specific test users...")

    db_url = get_database_url()
    if not db_url:
        print("Error: Could not determine database URL.")
        return

    print("📦 Connecting to database...")

    try:
        engine = create_engine(db_url)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        db = SessionLocal()

        # Get test users from environment variable
        test_users = get_test_users()
        if not test_users:
            print("No test users to delete. Exiting.")
            return

        print(f"Found {len(test_users)} test users to delete")

        # Delete test users
        deleted_count = 0
        for user_data in test_users:
            try:
                success = await delete_test_user_from_db(db, user_data["email"])
                if success:
                    deleted_count += 1

            except Exception as e:
                print(f"Error deleting user {user_data['email']}: {str(e)}")
                db.rollback()
                continue

        print(f"\nSuccessfully deleted {deleted_count} test users!")

        # Clean up any orphaned premium assignments (for users that no longer exist)
        print("\nCleaning up orphaned premium assignments...")
        try:
            orphan_result = db.execute(
                text(
                    """DELETE FROM premium_user_assignments
                        WHERE user_id NOT IN (SELECT id FROM users)"""
                )
            )
            orphan_count = orphan_result.rowcount
            db.commit()
            if orphan_count > 0:
                print(f"Cleaned up {orphan_count} orphaned premium assignment(s)")
            else:
                print("No orphaned premium assignments found")
        except Exception as orphan_error:
            print(f"Warning: Could not clean up orphaned assignments: {orphan_error}")
            db.rollback()

        db.close()

    except Exception as e:
        print(f"Database connection error: {str(e)}")


if __name__ == "__main__":
    asyncio.run(main())
