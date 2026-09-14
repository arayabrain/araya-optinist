# ============================================================================
# Terraform apply provenance
# ============================================================================
# Records which git revision of infrastructure/ was applied, so a running
# deployment can be traced back to its source revision. This mirrors the Docker
# /app/BUILD_INFO concept (image provenance) at the infrastructure layer.
#
# The values are gathered at apply time by scripts/terraform_build_info.sh and
# stamped onto the ECS cluster as tags (see aws_ecs_cluster.main in compute.tf).
# The tag only changes when the git commit changes, so no-op applies produce no
# diff. Blast radius on a real deploy is no longer just the cluster tag:
# deployment.tf keys null_resource.build_and_deploy on the same value, so a new
# commit rebuilds the image and rolls every service in the cluster. Note the
# revision is the repository HEAD, which is coarser than "the image changed" --
# a docs-only commit triggers the same rollout.
#
# Inspect from the running environment with:
#   aws ecs describe-clusters --clusters <cluster-name> --include TAGS \
#     --query 'clusters[0].tags' --output table
# ============================================================================

data "external" "tf_build_info" {
  program = ["bash", "${path.module}/../scripts/terraform_build_info.sh"]
}
