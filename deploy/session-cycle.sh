#!/usr/bin/env bash
# One daily forward-test cycle (run by drc-session.timer at 12:00 UTC):
#   1. drc-session for each fund (one transaction per fund; a re-run the same day is a no-op),
#   2. drc-report into a stable directory (always, even if a session failed: the report shows it),
#   3. drc-ledger-backup: dated commit to the private drc-ledger repo + immutable Drive copy.
# Every step runs even if an earlier one failed; the script exits 1 at the end if any did, so
# systemd marks the run failed. Nothing here can trade: SimBroker is the only broker.
set -uo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
: "${DRC_HOST:?set DRC_HOST (e.g. vm-london)}"
FUNDS="${DRC_FUNDS:-pm-sched-gap-v1}"            # space-separated fund ids
REPORT_DIR="${DRC_REPORT_DIR:-data/reports}"     # stable: overwritten in place each day
failed=0

echo "=== session cycle $(date -u +%FT%TZ) host=$DRC_HOST funds=$FUNDS"
for fund in $FUNDS; do
  uv run drc-session --fund "$fund" || { echo "ERROR: drc-session $fund exit $?" >&2; failed=1; }
done
uv run drc-report --out "$REPORT_DIR" || { echo "ERROR: drc-report exit $?" >&2; failed=1; }
uv run drc-ledger-backup backup || { echo "ERROR: drc-ledger-backup exit $?" >&2; failed=1; }

if [ "$failed" -ne 0 ]; then
  echo "=== session cycle FAILED $(date -u +%FT%TZ)" >&2
  exit 1
fi
echo "=== session cycle ok $(date -u +%FT%TZ)"
