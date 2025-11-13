#!/bin/bash
# Entrypoint script for Snakemake batch containers
# Creates symlink to allow wrappers to access files at /app/studio_data

set -e

# Only run symlink setup in AWS Batch jobs
if [ -n "$AWS_BATCH_JOB_ID" ]; then
    echo "[batch-entrypoint] Detected AWS Batch execution (Job ID: $AWS_BATCH_JOB_ID)"

    # Determine bucket name from environment
    BUCKET_NAME="${AWS_BATCH_S3_BUCKET_NAME:-${S3_DEFAULT_BUCKET_NAME}}"

    if [ -z "$BUCKET_NAME" ]; then
        echo "[batch-entrypoint] WARNING: No S3 bucket name found in environment"
        echo "[batch-entrypoint] Storage paths may not resolve correctly"
    else
        # Construct symlink paths
        # Use absolute path for source so Snakemake's absolute paths work
        SOURCE_PATH="/app/.snakemake/storage/s3/${BUCKET_NAME}/app/studio_data"
        TARGET_PATH="/app/studio_data"

        # Create symlink if target doesn't exist
        if [ -L "$TARGET_PATH" ]; then
            EXISTING_TARGET=$(readlink "$TARGET_PATH")
            if [ "$EXISTING_TARGET" = "$SOURCE_PATH" ]; then
                echo "[batch-entrypoint] Batch storage symlink already exists: $TARGET_PATH -> $SOURCE_PATH"
            else
                echo "[batch-entrypoint] WARNING: Symlink exists but points to wrong location:"
                echo "[batch-entrypoint]   $TARGET_PATH -> $EXISTING_TARGET (expected: $SOURCE_PATH)"
            fi
        elif [ -e "$TARGET_PATH" ]; then
            echo "[batch-entrypoint] WARNING: $TARGET_PATH already exists as a directory/file"
            echo "[batch-entrypoint] Cannot create symlink - storage may not work correctly"
        else
            # Create the symlink target directory first
            # This is needed because dir_path.py will try to create subdirectories
            # through the symlink, and os.makedirs follows symlinks
            mkdir -p "$SOURCE_PATH"
            echo "[batch-entrypoint] Created symlink target directory: $SOURCE_PATH"

            # Create the symlink
            ln -s "$SOURCE_PATH" "$TARGET_PATH"
            echo "[batch-entrypoint] Created batch storage symlink: $TARGET_PATH -> $SOURCE_PATH"
        fi
    fi
else
    echo "[batch-entrypoint] Not in batch mode, skipping storage symlink setup"
fi

# Execute the command passed to the container
exec "$@"
