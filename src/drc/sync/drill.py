"""Restore drill: prove the archive can rebuild what it claims to hold.

Pull one venue-hour of raw (plus the venue's `_meta`, which the normalizer needs for epochs) and
the archived Parquet for that hour into an empty directory, re-run the normalizer on the pulled
raw, and check each table is row-for-row identical to the archived one (polars frame equality,
not byte equality: the same rows can compress to different bytes). Read-only on the archive and
never touches `data/`.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl

from drc.normalize.pipeline import empty_marker, normalize_all, out_path
from drc.sync import plan

if TYPE_CHECKING:
    from drc.sync.cli import Archive


@dataclass
class TableCheck:
    table: str
    archived: str  # "parquet", "empty" (marker: no rows) or "missing"
    rebuilt: str
    archived_rows: int
    rebuilt_rows: int
    identical: bool
    detail: str = ""


def _load(norm: Path, table: str, venue: str, date: str, hour: int) -> tuple[str, pl.DataFrame]:
    p = out_path(norm, table, venue, date, hour)
    if p.exists():
        return "parquet", pl.read_parquet(p)
    if empty_marker(norm, table, venue, date, hour).exists():
        return "empty", pl.DataFrame()
    return "missing", pl.DataFrame()


def compare_table(
    archived: Path, rebuilt: Path, table: str, venue: str, date: str, hour: int
) -> TableCheck:
    ka, a = _load(archived, table, venue, date, hour)
    kb, b = _load(rebuilt, table, venue, date, hour)
    detail = ""
    if "missing" in (ka, kb):
        same = False
        detail = "output missing"
    elif ka == kb == "empty":
        same = True
    elif a.schema != b.schema:
        same = False
        detail = f"schema differs: archived {dict(a.schema)} rebuilt {dict(b.schema)}"
    else:
        same = a.equals(b)
        if not same and a.height == b.height:
            n = int((a.hash_rows() != b.hash_rows()).sum())
            detail = f"{n} of {a.height} rows differ"
        elif not same:
            detail = "row counts differ"
    return TableCheck(table, ka, kb, a.height, b.height, same, detail)


def _size(paths) -> tuple[int, int]:
    files = [p for p in paths if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


def run(
    arc: Archive,
    root: str,
    venue: str,
    date: str,
    hour: int,
    into: Path | None = None,
    keep: bool = False,
) -> int:
    """Run the drill and print a report. Exit code 0 if every table matches, 1 otherwise."""
    norm_root = plan.NORM_FOR_RAW.get(root)
    if norm_root is None:
        print(f"{root} has no normalized counterpart; nothing to compare", file=sys.stderr)
        return 1
    data = arc.data.resolve()
    if into is not None:
        work = into.resolve()
        if work == data or data in work.parents:
            print(f"refusing to run the drill inside the data directory ({work})", file=sys.stderr)
            return 1
        if work.exists() and any(work.iterdir()):
            print(f"{work} is not empty; the drill needs a fresh directory", file=sys.stderr)
            return 1
        work.mkdir(parents=True, exist_ok=True)
    else:
        work = Path(tempfile.mkdtemp(prefix="drc-restore-drill-"))

    t0 = time.monotonic()
    arc.pull(("raw", "norm"), work / "archive", root, venue, date, hour)
    t_pull = time.monotonic() - t0
    raw = work / "archive" / "raw" / root
    archived = work / "archive" / "norm" / norm_root
    rebuilt = work / "rebuilt" / norm_root

    hour_files = sorted((raw / venue).glob(f"*/{date}/{hour:02d}.jsonl.zst"))
    hour_files = [p for p in hour_files if not p.parent.parent.name.startswith("_")]
    if not hour_files:
        where = f"{arc.host}/{root}/{venue} {date} {hour:02d}:00"
        print(f"nothing pulled: no raw for {where} on the archive (expired, or never recorded?)",
              file=sys.stderr)  # fmt: skip
        if into is None:
            shutil.rmtree(work)
        return 1
    n_raw, b_raw = _size(hour_files)
    n_meta, b_meta = _size((raw / venue / "_meta").rglob("*"))
    n_norm, b_norm = _size(archived.rglob("*"))

    t1 = time.monotonic()
    normalize_all(raw, rebuilt, [venue])
    t_norm = time.monotonic() - t1
    checks = [compare_table(archived, rebuilt, t, venue, date, hour) for t in plan.NORM_TABLES]
    ok = all(c.identical for c in checks)

    print(f"restore drill {arc.host}/{root}/{venue} {date} {hour:02d}:00 UTC in {work}")
    print(f"  pulled raw  {n_raw} files {b_raw / 1e6:.2f} MB (+ _meta {n_meta} files "
          f"{b_meta / 1e6:.2f} MB); archived norm {n_norm} files {b_norm / 1e6:.2f} MB; "
          f"pull {t_pull:.1f}s")  # fmt: skip
    print(f"  re-normalized in {t_norm:.1f}s")
    for c in checks:
        verdict = "IDENTICAL" if c.identical else "DIFFERENT"
        print(f"  {c.table:12s} archived {c.archived}/{c.archived_rows} rows, rebuilt "
              f"{c.rebuilt}/{c.rebuilt_rows} rows: {verdict} {c.detail}".rstrip())  # fmt: skip
    print("PASS: the archive rebuilds this hour exactly" if ok else "FAIL: see above")
    if ok and not keep and into is None:
        shutil.rmtree(work)
    else:
        print(f"  work dir kept: {work}")
    return 0 if ok else 1
