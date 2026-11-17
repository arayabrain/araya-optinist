#!/usr/bin/env bash
# Prepare a standalone EC2 instance (Amazon Linux 2023, x86_64) for the
# workflow benchmark's real-infrastructure lane. Run as root, e.g. from user data.
#
# Required environment (set in the user-data header):
#   BENCH_IMAGE_URI     ECR image to benchmark, e.g. <acct>.dkr.ecr.<region>.amazonaws.com/<repo>:<tag>
#   BENCH_S3_PREFIX     s3://<bucket>/<prefix>   (inputs/ is read, results/ is written)
# Optional:
#   BENCH_GIT_REF       harness branch or tag to clone (default develop-main)
#   BENCH_MAX_MINUTES   self-shutdown after this many minutes (default 480)
#   BENCH_SWAP_DEVICE   an empty volume to use as host swap, as a deployed
#                       instance has (unset: no swap). Variant S needs it. Give a
#                       stable name (/dev/disk/by-id/..., or the /dev/sdX link of
#                       the EBS mapping); anything mounted or not empty is refused
#
# The instance must be launched with --instance-initiated-shutdown-behavior
# terminate, so the self-shutdown also deletes it. This script does not start
# the case list: run aws/run_cases.sh afterwards, with its own environment.
set -euo pipefail

: "${BENCH_IMAGE_URI:?}" "${BENCH_S3_PREFIX:?}"
BENCH_GIT_REF="${BENCH_GIT_REF:-develop-main}"
BENCH_MAX_MINUTES="${BENCH_MAX_MINUTES:-480}"
BENCH_SWAP_DEVICE="${BENCH_SWAP_DEVICE:-}"
ROOT=/opt/bench
REGION="$(TOKEN=$(curl -s -X PUT http://169.254.169.254/latest/api/token \
  -H 'X-aws-ec2-metadata-token-ttl-seconds: 60') && \
  curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/placement/region)"

log() { echo "[bootstrap $(date -u +%H:%M:%S)] $*"; }
die() { log "ERROR: $*"; exit 1; }

# mkswap wipes the device it is given. Nitro instances name NVMe devices in
# probe order, which can change across boots, so refuse anything that is not an
# empty, unmounted, non-root disk
check_swap_device() {
  local dev root_src root_disk
  dev="$(readlink -f "$1")"
  [[ -b "$dev" ]] || die "swap device $1 is not a block device"
  root_src="$(findmnt -no SOURCE / || true)"
  [[ -n "$root_src" ]] || die "cannot tell which disk holds /"
  # / on a partition: its disk; on a whole disk: that disk
  root_disk="$(lsblk -no PKNAME "$root_src" 2>/dev/null || true)"
  root_disk="${root_disk:-$(basename "$root_src")}"
  [[ "$(basename "$dev")" != "$root_disk" && "$(lsblk -no PKNAME "$dev")" != "$root_disk" ]] \
    || die "swap device $1 ($dev) is the root disk or on it"
  [[ -z "$(lsblk -no MOUNTPOINTS "$dev" | tr -d '[:space:]')" ]] \
    || die "swap device $1 ($dev) or a partition of it is mounted"
  # blkid -p exits 0 when it finds any signature (filesystem, partition table, swap)
  ! blkid -p "$dev" > /dev/null 2>&1 \
    || die "swap device $1 ($dev) is not empty: $(blkid -p -o export "$dev" | tr '\n' ' ')"
}

# 1. Safety net first: the instance deletes itself even if nothing else runs
shutdown -h "+${BENCH_MAX_MINUTES}" "workflow benchmark: max runtime reached"
log "self-shutdown scheduled in ${BENCH_MAX_MINUTES} min"

# 2. Packages
# AL2023 ships python3, curl and the AWS CLI v2
dnf install -y -q docker git > /dev/null
systemctl enable --now docker

# 3. Swap, as on a deployed instance: a dedicated volume, swappiness 20
if [[ -n "$BENCH_SWAP_DEVICE" ]]; then
  check_swap_device "$BENCH_SWAP_DEVICE"
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

log "ready"
