#!/usr/bin/env bash
# Idempotent setup for a fresh Ubuntu/Debian VM (Oracle Always Free, GCP e2-micro, etc.).
# Run as the normal user (needs sudo for apt + systemd). Usage: bash deploy/setup-vm.sh
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/arorarohan/dog-research-capital.git}"
APP_DIR="${APP_DIR:-$HOME/dog-research-capital}"

sudo apt-get update -y
sudo apt-get install -y git curl ca-certificates

# Small VMs (1 GB RAM) need swap for `uv sync` / polars.
if [ "$(free -m | awk '/^Mem:/{print $2}')" -lt 2000 ] && [ ! -f /swapfile ]; then
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
  sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

[ -d "$APP_DIR/.git" ] || git clone "$REPO_URL" "$APP_DIR"
cd "$APP_DIR" && git pull --ff-only && uv sync

# Connectivity check: some venues geo-block datacenter regions. Fail loudly now, not at 3am.
check() {
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$2" || true)
  echo "$1: HTTP $code"
}
check "polymarket gamma" "https://gamma-api.polymarket.com/markets?limit=1"
check "polymarket clob"  "https://clob.polymarket.com/time"
check "kalshi"           "https://api.elections.kalshi.com/trade-api/v2/exchange/status"
check "coinbase"         "https://api.exchange.coinbase.com/time"
check "kraken"           "https://api.kraken.com/0/public/Time"

# Install the recorder as a service (restarts on failure, starts at boot).
sed "s|__USER__|$USER|g; s|__APP_DIR__|$APP_DIR|g" deploy/systemd/drc-record.service \
  | sudo tee /etc/systemd/system/drc-record.service >/dev/null
sudo systemctl daemon-reload
echo "Setup done. Review the connectivity lines above, then: sudo systemctl enable --now drc-record"
