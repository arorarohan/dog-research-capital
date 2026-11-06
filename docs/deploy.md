# Cloud VM deployment

Why: the forward test must run every day whether or not the laptop is awake
(`docs/design/forward-test-platform.md`, decision 2026-10-25). The recorder already runs unchanged
on Linux; the daily `drc-session` job joins it as a systemd timer (written, not yet enabled:
see "Daily forward-test session" below).

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
- Pushing the ledger from the VM uses a deploy key scoped to `drc-ledger` (setup below).

## Daily forward-test session (written 2026-11-06, NOT installed or enabled)
`deploy/systemd/drc-session.timer` fires at **12:00 UTC** daily (the charter's `time_utc`) and
starts `drc-session.service`, which runs `deploy/session-cycle.sh`:
1. `drc-session --fund <id>` for each fund in `DRC_FUNDS` (default `pm-sched-gap-v1`);
2. `drc-report --out data/reports` (a stable directory, overwritten each day, so a link to
   `data/reports/index.html` always shows the latest);
3. `drc-ledger-backup backup` (below).

Every step runs even if an earlier one failed, and the run is marked failed if any did.
`Persistent=true`: if the VM was down at 12:00 the job runs once at boot. A late run on the
same UTC day is a normal session (it logs `late_secs`); if a whole day passed, the next session
logs that day as `session_missed`. Missed days are never backfilled.

Before enabling (in this order):
1. Rohan reviews `funds/pm-sched-gap-v1.toml`, sets `status = "frozen"`, and it lands on `main`
   through a PR. `drc-session` refuses a draft, an untracked or a modified charter.
2. The VM's checkout is on `main` with that commit (`git switch main && git pull --ff-only &&
   uv sync`). The session logs the git sha it ran from.
3. One-time `drc-ledger` setup (below), and the Drive login is current (`docs/sync.md`).
4. Dry run on the VM: `uv run drc-session --fund pm-sched-gap-v1 --dry-run`.
5. Install and enable: re-run `bash deploy/setup-vm.sh` (installs the units, enables nothing),
   then `sudo systemctl enable --now drc-session.timer`. Check with `systemctl list-timers` and
   `journalctl -u drc-session`. Stop with `sudo systemctl disable --now drc-session.timer`
   (days it is off will show as missed: that is honest, not a bug).

## Ledger backup and restore
`data/ledger/events.sqlite` is the only copy of the forward-test evidence, so every cycle ends
with `uv run drc-ledger-backup backup`:
- **Snapshot** with SQLite's online backup API (what `sqlite3 .backup` uses), safe while the
  file is open; integrity-checked and hash-chain verified. A broken chain is never backed up.
- **Git.** `data/ledger/` is a clone of the private repo `arorarohan/drc-ledger` (Rohan's Q8
  decision). The backup writes `events.jsonl` (every row verbatim) and `manifest.json` (count,
  head hash, file hashes), commits `ledger backup <date> <host>: N events, head <hash>`, and
  pushes to that repo's `main`. The binary `events.sqlite` is gitignored there: daily copies of
  a growing binary would bloat the repo, while JSONL grows by appended lines only.
- **Append-only guard.** If the ledger has fewer events than the last backup, or the old head
  row's hash changed, the backup refuses (exit 1) and nothing is committed or uploaded. This is
  the check `verify()` cannot do alone (deleting rows from the end leaves a valid chain).
- **Drive.** The snapshot and manifest go to `gdrive:drc/ledger/<DRC_HOST>/<UTC stamp>/` with
  `rclone copy --immutable`, then `rclone check`. Exit 3 means the Drive login expired
  (`docs/sync.md`); the git half has already succeeded by then.
- Options: `--no-git`, `--no-drive`, `--push-cmd` (or `DRC_LEDGER_PUSH`), `--repo`, `--ledger`.

One-time setup on the VM (Rohan):
1. Create a deploy key for `drc-ledger` only, with write access: on the VM `ssh-keygen -t
   ed25519 -f ~/.ssh/drc_ledger -N ""`, add `~/.ssh/drc_ledger.pub` under the repo's Settings >
   Deploy keys with "Allow write access". It can push to that one repo and nothing else.
2. `~/.ssh/config` on the VM:
   ```
   Host github-drc-ledger
     HostName github.com
     IdentityFile ~/.ssh/drc_ledger
     IdentitiesOnly yes
   ```
3. Clone into the ledger directory **before the first real session** (the repo is empty):
   `git clone git@github-drc-ledger:arorarohan/drc-ledger.git data/ledger`. If `data/ledger/`
   already holds an `events.sqlite`, instead run `git init -b main data/ledger` and
   `git -C data/ledger remote add origin git@github-drc-ledger:arorarohan/drc-ledger.git`.
4. After the first session: `DRC_HOST=vm-london uv run drc-ledger-backup backup`, then check
   the commit on GitHub and the folder on Drive.

**Restore** (new machine, dead disk, or a drill):
```
git clone git@github.com:arorarohan/drc-ledger.git /tmp/drc-ledger
uv run drc-ledger-backup restore --from /tmp/drc-ledger --to /tmp/restored/events.sqlite
# or from Drive: rclone copy gdrive:drc/ledger/vm-london/<stamp> /tmp/snap
uv run drc-ledger-backup restore --from /tmp/snap/events.sqlite --to /tmp/restored2/events.sqlite
```
Restore never overwrites a file. It rebuilds the database, verifies the hash chain and compares
the event count and head hash with the manifest; exit 0 only if all agree. Then render
`uv run drc-report --ledger /tmp/restored/events.sqlite --out /tmp/r`, compare it with the live
report and only then move the restored file to `data/ledger/events.sqlite`. If git and Drive
disagree, use the longer valid chain, provided it extends the other.
- No inbound ports are opened; reading results from outside comes with the web app phase.
