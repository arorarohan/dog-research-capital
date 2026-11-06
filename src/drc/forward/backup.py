"""`drc-ledger-backup`: copy the forward-test event log off the machine, and restore it.

The ledger cannot be re-downloaded (unlike market data), so every session ends with a backup:

1. **Snapshot.** SQLite's online backup API (what `sqlite3 .backup` uses) copies the committed
   state of the live file, even while a session holds it open. A raw `cp` could catch a
   half-written page. The snapshot is integrity-checked and its hash chain verified; a broken
   chain is never backed up over a good one.
2. **Git (private repo `arorarohan/drc-ledger`).** The snapshot is exported as `events.jsonl`
   (one row per line, every column verbatim, the payload kept as the exact hashed string) plus
   `manifest.json` (count, head hash, file hashes), committed as a dated commit and pushed to
   that repo's `main`. Text, not the binary DB: each day only appends lines, so git stores a
   small delta instead of a new copy of a growing binary file.
   Rohan's Q8 decision: the clone lives at `data/ledger/` (a nested repo, gitignored by this
   one); the live `events.sqlite` next to it is ignored by that repo.
3. **Drive.** The binary snapshot and manifest go to `gdrive:drc/ledger/<host>/<UTC stamp>/`
   with rclone `--immutable`, then `rclone check`, the same copy-only rules as `drc-sync`.

**Append-only guard.** `verify()` cannot see rows deleted from the END of the chain. The backup
can: if the new snapshot has fewer events than the last committed manifest, or its row at the
old head no longer has the old head hash, it refuses and nothing is committed or uploaded.

Restore: `drc-ledger-backup restore --from data/ledger --to /tmp/restored.sqlite` (or
`--from <snapshot>.sqlite`); it rebuilds the DB, verifies the chain and checks it against the
manifest. See docs/deploy.md "Ledger backup and restore".
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import shlex
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import orjson

from drc.forward.eventlog import EventLog, VerifyResult
from drc.sync.rclone import AuthError, Rclone, RcloneError

DEFAULT_LEDGER = Path("data/ledger/events.sqlite")
DEFAULT_REPO = Path("data/ledger")
DEFAULT_REMOTE = "gdrive:drc"
COLUMNS = ("seq", "ts_event", "ts_recorded", "fund_id", "session_id", "type", "payload",
           "prev_hash", "hash")  # fmt: skip
JSONL, MANIFEST = "events.jsonl", "manifest.json"
REPO_GITIGNORE = "# the live database; the repo keeps the text export\n*.sqlite\n*.sqlite-*\n"
DEFAULT_PUSH = "git push origin HEAD:main"

Runner = Callable[..., subprocess.CompletedProcess]


class BackupError(RuntimeError):
    pass


@dataclass
class BackupResult:
    n_events: int
    head_hash: str
    committed: bool = False  # False when nothing changed since the last backup
    pushed: bool = False
    drive_dest: str = ""


# ---- snapshot, export, manifest ----------------------------------------------------------
def snapshot(src: Path, dest: Path) -> None:
    """Consistent copy of a possibly-open SQLite file via the online backup API."""
    if not src.exists():
        raise BackupError(f"no ledger at {src}")
    s = sqlite3.connect(f"file:{src.resolve()}?mode=ro", uri=True)
    d = sqlite3.connect(dest)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()
    chk = sqlite3.connect(dest)
    try:
        (res,) = chk.execute("PRAGMA integrity_check").fetchone()
    finally:
        chk.close()
    if res != "ok":
        raise BackupError(f"snapshot failed SQLite integrity_check: {res}")


def verify_file(path: Path) -> VerifyResult:
    log = EventLog(path)
    try:
        return log.verify()
    finally:
        log.close()


def export_jsonl(db: Path, out: Path) -> tuple[int, int, str]:
    """Write every row verbatim; returns (n_events, head_seq, head_hash)."""
    con = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    n, head = 0, (0, "")
    try:
        with out.open("wb") as f:
            for row in con.execute(f"SELECT {', '.join(COLUMNS)} FROM events ORDER BY seq"):
                f.write(orjson.dumps(dict(zip(COLUMNS, row, strict=True))) + b"\n")
                n, head = n + 1, (row[0], row[-1])
    finally:
        con.close()
    return n, head[0], head[1]


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_append_only(db: Path, previous: dict) -> None:
    """The new snapshot must extend the last backed-up chain, never shorten or rewrite it."""
    if not previous:
        return
    con = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    try:
        (n,) = con.execute("SELECT COUNT(*) FROM events").fetchone()
        row = con.execute("SELECT hash FROM events WHERE seq = ?", (previous["head_seq"],))
        got = row.fetchone()
    finally:
        con.close()
    if n < previous["n_events"]:
        raise BackupError(
            f"ledger has {n} events but the last backup had {previous['n_events']}: rows were "
            "lost or deleted. Refusing to back up; restore from the backup and investigate."
        )
    if previous["head_seq"] and (got is None or got[0] != previous["head_hash"]):
        raise BackupError(
            f"event {previous['head_seq']} no longer has the hash the last backup recorded: "
            "history was rewritten. Refusing to back up."
        )


# ---- the backup run ----------------------------------------------------------------------
def _git(run: Runner, repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    ident = ["-c", "user.name=drc-ledger-backup", "-c", "user.email=drc-ledger-backup@localhost"]
    p = run(["git", "-C", str(repo), *ident, *args], capture_output=True, text=True)
    if check and p.returncode != 0:
        raise BackupError(f"git {args[0]} failed (exit {p.returncode}): {p.stderr.strip()}")
    return p


def backup(
    ledger: Path,
    repo: Path | None,
    *,
    rclone: Rclone | None,
    remote: str = DEFAULT_REMOTE,
    host: str = "",
    push_cmd: str = DEFAULT_PUSH,
    run: Runner = subprocess.run,
    now: dt.datetime | None = None,
) -> BackupResult:
    """Snapshot -> verify -> append-only check -> git commit + push -> Drive copy + check.
    `repo=None` skips git, `rclone=None` skips Drive. Raises BackupError / RcloneError."""
    now = now or dt.datetime.now(dt.UTC)
    stamp = now.strftime("%Y-%m-%dT%H%M%SZ")
    if repo is not None and not (repo / ".git").exists():
        raise BackupError(
            f"{repo} is not a git clone of the ledger repo; one-time setup in docs/deploy.md "
            "('Ledger backup and restore')"
        )
    with tempfile.TemporaryDirectory(prefix="drc-ledger-") as td:
        work = Path(td) / stamp
        work.mkdir()
        snap = work / "events.sqlite"
        snapshot(ledger, snap)
        v = verify_file(snap)
        if not v.ok:
            raise BackupError(
                f"hash chain BROKEN at event {v.bad_seq} ({v.reason}); refusing to back up a "
                "damaged ledger over the last good backup"
            )
        jsonl = Path(td) / JSONL
        n, head_seq, head_hash = export_jsonl(snap, jsonl)
        previous = {}
        if repo is not None and (repo / MANIFEST).exists():
            previous = orjson.loads((repo / MANIFEST).read_bytes())
        check_append_only(snap, previous)
        manifest = {
            "backup_utc": now.isoformat(),
            "host": host,
            "n_events": n,
            "head_seq": head_seq,
            "head_hash": head_hash,
            "jsonl_sha256": sha256_file(jsonl),
            "sqlite_sha256": sha256_file(snap),
            "source": str(ledger),
        }
        mbytes = orjson.dumps(manifest, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS) + b"\n"
        (work / MANIFEST).write_bytes(mbytes)
        res = BackupResult(n, head_hash)

        if repo is not None:
            if previous.get("head_hash") == head_hash and (repo / JSONL).exists():
                pass  # nothing new since the last backup: no empty commit
            else:
                (repo / JSONL).write_bytes(jsonl.read_bytes())
                (repo / MANIFEST).write_bytes(mbytes)
                gi = repo / ".gitignore"
                if not gi.exists():
                    gi.write_text(REPO_GITIGNORE)
                _git(run, repo, "add", "--", JSONL, MANIFEST, ".gitignore")
                msg = (
                    f"ledger backup {now:%Y-%m-%d %H:%M}Z {host}: {n} events, head {head_hash[:12]}"
                )
                _git(run, repo, "commit", "-q", "-m", msg)
                res.committed = True
            # push every time: a push that failed yesterday goes out today
            p = run(shlex.split(push_cmd), cwd=repo, capture_output=True, text=True)
            if p.returncode != 0:
                raise BackupError(f"push failed (exit {p.returncode}): {p.stderr.strip()}")
            res.pushed = True

        if rclone is not None:
            dest = f"{remote.rstrip('/')}/ledger/{host or 'unknown-host'}/{stamp}"
            rclone.run("copy", work, dest, "--immutable")
            rclone.run("check", work, dest, "--one-way")
            res.drive_dest = dest
    return res


# ---- restore -----------------------------------------------------------------------------
@dataclass
class RestoreResult:
    path: Path
    verify: VerifyResult
    manifest_ok: bool | None  # None: no manifest to compare against
    problems: list[str]


def restore(src: Path, dest: Path, manifest: Path | None = None) -> RestoreResult:
    """Rebuild a ledger from a backup (a repo dir / events.jsonl, or a snapshot .sqlite),
    verify its chain and compare it with the manifest. Never overwrites an existing file."""
    if dest.exists():
        raise BackupError(f"{dest} exists; restore only into a new path")
    if src.is_dir():
        manifest = manifest or (src / MANIFEST if (src / MANIFEST).exists() else None)
        src = src / JSONL if (src / JSONL).exists() else src / "events.sqlite"
    elif manifest is None and (src.parent / MANIFEST).exists():
        manifest = src.parent / MANIFEST
    if not src.exists():
        raise BackupError(f"no backup at {src}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.suffix == ".sqlite":
        snapshot(src, dest)
    else:
        EventLog(dest).close()  # schema + append-only triggers
        con = sqlite3.connect(dest, isolation_level=None)
        try:
            con.execute("BEGIN")
            with src.open("rb") as f:
                for line in f:
                    if line.strip():
                        r = orjson.loads(line)
                        con.execute(
                            "INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?)",
                            tuple(r[c] for c in COLUMNS),
                        )
            con.execute("COMMIT")
        finally:
            con.close()
    v = verify_file(dest)
    problems: list[str] = []
    mok = None
    if manifest is not None:
        m = orjson.loads(manifest.read_bytes())
        con = sqlite3.connect(dest)
        try:
            row = con.execute("SELECT COUNT(*), MAX(seq) FROM events").fetchone()
            head = con.execute("SELECT hash FROM events WHERE seq = ?", (row[1],)).fetchone()
        finally:
            con.close()
        if row[0] != m["n_events"]:
            problems.append(f"{row[0]} events, manifest says {m['n_events']}")
        if (head[0] if head else "") != m["head_hash"]:
            problems.append("head hash differs from the manifest")
        mok = not problems
    if not v.ok:
        problems.insert(0, f"hash chain BROKEN at event {v.bad_seq}: {v.reason}")
    return RestoreResult(dest, v, mok, problems)


# ---- CLI ---------------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="drc-ledger-backup", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backup", help="snapshot, commit + push to drc-ledger, copy to Drive")
    b.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    b.add_argument("--repo", type=Path, default=DEFAULT_REPO, help="clone of drc-ledger")
    b.add_argument("--remote", default=DEFAULT_REMOTE, help="rclone remote:folder")
    b.add_argument("--host", default=os.environ.get("DRC_HOST", ""))
    b.add_argument("--push-cmd", default=os.environ.get("DRC_LEDGER_PUSH", DEFAULT_PUSH))
    b.add_argument("--no-git", action="store_true")
    b.add_argument("--no-drive", action="store_true")
    r = sub.add_parser("restore", help="rebuild a ledger from a backup and verify it")
    r.add_argument("--from", dest="src", type=Path, required=True,
                   help="drc-ledger clone, events.jsonl, or a snapshot events.sqlite")  # fmt: skip
    r.add_argument("--to", type=Path, required=True, help="new file to create")
    r.add_argument("--manifest", type=Path)
    args = ap.parse_args(argv)

    if args.cmd == "restore":
        try:
            rr = restore(args.src, args.to, args.manifest)
        except BackupError as e:
            print(f"drc-ledger-backup: {e}", file=sys.stderr)
            return 2
        chain = "verified" if rr.verify.ok else "BROKEN"
        man = {None: "no manifest", True: "matches manifest", False: "DOES NOT match manifest"}
        print(f"restored {rr.verify.n_events} events to {rr.path}: chain {chain}, "
              f"{man[rr.manifest_ok]}")  # fmt: skip
        for p in rr.problems:
            print(f"  {p}", file=sys.stderr)
        return 0 if not rr.problems else 1

    if not args.host and not args.no_drive:
        print("drc-ledger-backup: set DRC_HOST or --host (Drive path)", file=sys.stderr)
        return 2
    try:
        res = backup(
            args.ledger,
            None if args.no_git else args.repo,
            rclone=None if args.no_drive else Rclone(),
            remote=args.remote,
            host=args.host,
            push_cmd=args.push_cmd,
        )
    except AuthError as e:
        print(f"drc-ledger-backup: AUTH EXPIRED: {e}", file=sys.stderr)
        return 3
    except (BackupError, RcloneError) as e:
        print(f"drc-ledger-backup: {e}", file=sys.stderr)
        return 1
    git = (
        "skipped"
        if args.no_git
        else ("committed" if res.committed else "no change") + (", pushed" if res.pushed else "")
    )
    print(f"ledger backup: {res.n_events} events, head {res.head_hash[:12]}; git {git}; "
          f"drive {res.drive_dest or 'skipped'}")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
