#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# terraform_build_info.sh
# ============================================================================
# Emits terraform-apply provenance as JSON on stdout, for consumption by the
# Terraform `external` data source (data.external.tf_build_info).
#
# This records "which git revision of infrastructure/ was applied", mirroring
# the Docker /app/BUILD_INFO concept (which records image provenance) at the
# infrastructure layer. The values are stamped onto the ECS cluster as tags so
# a deployment can be traced back to its source revision from the running env.
#
# `external` requires a flat JSON object of string values on stdout.
# ============================================================================

# Resolve to the infrastructure/ directory so git info reflects the IaC repo,
# regardless of the caller's working directory.
cd "$(dirname "$0")/.."

# The commit is no longer decoration: deployment.tf keys
# null_resource.build_and_deploy on it, so falling back to a constant would
# leave the trigger set identical across commits -- the exact condition that
# made a routine apply skip the image build. Fail the plan instead, loudly,
# rather than restoring that silently wherever git metadata is absent (a
# packaged bundle, a .git-stripped CI checkout).
if ! git_commit=$(git rev-parse HEAD 2>/dev/null); then
  echo "terraform_build_info.sh: no git metadata under $(pwd)." >&2
  echo "  source_revision would be constant, so a new image would not deploy." >&2
  echo "  Run terraform from a git checkout." >&2
  exit 1
fi

# The branch is only ever a tag value, so a fallback is harmless here.
git_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
# `|| true` keeps the dirty check non-fatal under `set -e`
git_status=$(git status --porcelain 2>/dev/null || true)
if [ -n "$git_status" ]; then
  git_dirty="true"
else
  git_dirty="false"
fi

# Encode as JSON via python3 (available in the toolchain; avoids a jq dependency).
python3 -c "import json,sys; json.dump({'git_commit':sys.argv[1],'git_branch':sys.argv[2],'git_dirty':sys.argv[3]}, sys.stdout)" \
  "$git_commit" "$git_branch" "$git_dirty"
