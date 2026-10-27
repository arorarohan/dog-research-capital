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
- One hour a day (12:00 UTC) of every raw channel is also copied to `sample/` and kept forever, as
  parser regression fixtures.
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

## Not done yet
VM timer (normalize, push, verify, prune), restore test, Chris's pull instructions.
