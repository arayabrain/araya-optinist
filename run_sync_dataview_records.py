import argparse
import sys

from sqlmodel import select

from studio.app.common.core.experiment.experiment_record_services import (
    ExperimentRecordService,
)
from studio.app.common.core.logger import AppLogger
from studio.app.common.db.database import session_scope
from studio.app.common.models.workspace import Workspace

logger = AppLogger.get_logger()


def confirm_all_workspaces_processing():
    """
    Confirm with the user before processing all workspaces
    """
    print(
        "\n" + "=" * 60 + "\n"
        "WARNING: You are about to process ALL workspaces!\n"
        + "=" * 60 + "\n"
        "This will sync dataview records for all active workspaces in the system.\n"
        "This operation may take a considerable amount of time depending on\n"
        "the number of workspaces and experiments.\n"
    )
    response = input("Do you want to continue? (yes/no): ").strip().lower()

    if response not in ["yes", "y"]:
        print("Operation cancelled by user.")
        return False

    return True


def main(args):
    """
    Main function to sync dataview records for all workspaces
    """
    if not ExperimentRecordService.is_available():
        logger.error(
            "ExperimentRecordService is not available. "
            "This script is only for multiuser mode."
        )
        return

    total_success = 0
    total_errors = 0

    if args.wsid:
        # Sync specific workspace
        logger.info(f"Syncing dataview records for workspace: [{args.wsid}]")
        if args.delete_existing:
            logger.warning(f"Deleting existing records for workspace: [{args.wsid}]")

        success, errors = ExperimentRecordService.sync_dataview_records_for_workspace(
            args.wsid, delete_existing=args.delete_existing
        )
        total_success += success
        total_errors += errors
    else:
        # Sync all workspaces - require confirmation
        if not confirm_all_workspaces_processing():
            sys.exit(0)

        logger.info("Syncing dataview records for all workspaces")
        if args.delete_existing:
            logger.warning("Deleting existing records for all workspaces")

        with session_scope() as db:
            workspace_list = db.execute(
                select(Workspace.id).filter(Workspace.deleted.is_(False))
            ).scalars()

            for workspace_id in workspace_list:
                logger.info(f"Processing workspace: [{workspace_id}]")
                (
                    success,
                    errors,
                ) = ExperimentRecordService.sync_dataview_records_for_workspace(
                    str(workspace_id), delete_existing=args.delete_existing
                )
                total_success += success
                total_errors += errors

    logger.info(
        f"Sync dataview records completed. "
        f"Total success: {total_success}, Total errors: {total_errors}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Sync dataview records from local experiment configs to database"
    )
    parser.add_argument(
        "-wsid",
        type=str,
        help="Specific workspace ID to sync (optional, syncs all if not provided)",
    )
    parser.add_argument(
        "--delete-existing",
        action="store_true",
        help="Delete all existing records before syncing (similar to recalculate)",
    )

    main(parser.parse_args())
