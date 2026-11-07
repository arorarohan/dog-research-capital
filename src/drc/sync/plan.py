"""What to upload, what is verified, and what expires: pure logic, no network.

Remote layout (one subtree per machine so two recorders never overwrite each other):
    <remote>/<host>/raw/<root>/<venue>/<channel>/<date>/<HH>.jsonl.zst   copy-only, immutable
    <remote>/<host>/sample/<root>/...                                    one hour a week, forever
    <remote>/<host>/norm/<root>/<table>/venue=V/date=D/hour=HH.parquet   overwritten if rebuilt
    <remote>/<host>/hist/...  <remote>/<host>/runs/...

Only closed-hour files are ever uploaded: the recorder writes the open hour as plain `.jsonl`
and compresses it to `.jsonl.zst` once the hour has closed, so a `.zst` file is final.
"""

from __future__ import annotations

import calendar
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from drc.normalize.parsers import PARSERS
from drc.recorder.writer import META_CHANNEL

RAW_ROOTS = ("raw", "raw_forward", "raw_weather")
NORM_ROOTS = ("norm", "norm_forward")
NORM_FOR_RAW = {"raw": "norm", "raw_forward": "norm_forward"}  # raw_weather has no normalizer
NORM_TABLES = ("trades", "book_events")
PARSED_CHANNELS = frozenset(PARSERS)  # (venue, channel) pairs the normalizer consumes
DAY = 86400
IDLE_SECS = 900  # a plain file untouched this long is finished (nobody is writing to it)


@dataclass(frozen=True)
class RawFile:
    root: str
    venue: str
    channel: str
    date: str
    hour: int
    size: int
    host: str = ""  # only known for files listed from the archive

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


def parse_raw_rel(root: str, rel: str, size: int = 0, host: str = "") -> RawFile | None:
    parts = rel.split("/")
    if len(parts) != 4 or not parts[3].endswith(".jsonl.zst"):
        return None
    try:
        hour = int(parts[3].split(".")[0])
        time.strptime(parts[2], "%Y-%m-%d")
    except ValueError:
        return None
    return RawFile(root, parts[0], parts[1], parts[2], hour, size, host)


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
    """Hourly normalized Parquet and its empty markers (`hour=HH.empty`, see normalize/pipeline).
    The per-venue 1s bar files are derived and rewritten constantly, so they are not archived."""
    base = data / root
    if not base.is_dir():
        return []
    return sorted(
        f.relative_to(base).as_posix()
        for pat in ("hour=*.parquet", "hour=*.empty")
        for f in base.rglob(pat)
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


def sample_files(
    files: list[RawFile], sample_hour: int, sample_weekday: int | None = None
) -> list[RawFile]:
    """One hour of every raw channel, on one weekday if given (0 = Monday), kept forever."""
    return [
        f
        for f in files
        if f.hour == sample_hour
        and not f.exempt
        and (sample_weekday is None or time.strptime(f.date, "%Y-%m-%d").tm_wday == sample_weekday)
    ]


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
    blocked: list[RawFile] = field(default_factory=list)  # would expire, but not normalized

    @property
    def cap_met(self) -> bool:
        return self.kept_bytes <= self.cap_bytes


def plan_expiry(
    files: list[RawFile],
    now: float,
    max_age_days: float,
    cap_bytes: int,
    allowed: Callable[[RawFile], bool] = lambda f: True,
) -> Expiry:
    """Which raw files to delete from the archive.

    1. Anything whose day ended more than `max_age_days` ago.
    2. If what remains still exceeds `cap_bytes`, the oldest days go first until it fits.
    Exempt channels (catalogs, metadata) never expire, but they do count toward the total.
    `allowed(f)` is the safety guard (#27): a file it rejects is never deleted by either rule and
    is listed in `blocked`; if that keeps the archive over the cap, `cap_met` is False.
    """
    aged = [f for f in files if not f.exempt and older_than(f, max_age_days, now)]
    by_age = [f for f in aged if allowed(f)]
    blocked = {f for f in aged if not allowed(f)}
    gone = set(by_age)
    left = [f for f in files if f not in gone]
    total = sum(f.size for f in left)
    by_cap: list[RawFile] = []
    if total > cap_bytes:
        for f in sorted((f for f in left if not f.exempt), key=lambda f: (f.date, f.hour)):
            if total <= cap_bytes:
                break
            if not allowed(f):
                blocked.add(f)
                continue
            by_cap.append(f)
            total -= f.size
    return Expiry(
        by_age, by_cap, sum(f.size for f in files), total, cap_bytes,
        sorted(blocked, key=lambda f: (f.host, f.root, f.venue, f.date, f.hour, f.channel)),
    )  # fmt: skip


NormKey = tuple[str, str, str, str, int, str]  # host, norm root, venue, date, hour, table


def parse_norm_listing(lsjson_text: str) -> set[NormKey]:
    """Archive listing -> which (host, norm root, venue, date, hour, table) outputs exist, as
    Parquet or as an empty marker. Paths look like
    `<host>/norm/<root>/<table>/venue=V/date=D/hour=HH.parquet` (or `.empty`)."""
    out: set[NormKey] = set()
    for item in json.loads(lsjson_text or "[]"):
        parts = item["Path"].split("/")
        if len(parts) != 7 or parts[1] != "norm" or parts[3] not in NORM_TABLES:
            continue
        name, _, ext = parts[6].rpartition(".")
        if ext not in ("parquet", "empty") or not name.startswith("hour="):
            continue
        if not (parts[4].startswith("venue=") and parts[5].startswith("date=")):
            continue
        try:
            hour = int(name[5:])
        except ValueError:
            continue
        out.add((parts[0], parts[2], parts[4][6:], parts[5][5:], hour, parts[3]))
    return out


def is_normalized(f: RawFile, norm: set[NormKey]) -> bool:
    """May this raw file be deleted from the archive? Only if every normalized table for its
    (host, venue, date, hour) is on the archive as Parquet or as an explicit empty marker.
    Channels the normalizer does not read, and raw roots it does not cover (weather), have no
    Parquet counterpart to wait for, so the policy alone decides for them."""
    norm_root = NORM_FOR_RAW.get(f.root)
    if norm_root is None or (f.venue, f.channel) not in PARSED_CHANNELS:
        return True
    return all((f.host, norm_root, f.venue, f.date, f.hour, t) in norm for t in NORM_TABLES)


def pull_filters(
    kind: str, venue: str | None = None, date: str | None = None, hour: int | None = None
) -> list[str]:
    """rclone `--include` patterns, relative to one raw or norm root, that select one venue
    and/or day and/or hour. No selector means no filter (the whole root).

    A raw selection also takes the venue's whole `_meta` channel: the normalizer derives
    connection epochs from every connect/subscribe event of the venue, not just the hour's, so an
    hour normalized without the earlier `_meta` files gets different `epoch` values."""
    if venue is None and date is None and hour is None:
        return []
    v, d = venue or "*", date or "*"
    h = f"{hour:02d}" if hour is not None else "*"
    if kind == "raw":
        return [f"/{v}/*/{d}/{h}.jsonl.zst", f"/{v}/{META_CHANNEL}/**"]
    if kind == "norm":
        return [f"/*/venue={v}/date={d}/hour={h}.*"]
    raise ValueError(f"cannot select by venue/date/hour in {kind!r}")


def parse_remote_listing(lsjson_text: str) -> list[tuple[str, RawFile]]:
    """`rclone lsjson -R --files-only` of the archive root -> (path, RawFile) for raw files.
    Paths look like `<host>/raw/<root>/<venue>/<channel>/<date>/<HH>.jsonl.zst`."""
    out = []
    for item in json.loads(lsjson_text or "[]"):
        path = item["Path"]
        parts = path.split("/")
        if len(parts) < 7 or parts[1] != "raw":
            continue
        rf = parse_raw_rel(parts[2], "/".join(parts[3:]), int(item.get("Size", 0)), parts[0])
        if rf:
            out.append((path, rf))
    return out
