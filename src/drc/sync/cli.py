"""`drc-sync`: copy-only archive of data/ to cloud storage through rclone.

    drc-sync push   [--what raw,norm,hist,runs] [--dry-run]
    drc-sync pull   --host H [--what ...] [--into data/remote/H]
    drc-sync verify [--what ...]             compare local files with the archive (size + hash)
    drc-sync status                          how many local files are not archived yet
    drc-sync prune-local [--apply]           delete local raw that the archive has VERIFIED
    drc-sync expire-remote [--apply]         delete expired raw from the archive (dry run first)

Nothing is ever deleted unless you pass --apply, and nothing is ever overwritten in raw:
raw uploads use rclone's --immutable (a changed file is an error, not a replacement).
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
import tomllib
from pathlib import Path

from drc.sync import plan
from drc.sync.rclone import Rclone, RcloneError

WHATS = ("raw", "norm", "hist", "runs")


def load_config(path: Path) -> dict:
    return tomllib.loads(path.read_text()) if path.exists() else {}


class Archive:
    """One machine's view of the archive."""

    def __init__(self, rc: Rclone, remote: str, host: str, data: Path, sample_hour: int):
        self.rc, self.remote, self.host, self.data = rc, remote.rstrip("/"), host, data
        self.sample_hour = sample_hour

    def dest(self, kind: str, root: str = "") -> str:
        return "/".join(p for p in (self.remote, self.host, kind, root) if p)

    # ---- the file groups we archive: (kind, local root dir, remote root, rels, immutable) ----
    def groups(self, what: tuple[str, ...]):
        data = self.data
        if "raw" in what:
            raw = plan.local_raw_files(data)
            for root in plan.RAW_ROOTS:
                mine = [f for f in raw if f.root == root]
                yield "raw", data / root, self.dest("raw", root), [f.rel for f in mine], True
                smp = plan.sample_files(mine, self.sample_hour)
                yield "sample", data / root, self.dest("sample", root), [f.rel for f in smp], True
        if "norm" in what:
            for root in plan.NORM_ROOTS:
                rels = plan.local_norm_rels(data, root)
                yield "norm", data / root, self.dest("norm", root), rels, False
        if "hist" in what:
            yield (
                "hist",
                data / "hist",
                self.dest("hist"),
                plan.local_tree_rels(data / "hist"),
                False,
            )
        if "runs" in what:
            yield (
                "runs",
                data / "runs",
                self.dest("runs"),
                plan.local_tree_rels(data / "runs"),
                False,
            )

    def push(self, what: tuple[str, ...], dry_run: bool) -> int:
        failures = 0
        for kind, src, dst, rels, immutable in self.groups(what):
            if not rels:
                continue
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as lst:
                lst.write("\n".join(rels) + "\n")
            args = [src, dst, "--files-from", lst.name, "--transfers", "4", "--checkers", "8"]
            if immutable:
                args.append("--immutable")
            if dry_run:
                args.append("--dry-run")
            try:
                self.rc.run("copy", *args)
                print(f"push {kind:6s} {src.name:12s} {len(rels):6d} files -> {dst}")
            except RcloneError as e:
                failures += 1
                print(f"FAILED push {kind} {src.name}: {e}", file=sys.stderr)
            finally:
                os.unlink(lst.name)
        return failures

    def check(self, src: Path, dst: str, rels: list[str]) -> dict[str, list[str]]:
        """rclone check one-way over an exact file list. Returns paths per outcome:
        match (=), missing on the archive (-), differs (*), error (!)."""
        out: dict[str, list[str]] = {"=": [], "-": [], "*": [], "!": []}
        if not rels:
            return out
        with tempfile.TemporaryDirectory() as tmp:
            lst, rep = Path(tmp) / "files.txt", Path(tmp) / "report.txt"
            lst.write_text("\n".join(rels) + "\n")
            self.rc.run(
                "check", src, dst, "--one-way", "--files-from", lst, "--combined", rep,
                ok_codes=(0, 1),
            )  # fmt: skip
            for line in rep.read_text().splitlines() if rep.exists() else []:
                sym, _, path = line.partition(" ")
                if sym in out:
                    out[sym].append(path)
        return out

    def verify(self, what: tuple[str, ...]) -> dict[str, dict[str, int]]:
        summary: dict[str, dict[str, int]] = {}
        for kind, src, dst, rels, _ in self.groups(what):
            if kind == "sample":
                continue
            res = self.check(src, dst, rels)
            s = summary.setdefault(kind, {"match": 0, "missing": 0, "differ": 0, "error": 0})
            s["match"] += len(res["="])
            s["missing"] += len(res["-"])
            s["differ"] += len(res["*"])
            s["error"] += len(res["!"])
        return summary

    def prune_local(self, older_than_days: float, apply: bool, now: float) -> tuple[int, int]:
        """Delete local raw older than N days, but only files the archive confirms (hash match).
        Returns (deleted_or_would_delete, kept_unverified)."""
        old = [
            f for f in plan.local_raw_files(self.data) if plan.older_than(f, older_than_days, now)
        ]
        deleted = kept = 0
        for root in plan.RAW_ROOTS:
            mine = {f.rel: f for f in old if f.root == root}
            res = self.check(self.data / root, self.dest("raw", root), sorted(mine))
            kept += len(mine) - len(res["="])
            for rel in res["="]:
                deleted += 1
                if apply:
                    (self.data / root / rel).unlink()
        return deleted, kept

    def list_raw(self) -> list[tuple[str, plan.RawFile]]:
        p = self.rc.run("lsjson", self.remote, "-R", "--files-only", "--include", "*/raw/**")
        return plan.parse_remote_listing(p.stdout)

    def expire_remote(self, max_age: float, cap_gb: float, apply: bool, now: float):
        listing = self.list_raw()
        ex = plan.plan_expiry([rf for _, rf in listing], now, max_age, int(cap_gb * 1e9))
        doomed = set(ex.by_age) | set(ex.by_cap)
        paths = [p for p, rf in listing if rf in doomed]
        if apply and paths:
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as lst:
                lst.write("\n".join(paths) + "\n")
            try:
                self.rc.run(
                    "delete", self.remote, "--files-from", lst.name, "--drive-use-trash=false"
                )
            finally:
                os.unlink(lst.name)
        return ex, len(paths)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="drc-sync", description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", default="config/sync.toml", type=Path)
    ap.add_argument("--data", default="data", type=Path)
    ap.add_argument("--remote", help="rclone remote:folder (default from config)")
    ap.add_argument("--host", default=os.environ.get("DRC_HOST"), help="this machine's name")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("push", "verify", "pull"):
        p = sub.add_parser(name)
        p.add_argument("--what", default=",".join(WHATS))
        if name == "push":
            p.add_argument("--dry-run", action="store_true")
        if name == "pull":
            p.add_argument("--into", type=Path)
    sub.add_parser("status")
    p = sub.add_parser("prune-local")
    p.add_argument("--apply", action="store_true")
    p = sub.add_parser("expire-remote")
    p.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    remote = args.remote or cfg.get("remote", {}).get("name", "gdrive:drc")
    ret = cfg.get("retention", {})
    if not args.host:
        ap.error("this machine needs a name: pass --host or set DRC_HOST (e.g. vm-london)")
    what = tuple(w for w in getattr(args, "what", ",".join(WHATS)).split(",") if w)
    bad = set(what) - set(WHATS)
    if bad:
        ap.error(f"unknown --what {sorted(bad)}; choose from {WHATS}")
    arc = Archive(Rclone(), remote, args.host, args.data, ret.get("sample_hour_utc", 12))
    now = time.time()

    try:
        if args.cmd == "push":
            return 1 if arc.push(what, args.dry_run) else 0
        if args.cmd == "verify":
            bad_total = 0
            for kind, s in arc.verify(what).items():
                print(f"{kind:6s} match={s['match']} missing={s['missing']} "
                      f"differ={s['differ']} error={s['error']}")  # fmt: skip
                bad_total += s["missing"] + s["differ"] + s["error"]
            return 1 if bad_total else 0
        if args.cmd == "status":
            s = arc.verify(WHATS)
            missing = sum(v["missing"] + v["differ"] for v in s.values())
            print(f"{missing} local files not (correctly) archived yet")
            return 1 if missing else 0
        if args.cmd == "pull":
            into = args.into or args.data / "remote" / args.host
            for kind in what:
                roots = {"raw": plan.RAW_ROOTS, "norm": plan.NORM_ROOTS}.get(kind, ("",))
                for root in roots:
                    arc.rc.run("copy", arc.dest(kind, root), into / kind / root)
            print(f"pulled {', '.join(what)} for host {args.host} into {into}")
            return 0
        if args.cmd == "prune-local":
            n, kept = arc.prune_local(ret.get("local_raw_days", 7), args.apply, now)
            verb = "deleted" if args.apply else "would delete"
            print(f"{verb} {n} verified local raw files; kept {kept} not yet verified")
            return 0
        if args.cmd == "expire-remote":
            ex, n = arc.expire_remote(
                ret.get("remote_raw_days", 15), ret.get("remote_raw_cap_gb", 300), args.apply, now
            )
            verb = "deleted" if args.apply else "would delete"
            print(
                f"archive raw total {ex.total_bytes / 1e9:.1f} GB (cap {ex.cap_bytes / 1e9:.0f} GB)"
            )
            print(f"{verb} {n} files: {len(ex.by_age)} past age limit, {len(ex.by_cap)} over cap; "
                  f"{ex.kept_bytes / 1e9:.1f} GB remains")  # fmt: skip
            if not args.apply:
                print("dry run: pass --apply to delete")
            return 0
    except RcloneError as e:
        print(e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
