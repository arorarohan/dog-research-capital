#!/usr/bin/env bash
# (Re-)authorize the rclone remote `gdrive` against our own Google OAuth client.
#
# While the OAuth app is in "Testing" status, Google expires refresh tokens after 7 days, so
# run this about weekly (drc-sync warns from day 5). Run it where a browser can reach
# http://127.0.0.1:53682 (your laptop), then copy the remote's token to other machines.
#
#   bash deploy/reauth-drive.sh <client_id>
#
# The client secret is read from ~/.config/rclone/client_secret.txt (mode 600), never from the
# command line, so it does not land in shell history or chat. Prints no secrets or tokens.
set -euo pipefail
export SUPPRESS_LABEL_WARNING=True
R="${RCLONE:-rclone}"
CONF_DIR="${RCLONE_CONFIG_DIR:-$HOME/.config/rclone}"
SECRET_FILE="$CONF_DIR/client_secret.txt"
STAMP="$CONF_DIR/drc-authorized-at"
ID="${1:-${DRC_OAUTH_CLIENT_ID:-}}"
[ -n "$ID" ] || { echo "usage: $0 <client_id>  (or set DRC_OAUTH_CLIENT_ID)"; exit 1; }
SECRET=$(tr -d '\n\r ' < "$SECRET_FILE")
[ -n "$SECRET" ] || { echo "no client secret in $SECRET_FILE"; exit 1; }

LOG=$(mktemp)
trap 'rm -f "$LOG"' EXIT
cp "$CONF_DIR/rclone.conf" "$CONF_DIR/rclone.conf.bak" && chmod 600 "$CONF_DIR/rclone.conf.bak"

"$R" authorize drive "$ID" "$SECRET" --auth-no-open-browser > "$LOG" 2>&1 &
PID=$!
sleep 4
grep -o "http://127.0.0.1:53682/auth?state=[A-Za-z0-9_-]*" "$LOG" | head -1 | sed 's/^/OPEN THIS LINK: /'
wait $PID

TOKEN=$(python3 -I - "$LOG" <<'PY'
import json, re, sys
t = open(sys.argv[1]).read()
m = re.search(r'(\{"access_token".*?\})\s*(?:<---|$)', t, re.S)
if not m:
    sys.exit("no token found; did the consent complete?")
json.loads(m.group(1))
print(m.group(1))
PY
)
"$R" config update gdrive client_id="$ID" client_secret="$SECRET" token="$TOKEN" > /dev/null
chmod 600 "$CONF_DIR/rclone.conf"
date +%s > "$STAMP"
echo "gdrive re-authorized at $(date -u +%FT%TZ)"
"$R" about gdrive: 2>&1 | head -4
