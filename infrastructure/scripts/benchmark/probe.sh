#!/bin/sh
# External health prober, on the same terms as the #625 / #643 baseline:
# GET /health once per interval; failure = non-200 or timeout.
# Usage: probe.sh <url> <output.tsv> [interval_s] [timeout_s]
set -u
URL="$1"
OUT="$2"
INTERVAL="${3:-1}"
TIMEOUT="${4:-10}"

printf 'epoch\thttp_code\ttime_total_s\n' > "$OUT"
while :; do
  START=$(date +%s)
  RESULT=$(curl -s -o /dev/null -w '%{http_code}\t%{time_total}' --max-time "$TIMEOUT" "$URL" 2>/dev/null)
  printf '%s\t%s\n' "$START" "${RESULT:-000	$TIMEOUT}" >> "$OUT"
  sleep "$INTERVAL"
done
