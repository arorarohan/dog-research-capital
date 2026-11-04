#!/usr/bin/env bash
# One archive cycle for a recorder machine (run hourly by drc-sync.timer):
#   1. normalize hours that have closed (never the hour still being recorded),
#   2. upload raw + normalized to the Drive archive (copy-only; compresses finished plain hours),
#   3. verify what was uploaded by size and hash,
#   4. only then prune local raw older than the retention window that Drive confirms.
# Any failing step stops the cycle (set -e), so nothing is pruned after a failed upload. If the
# Drive login expired, drc-sync exits 3 with "AUTH EXPIRED" (see docs/sync.md, Re-authorizing).
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH" SUPPRESS_LABEL_WARNING=True
: "${DRC_HOST:?set DRC_HOST (e.g. vm-london)}"
RAW="${DRC_RAW:-data/raw_forward}"
NORM="${DRC_NORM:-data/norm_forward}"

echo "=== cycle $(date -u +%FT%TZ) host=$DRC_HOST raw=$RAW norm=$NORM"
# Cap the normalizer's address space (default 9 GB on a 12 GB machine) so a runaway can only
# fail itself, never take the recorder down with it. Subshell: rclone (Go) must not inherit it.
(ulimit -v "${DRC_NORMALIZE_VMEM_KB:-9000000}"; uv run drc-normalize --raw "$RAW" --norm "$NORM" --closed-only)
uv run drc-sync push --what raw,norm
uv run drc-sync verify --what raw,norm
uv run drc-sync prune-local --apply
echo "=== cycle ok $(date -u +%FT%TZ)"
