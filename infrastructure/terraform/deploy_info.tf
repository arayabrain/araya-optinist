# ============================================================================
# Terraform apply provenance
# ============================================================================
# Records which git revision of infrastructure/ was applied, so a running
# deployment can be traced back to its source. /app/BUILD_INFO is the
# image-layer equivalent.
#
#   Gathered by  scripts/terraform_build_info.sh, at apply time
#   Stamped on   aws_ecs_cluster.main (compute.tf), as TfGit* tags
#   Diff churn   only when a recorded value changes, so a re-apply from the
#                same checkout is a no-op. The checkout state is recorded too,
#                not just the commit: re-applying one commit from a tag after
#                a branch does rewrite TfGitBranch and TfGitTag.
#   Blast radius no longer just the cluster tag: deployment.tf keys
#                null_resource.build_and_deploy on the commit, so a new one
#                rebuilds the image and rolls every service. The value is the
#                repository HEAD, coarser than "the image changed" -- a
#                docs-only commit triggers the same rollout.
#
# Read back with:
#   aws ecs describe-clusters --clusters <cluster-name> --include TAGS \
#     --query 'clusters[0].tags' --output table
# ============================================================================

data "external" "tf_build_info" {
  program = ["bash", "${path.module}/../scripts/terraform_build_info.sh"]
}
