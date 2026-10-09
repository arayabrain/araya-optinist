#!/usr/bin/env bash
# Prepare a standalone EC2 instance (Amazon Linux 2023, x86_64) for the
# workflow benchmark (#893 real lane). Run as root, e.g. from user data.
#
# Required environment (set in the user-data header):
#   BENCH_ROLE          demand | replica
#   BENCH_IMAGE_URI     ECR image to benchmark, e.g. <acct>.dkr.ecr.<region>.amazonaws.com/<repo>:<tag>
#   BENCH_S3_PREFIX     s3://<bucket>/<prefix>   (inputs/ is read, results/ is written)
#   BENCH_GIT_REF       harness branch or commit (default feature/893-phase0-benchmark)
#   BENCH_MAX_MINUTES   self-shutdown after this many minutes (default 480)
# Optional:
#   BENCH_SWAP_DEVICE   block device for swap on the replica role (default /dev/nvme1n1)
#
# The instance must be launched with --instance-initiated-shutdown-behavior
# terminate, so the self-shutdown also deletes it.
set -euo pipefail

: "${BENCH_ROLE:?}" "${BENCH_IMAGE_URI:?}" "${BENCH_S3_PREFIX:?}"
BENCH_GIT_REF="${BENCH_GIT_REF:-feature/893-phase0-benchmark}"
BENCH_MAX_MINUTES="${BENCH_MAX_MINUTES:-480}"
BENCH_SWAP_DEVICE="${BENCH_SWAP_DEVICE:-/dev/nvme1n1}"
ROOT=/opt/bench
REGION="$(TOKEN=$(curl -s -X PUT http://169.254.169.254/latest/api/token \
  -H 'X-aws-ec2-metadata-token-ttl-seconds: 60') && \
  curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/placement/region)"

log() { echo "[bootstrap $(date -u +%H:%M:%S)] $*"; }

# 1. Safety net first: the instance deletes itself even if nothing else runs
shutdown -h "+${BENCH_MAX_MINUTES}" "phase0 benchmark: max runtime reached"
log "self-shutdown scheduled in ${BENCH_MAX_MINUTES} min"

# 2. Packages
# AL2023 ships python3, curl and the AWS CLI v2
dnf install -y -q docker git > /dev/null
systemctl enable --now docker

# 3. Swap — replica role only, mirroring production: a dedicated volume, swappiness 20
if [[ "$BENCH_ROLE" == "replica" ]]; then
  mkswap "$BENCH_SWAP_DEVICE"
  swapon "$BENCH_SWAP_DEVICE"
  sysctl -w vm.swappiness=20
  log "swap on $BENCH_SWAP_DEVICE: $(free -g | awk '/Swap/ {print $2}') GiB"
fi

# 4. Harness (public repository, no credentials needed)
mkdir -p "$ROOT"
git clone --quiet --depth 1 --branch "$BENCH_GIT_REF" \
  https://github.com/arayabrain/araya-optinist.git "$ROOT/repo"
log "harness at $(git -C "$ROOT/repo" rev-parse --short HEAD)"

# 5. Image: pull from ECR and tag with the name run_bench.sh expects
REGISTRY="${BENCH_IMAGE_URI%%/*}"
aws ecr get-login-password --region "$REGION" | docker login -u AWS --password-stdin "$REGISTRY"
docker pull -q "$BENCH_IMAGE_URI"
docker tag "$BENCH_IMAGE_URI" development-optinist-for-cloud:latest

# 6. Inputs and working directories (layout matches run_bench.sh defaults)
mkdir -p "$ROOT/inputs" "$ROOT/optinist-docker-volumes/.snakemake" \
  "$ROOT/optinist-docker-volumes/bench_studio_data" "$ROOT/results"
aws s3 cp --quiet --recursive "$BENCH_S3_PREFIX/inputs/" "$ROOT/inputs/"
ls -la "$ROOT/inputs"

log "ready: role=$BENCH_ROLE"
