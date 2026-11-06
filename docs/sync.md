# Cloud archive (drc-sync)

Source of truth for recordings and normalized data is Google Drive, reached through rclone
(issue #15). Local `data/` is a cache. Everything is **copy-only**: raw uploads use
`rclone --immutable` (a changed file is an error, never a replacement), and nothing is deleted
from the archive unless you run `expire-remote --apply`.

## Policy (decided by Rohan, 2026-10-27)
- Raw (`*.jsonl.zst`) stays on Drive **15 days**; if raw on Drive exceeds **300 GB**, the oldest
  days expire first. Catalogs and metadata (`_catalog`, `_meta`) are raw forever (tiny, and
  point-in-time so they cannot be re-fetched).
- Normalized Parquet is kept forever. Raw is **not** frozen behind findings: after expiry the
  Parquet is all there is, which is why parser correctness is a priority (issue #20).
- One hour a week (12:00 UTC on Mondays, `sample_weekday` in the config) of every raw channel is
  also copied to `sample/` and kept forever, as parser regression fixtures. Weekly rather than
  daily (decided 2026-10-27): daily would be about 58 GB a year of a 400 GiB Drive.
- Size reference (measured 2026-10-27, level re-measured 2026-11-04): Parquet is written at zstd
  level 19. Polymarket (the VM's dominant data) is now ~0.22 of compressed raw (was 0.33 at the
  default level; write time +40%, reads unchanged); Coinbase/Kraken/Kalshi stay near 0.8.
  Raw at 15 days is ~59 GB for the VM's forward config; Parquet at ~0.9 GB/day would take about a
  year to fill what is left of the Drive (~320 GB), so revisit before then.
- Local raw older than 7 days is pruned only after the archive confirms it by size and hash.
- Settings live in `config/sync.toml`.

## Layout on Drive
`gdrive:drc/<host>/{raw,sample,norm,hist,runs}/...` with one tree per machine (`vm-london`,
`rohan-laptop`) so two recorders writing the same relative paths never collide. The VM is the
canonical live recorder; the laptop's older recordings are a separate historical host.

## Commands (set `DRC_HOST` once per machine)
```
export DRC_HOST=rohan-laptop
uv run drc-sync push --dry-run        # what would upload
uv run drc-sync push                  # upload (closed hours only)
uv run drc-sync status                # local files not yet archived
uv run drc-sync verify                # size + hash check against the archive
uv run drc-sync prune-local           # dry run; --apply deletes verified local raw >7 days old
uv run drc-sync expire-remote         # dry run; --apply deletes expired archive raw that is normalized
uv run drc-sync pull --host vm-london --what norm   # into data/remote/vm-london
```
Pulls land in a separate directory per host and never merge into your own `data/raw*`.

## Setup on a new machine
1. Install rclone (download the zip from downloads.rclone.org, verify the SHA256 in the
   version's `SHA256SUMS`, put `rclone` on PATH).
2. `rclone authorize drive --auth-no-open-browser`, open the printed link, allow access, and
   create the remote `gdrive` from the resulting token (`rclone config create gdrive drive
   scope=drive token=...`). The token lives in `~/.config/rclone/rclone.conf` (mode 600) and
   never in the repo.
3. **Own OAuth client ID.** rclone's shared client ID is being retired during 2026. Create our own
   (Google Cloud console: Drive API, OAuth consent screen, Desktop-app client) and set
   `client_id` / `client_secret` on the remote. Tokens are tied to the client, so re-authorize
   afterwards (`deploy/reauth-drive.sh`).

## OAuth app status: Testing (decided by Rohan, 2026-11-04)
Publishing the app to "In production" requires a homepage and a privacy policy on a domain we
have proven we own (Search Console + authorized domain). Rohan chose to stay in **Testing** for
now. Consequences:
- Google expires **refresh tokens 7 days** after each authorization, so Drive sync stops weekly
  until someone re-authorizes. `drc-sync` exits with code 3 and says `AUTH EXPIRED`, and warns
  from day 5 (it reads the date stamp `~/.config/rclone/drc-authorized-at`).
- Only listed **test users** can authorize (Rohan's Google account; add Chris's when he needs it).
- The cloud VM therefore cannot sync unattended for more than a week. Until we leave Testing, the
  laptop re-authorizes weekly and the token is copied to the VM, or the VM's sync waits.
- To leave Testing later: put a one-paragraph homepage and privacy page on Rohan's domain
  (GitHub Pages works), verify the domain in Search Console, add it under *Authorized domains* on
  the Branding page, and click *Publish app*. No re-creation of the client is needed.

### Re-authorizing (about weekly while in Testing)
```
bash deploy/reauth-drive.sh <client_id>   # prints a link; open it, Advanced > Go to drc-rclone > Allow
```
The client secret is read from `~/.config/rclone/client_secret.txt` (mode 600). Never paste it into
chat or commit it. Afterwards `drc-sync verify` confirms access.

## Status (2026-11-04)
Done: `drc-sync` built and tested (198 tests); rclone v1.75.2 installed on Rohan's laptop;
remote `gdrive` configured with full `drive` scope; round trip and
`--immutable` refusal tested on the real Drive; stale plain `.jsonl` hours are compressed (with
hash verification) before upload; **initial laptop upload (host `rohan-laptop`) finished and
verified 2026-10-28**: raw 70, norm 20, hist 1,675, runs 4 files, all matching by size and hash,
zero missing or differing (`drc-sync status`: 0 local files not archived). The job spanned an
overnight laptop suspend, which did not affect the result.
Drive is 400 GiB (not 500 GB), ~382 GiB free before this upload.

Resuming is always safe: `DRC_HOST=rohan-laptop uv run drc-sync push` is idempotent, and
`drc-sync verify` should show `missing=0 differ=0` for every group.

### Outstanding (in order)
1. ~~Own Google OAuth client~~ **done 2026-11-04.** Cloud project `drc-rclone` (Drive API on,
   External, Testing, Rohan as test user), Desktop-app client created, `gdrive` remote switched to
   it and re-verified (`drc-sync verify`: 1,769 files match). **The login expires every 7 days**
   (first expiry ~2026-11-11; `drc-sync` warns from day 5): run `deploy/reauth-drive.sh
   <client_id>` weekly until the app is published (see "OAuth app status"). The client ID is
   `732823808349-ln5j74i416a29ameu0i793bh16sqskec.apps.googleusercontent.com` (not secret); the
   secret lives only in `~/.config/rclone/client_secret.txt` (mode 600) and the Cloud console.
2. ~~Confirm the initial upload with `verify`~~ done 2026-10-28.
3. ~~Parquet at zstd level 19~~ done 2026-11-04 (Polymarket Parquet -32%).
4. **VM sync (running since 2026-11-05).** Done: rclone v1.75.2 (arm64) installed on the VM, the
   `gdrive` remote copied over (`~/.config/rclone/rclone.conf`, mode 600; expires with the weekly
   login, so after each `deploy/reauth-drive.sh` copy `rclone.conf` and `drc-authorized-at` to
   the VM with `scp`), the VM's repo checkout is on `infra/15-drive-sync` (plus local merges of
   the fix branches, disposable; go back to `main` once the PRs land), and the **initial
   upload of the VM's existing raw (`gdrive:drc/vm-london`) finished and verified 2026-11-04:
   1,668 raw files, 0 missing, 0 differing, 28.3 GB** (about 51 MB/s for the large files). Written
   but NOT yet enabled: `deploy/sync-cycle.sh` +
   `drc-sync.service/.timer` (hourly: normalize closed hours, push, verify, prune-local).
   To enable once the initial upload verifies: `sudo systemctl enable --now drc-sync.timer`.
   Weekly token expiry applies (see "OAuth app status").
   **Why this was urgent:** the VM's local cap (30 GB, now 50 GB) was deleting raw days nobody
   had backed up: `polymarket/price_change` is ~4.5 GB/day compressed, and days from Oct 27-29
   were already gone before any upload. See `docs/gotchas.md`.
   **First cycle, 2026-11-04:** `drc-normalize` was OOM-killed (11.7 GB peak) on polymarket
   2026-10-30 00:00, a 601 MB compressed hour; the cycle stopped without pruning, as designed, and
   the recorder was untouched. Fixed in PR #26 (issue #25): streaming normalizer, same hour now
   peaks at 3.5 GB in 190 s, output identical on 18.8M real rows. The backlog of ~140 big hours
   then ran as one supervised cycle (2h 48m, peak 3.6 GB; raw 1,695 and Parquet 716 files
   verified, 33 verified local raw files pruned). A second cycle, started through systemd
   itself (`drc-sync.service`) on 2026-11-05, succeeded in 36 minutes (60 new venue-hours;
   raw 1,793 and Parquet 784 files verified, 0 missing or differing; 175 verified local raw
   files pruned). **The hourly timer `drc-sync.timer` was enabled 2026-11-05 10:16 UTC** (next
   runs at :10 past each hour; survives reboot). Watch it with `systemctl list-timers` and
   `journalctl -u drc-sync`; stop it with `sudo systemctl disable --now drc-sync.timer`. It will
   start failing on or about 2026-11-11 when the Drive login expires (see "OAuth app status").
5. Expiry: **guard built (issue #27).** `expire-remote` deletes a raw file only if the archive
   also holds that hour's normalized output for the same host: for each table (`trades`,
   `book_events`) either `hour=HH.parquet` or an empty marker `hour=HH.empty` (the normalizer
   writes the marker when a table has no rows, so "no rows" is distinguishable from "failed").
   Otherwise the file is skipped and listed as `blocked: not normalized`, in the dry run and the
   real run alike; the cap rule skips blocked files too and, if that leaves the archive over the
   cap, prints a WARNING and exits 1. Channels the normalizer does not read (and `raw_weather`)
   are not guarded. Hours normalized before this change have no markers for tables that were
   empty, so they show as blocked until `drc-normalize` re-runs them (it does so by itself on the
   next cycle, since an hour with a missing output is never skipped). Still to do: the first real
   `expire-remote` dry run once data older than 15 days exists (about 2026-11-12).
6. Restore test: pull into a clean directory, run `drc-normalize`, compare row counts.
7. Docs for Chris (`docs/onboarding.md`): install rclone, authorize, `drc-sync pull`.
8. Parser verification (#20) is the priority behind all of this: raw expires after 15 days.
