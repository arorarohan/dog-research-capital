# Cloud VM deployment

Why: the forward test must run every day whether or not the laptop is awake
(`docs/design/forward-test-platform.md`, decision 2026-10-25). The recorder already runs unchanged
on Linux; the daily `drc-session` job (Slice 1, not built yet) joins it as a systemd timer.

Status (2026-10-27): **tested.** VM `drc-vm` runs in Oracle London (AD-3, `VM.Standard.A1.Flex`,
2 OCPU / 12 GB / 100 GB, Ubuntu 24.04 aarch64). `setup-vm.sh` ran clean; all five venue checks
returned HTTP 200 from London; the recorder runs as the `drc-record` systemd service.
Access: `ssh -i ~/.ssh/id_ed25519_drc ubuntu@<public ip>` (key and OCI API config live in
`~/.ssh` and `~/.oci` on Rohan's laptop only, never in the repo). Network: one VCN, public
subnet, SSH is the only inbound port. Created with the OCI CLI (`uv tool install oci-cli`).

## What Rohan does (needs a human and a payment card for identity checks)
1. Create a free-tier VM, Ubuntu 22.04/24.04, with an SSH key.
   - Preferred: **Oracle Cloud Always Free** (ARM Ampere, up to 4 cores / 24 GB, 200 GB disk).
     Sign-up sometimes rejects cards and ARM capacity can be unavailable in busy regions; retry or
     pick another region.
   - Fallback: **GCP e2-micro** (free in a few US regions, 1 GB RAM, 30 GB disk; the setup script
     adds swap).
   - Region matters: a venue may block datacenter IPs in some countries. The setup script prints
     an HTTP code per venue; anything other than 200 means pick another region before going on.
2. SSH in and run:
   `git clone https://github.com/arorarohan/dog-research-capital.git && bash dog-research-capital/deploy/setup-vm.sh`
3. Read the connectivity lines, then `sudo systemctl enable --now drc-record`.

## What the VM records (changed 2026-10-27, issue #17)
The service runs `config/recorder-forward.toml`: the Polymarket deadline universe (ex-ante,
chosen by scheduled end and volume to date) plus the Kalshi weather series, into
`data/raw_forward`. No crypto books. Measured on the generic config (`config/recorder.toml`),
which grew ~2.4 GB/day compressed and would have filled the 94 GB disk in ~55 days: channels
without a retention rule are kept forever, and the `max_total_gb` valve only prunes channels
that have a rule. The forward config therefore sets 30-day retention on Polymarket
`price_change` and Kalshi `trades` (starting values, re-measure growth and adjust). The unit
stops with SIGINT so the open hour is flushed.

## Notes
- Data stays on the VM (`data/raw`, gitignored); the recorder's `max_total_gb` safety valve
  applies. Size the disk accordingly (the laptop's 610 MB came from a few hours of sessions).
- The ledger (forward-test evidence) goes to the private repo `arorarohan/drc-ledger`, not
  this one. Never commit market data or secrets.
- A deploy key or fine-grained token for pushing the ledger from the VM is a Slice 1 task.
- No inbound ports are opened; reading results from outside comes with the web app phase.

## Health check: `drc-status` (issue #31)
One read-only command answers "is everything still running?":
```
uv run drc-status                 # the VM, over ssh: short summary, problems first
uv run drc-status --json          # the same as JSON (for scripts / a future dashboard)
uv run drc-status --local         # this laptop: Drive-auth age, disk, live Drive check
uv run drc-status --local --verify rohan-laptop   # also hash-check local data vs Drive (slow)
```
Exit code: **0** all ok, **1** a warning, **2** an error (stale, failing, or VM unreachable),
**64** bad config. Thresholds are in `config/status.toml`, each with a comment saying why.

The ssh target defaults to the alias `drc-vm`; add it once to `~/.ssh/config` on the laptop
(the IP stays out of the repo):
```
Host drc-vm
    HostName <public ip>
    User ubuntu
    IdentityFile ~/.ssh/id_ed25519_drc
```
or pass `--ssh ubuntu@<ip> --ssh-key ~/.ssh/id_ed25519_drc` (or set `DRC_VM_SSH`/`DRC_VM_KEY`).
ssh runs with `BatchMode=yes` and a 10 s connect timeout, so it never prompts or hangs; an
unreachable VM is one `ERROR reachable ... UNREACHABLE` line, not a traceback.

What it checks on the VM (ages use the VM's clock, not the laptop's; see `docs/gotchas.md`):
| check | source | warn / error |
|---|---|---|
| recorder service | `systemctl show drc-record` | crash restarts / not active |
| raw freshness | newest market-data file per venue in `data/raw_forward` | 10 / 30 min old |
| feed gaps (24h) | `_meta` disconnect, shutdown, connect, poll_errors, throttled events | 10 / 50 gaps a day, any gap 120 s; 20 / 200 Kalshi poll errors |
| 1013 closes | `journalctl -u drc-record`, `code=1013`, per UTC day for 7 days and since the recorder started | 3 / 20 in 24 h |
| sync timer | `systemctl show drc-sync.timer` (also shows a cycle in progress) | - / not active+enabled |
| last sync | last `=== cycle ok` in `journalctl -u drc-sync`; a later failure or `AUTH EXPIRED` | 2.5 / 6 h old |
| archive verify | the latest `match= missing= differ= error=` lines of the sync log | - / anything missing, differing or errored |
| drive auth | `~/.config/rclone/drc-authorized-at` | day 5 / day 7 (expired) |
| disk | `df`, plus `data/raw_forward` against the recorder's `max_total_gb` | 80% or raw at 85% of the cap / 90% |

Strictly read-only: on the VM it only runs `systemctl show`, `journalctl`, `find`, `cat`, `zstdcat`, `df`,
`du` and `date` (a test enforces this). The live Drive check (`rclone about`) is opt-in on the VM
(`--probe-drive`) because rclone may refresh its token and rewrite `rclone.conf` while doing it;
it is on by default for `--local`.
