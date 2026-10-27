"""What to upload, what is verified, and what expires: pure logic, no network.

Remote layout (one subtree per machine so two recorders never overwrite each other):
    <remote>/<host>/raw/<root>/<venue>/<channel>/<date>/<HH>.jsonl.zst   copy-only, immutable
    <remote>/<host>/sample/<root>/...                                    one hour a day, forever
    <remote>/<host>/norm/<root>/<table>/venue=V/date=D/hour=HH.parquet   overwritten if rebuilt
    <remote>/<host>/hist/...  <remote>/<host>/runs/...

Only closed-hour files are ever uploaded: the recorder writes the open hour as plain `.jsonl`
and compresses it to `.jsonl.zst` once the hour has closed, so a `.zst` file is final.
"""

from __future__ import annotations

import calendar
import json
import time
from dataclasses import dataclass
from pathlib import Path

RAW_ROOTS = ("raw", "raw_forward", "raw_weather")
NORM_ROOTS = ("norm", "norm_forward")
DAY = 86400


@dataclass(frozen=True)
class RawFile:
    root: str
    venue: str
    channel: str
    date: str
    hour: int
    size: int

    @property
    def rel(self) -> str:
        return f"{self.venue}/{self.channel}/{self.date}/{self.hour:02d}.jsonl.zst"

    @property
    def exempt(self) -> bool:
        """Catalogs and metadata (`_catalog`, `_meta`) are tiny, point-in-time and never expire."""
        return self.channel.startswith("_")

    @property
    def day_end(self) -> int:
        return calendar.timegm(time.strptime(self.date, "%Y-%m-%d")) + DAY


def parse_raw_rel(root: str, rel: str, size: int = 0) -> RawFile | None:
    parts = rel.split("/")
    if len(parts) != 4 or not parts[3].endswith(".jsonl.zst"):
        return None
    try:
        hour = int(parts[3].split(".")[0])
        time.strptime(parts[2], "%Y-%m-%d")
    except ValueError:
        return None
    return RawFile(root, parts[0], parts[1], parts[2], hour, size)


def local_raw_files(data: Path) -> list[RawFile]:
    out = []
    for root in RAW_ROOTS:
        base = data / root
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*.jsonl.zst")):
            rf = parse_raw_rel(root, f.relative_to(base).as_posix(), f.stat().st_size)
            if rf:
                out.append(rf)
    return out


def local_norm_rels(data: Path, root: str) -> list[str]:
    """Hourly normalized Parquet only. The per-venue 1s bar files are derived and rewritten
    constantly, so they are not archived."""
    base = data / root
    if not base.is_dir():
        return []
    return sorted(
        f.relative_to(base).as_posix()
        for f in base.rglob("hour=*.parquet")
        if "bars_1s" not in f.relative_to(base).parts
    )


def local_tree_rels(base: Path) -> list[str]:
    if not base.is_dir():
        return []
    return sorted(
        f.relative_to(base).as_posix()
        for f in base.rglob("*")
        if f.is_file() and not any(p.startswith("_old") for p in f.relative_to(base).parts)
    )


def sample_files(files: list[RawFile], sample_hour: int) -> list[RawFile]:
    return [f for f in files if f.hour == sample_hour and not f.exempt]


def older_than(f: RawFile, days: float, now: float) -> bool:
    """True once the whole UTC day of this file ended more than `days` ago."""
    return (now - f.day_end) / DAY > days


@dataclass
class Expiry:
    by_age: list[RawFile]
    by_cap: list[RawFile]
    total_bytes: int
    kept_bytes: int
    cap_bytes: int


def plan_expiry(files: list[RawFile], now: float, max_age_days: float, cap_bytes: int) -> Expiry:
    """Which raw files to delete from the archive.

    1. Anything whose day ended more than `max_age_days` ago.
    2. If what remains still exceeds `cap_bytes`, the oldest days go first until it fits.
    Exempt channels (catalogs, metadata) never expire, but they do count toward the total.
    """
    by_age = [f for f in files if not f.exempt and older_than(f, max_age_days, now)]
    gone = set(by_age)
    left = [f for f in files if f not in gone]
    total = sum(f.size for f in left)
    by_cap: list[RawFile] = []
    if total > cap_bytes:
        for f in sorted((f for f in left if not f.exempt), key=lambda f: (f.date, f.hour)):
            if total <= cap_bytes:
                break
            by_cap.append(f)
            total -= f.size
    return Expiry(by_age, by_cap, sum(f.size for f in files), total, cap_bytes)


def parse_remote_listing(lsjson_text: str) -> list[tuple[str, RawFile]]:
    """`rclone lsjson -R --files-only` of the archive root -> (path, RawFile) for raw files.
    Paths look like `<host>/raw/<root>/<venue>/<channel>/<date>/<HH>.jsonl.zst`."""
    out = []
    for item in json.loads(lsjson_text or "[]"):
        path = item["Path"]
        parts = path.split("/")
        if len(parts) < 7 or parts[1] != "raw":
            continue
        rf = parse_raw_rel(parts[2], "/".join(parts[3:]), int(item.get("Size", 0)))
        if rf:
            out.append((path, rf))
    return out
