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
- Size reference (measured 2026-10-27): Polymarket Parquet is ~1/3 of compressed raw, other
  venues ~0.8; zstd level 19 would save another 10-30%. Raw at 15 days is ~59 GB for the VM's
  forward config; Parquet forever would fill the Drive in under a year or two (revisit).
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
uv run drc-sync expire-remote         # dry run; --apply deletes expired archive raw
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
   (Google Cloud console: Drive API, OAuth consent screen set to *In production*, Desktop-app
   client) and set `client_id` / `client_secret` on the remote. Tokens are tied to the client, so
   re-authorize afterwards.

## Status (2026-10-27 end of day)
Done: `drc-sync` built and tested (198 tests); rclone v1.75.2 installed on Rohan's laptop;
remote `gdrive` configured (full `drive` scope, using rclone's *shared* client ID); round trip and
`--immutable` refusal tested on the real Drive; stale plain `.jsonl` hours are compressed (with
hash verification) before upload; **initial laptop upload (host `rohan-laptop`) finished and
verified 2026-10-28**: raw 70, norm 20, hist 1,675, runs 4 files, all matching by size and hash,
zero missing or differing (`drc-sync status`: 0 local files not archived). The job spanned an
overnight laptop suspend, which did not affect the result.
Drive is 400 GiB (not 500 GB), ~382 GiB free before this upload.

Resuming is always safe: `DRC_HOST=rohan-laptop uv run drc-sync push` is idempotent, and
`drc-sync verify` should show `missing=0 differ=0` for every group.

### Outstanding (in order)
1. **Own Google OAuth client** (rclone's shared one is retired during 2026, so this is urgent).
   Rohan does the console clicks: new Cloud project, enable Drive API, OAuth consent screen
   External then publish *In production* (else tokens die after 7 days), Desktop-app client ID.
   Then set `client_id`/`client_secret` on the `gdrive` remote and re-authorize on the laptop;
   uploaded files stay as they are.
2. ~~Confirm the initial upload with `verify`~~ done 2026-10-28.
3. Parquet written at zstd level 19 in `normalize` (saves 10-30%).
4. VM: install rclone, copy the remote config (own client), systemd timer that normalizes
   closed hours, pushes, verifies, and runs `prune-local --apply`; check that `drc-normalize`
   skips the open hour and how long an hour takes on the ARM cores.
5. Expiry: first real `expire-remote` dry run once data older than 15 days exists.
6. Restore test: pull into a clean directory, run `drc-normalize`, compare row counts.
7. Docs for Chris (`docs/onboarding.md`): install rclone, authorize, `drc-sync pull`.
8. Parser verification (#20) is the priority behind all of this: raw expires after 15 days.
