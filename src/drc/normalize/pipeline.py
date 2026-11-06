"""Raw JSONL -> normalized Parquet, one (venue, date, hour) at a time.

Output layout:  <norm>/<table>/venue=<v>/date=<YYYY-MM-DD>/hour=<HH>.parquet
Tables: `trades`, `book_events` (see rows.py). Re-running an hour overwrites its output, so the
pipeline is idempotent; an hour is skipped only if its output is newer than every input.

`epoch` is the receive-timestamp (ns) of the latest `connect` event at or before a message. It
changes whenever the recorder reconnects, so it is a hard resync boundary for book replay and
never needs to be recomputed when new data arrives.
"""

from __future__ import annotations

import bisect
import calendar
import heapq
import logging
import time
from pathlib import Path

import polars as pl

from drc.normalize.accounting import HourStats, read_problems, sidecar_path, write_sidecar
from drc.normalize.fieldmap import IGNORED_CHANNELS, META_CHANNEL_NAME
from drc.normalize.parsers import PARSERS
from drc.normalize.rows import BOOK_EVENTS_SCHEMA, TRADES_SCHEMA, BookRow, Trade
from drc.recorder.writer import META_CHANNEL, read_lines

log = logging.getLogger(__name__)

MAX_EXAMPLES = 3

CHANNEL_ORDER = {key: i for i, key in enumerate(PARSERS)}

# zstd level for the Parquet we keep (and upload). Measured 2026-11-04 on Polymarket book
# events: level 3 (default) 54.9 MB, 15 -> 38.1 MB, 19 -> 37.0 MB, 22 -> 36.3 MB; write time
# 0.2 s -> 3.9 s per two hours of data; read time unchanged. Kraken books shrink only ~6%.
PARQUET_ZSTD_LEVEL = 19


def connect_times(raw_root: Path, venue: str) -> list[int]:
    out: list[int] = []
    for p in sorted((raw_root / venue / META_CHANNEL).rglob("*.jsonl*")):
        out += [r["t"] for r in read_lines(p) if r.get("ev") == "connect"]
    return sorted(out)


def _hour_files(raw_root: Path, venue: str, date: str, hour: int) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for (v, channel), _ in PARSERS.items():
        if v != venue:
            continue
        for ext in ("jsonl.zst", "jsonl"):
            p = raw_root / venue / channel / date / f"{hour:02d}.{ext}"
            if p.exists():
                found[channel] = p
                break
    return found


def _unread_channels(raw_root: Path, venue: str, date: str, hour: int) -> list[str]:
    """Raw channels with data for this hour that no parser reads and no one declared as ignored.
    Such data would otherwise vanish without a trace (e.g. a new Polymarket event type)."""
    out = []
    for d in sorted((raw_root / venue).glob("*")):
        ch = d.name
        if not d.is_dir() or ch == META_CHANNEL_NAME or (venue, ch) in PARSERS:
            continue
        if (venue, ch) in IGNORED_CHANNELS:
            continue
        if any((d / date / f"{hour:02d}.{ext}").exists() for ext in ("jsonl.zst", "jsonl")):
            out.append(ch)
    return out


def list_hours(raw_root: Path, venue: str) -> list[tuple[str, int]]:
    hours: set[tuple[str, int]] = set()
    for d in (raw_root / venue).glob("*"):
        if not d.is_dir() or d.name == META_CHANNEL_NAME:
            continue
        if (venue, d.name) not in PARSERS and (venue, d.name) in IGNORED_CHANNELS:
            continue  # declared ignorable: does not by itself make an hour worth normalizing
        for p in d.glob("*/*.jsonl*"):
            hours.add((p.parent.name, int(p.name[:2])))
    return sorted(hours)


def out_path(norm_root: Path, table: str, venue: str, date: str, hour: int) -> Path:
    return norm_root / table / f"venue={venue}" / f"date={date}" / f"hour={hour:02d}.parquet"


# Rows are accumulated in Python lists and turned into a polars chunk every CHUNK_ROWS, so peak
# memory is bounded by the chunk plus the finished chunks (columnar, far smaller than lists).
CHUNK_ROWS = 500_000


class _Unsorted(Exception):
    """A channel file is not in receive-time order, so a streaming merge would not equal a sort."""

    def __init__(self, channel: str, line_idx: int) -> None:
        super().__init__(f"{channel} file goes back in time at line {line_idx}")
        self.channel = channel


def _sort_key(rec: tuple) -> tuple:
    return rec[:3]  # (receive time, channel order, line index): unique, so the order is total


def _channel_stream(
    venue: str, channel: str, path: Path, strict: bool, bad: list[int] | None = None
):
    """Records of one channel file as (ts_recv, channel order, line index, channel, message).
    With `strict`, raise _Unsorted the moment receive time goes backwards."""
    order = CHANNEL_ORDER[(venue, channel)]
    prev = None
    for line_idx, rec in enumerate(read_lines(path, bad)):
        t = rec["t"]
        if strict and prev is not None and t < prev:
            raise _Unsorted(channel, line_idx)
        prev = t
        yield (t, order, line_idx, channel, rec["m"])


class _Table:
    """Column-wise row accumulator that spills to a polars chunk every `chunk_rows` rows."""

    def __init__(self, schema: dict, chunk_rows: int) -> None:
        self.schema, self.chunk_rows = schema, chunk_rows
        self.cols: dict[str, list] = {k: [] for k in schema}
        self.frames: list[pl.DataFrame] = []

    def maybe_flush(self) -> None:
        if len(self.cols["venue"]) >= self.chunk_rows:
            self.flush()

    def flush(self) -> None:
        if self.cols["venue"]:
            self.frames.append(pl.DataFrame(self.cols, schema=self.schema))
            self.cols = {k: [] for k in self.schema}

    def frame(self) -> pl.DataFrame:
        self.flush()
        if not self.frames:
            return pl.DataFrame({k: [] for k in self.schema}, schema=self.schema)
        return pl.concat(self.frames, rechunk=False)


def _build(
    records, venue: str, connects: list[int], stats: HourStats | None = None
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Parse receive-ordered records into the `trades` and `book_events` frames, tallying every
    message into `stats` (parsed / empty / unparseable)."""
    trades, book = _Table(TRADES_SCHEMA, CHUNK_ROWS), _Table(BOOK_EVENTS_SCHEMA, CHUNK_ROWS)
    stats = stats or HourStats()
    msg_id = 0
    for ts_recv, _, _, channel, msg in records:
        table, parse = PARSERS[(venue, channel)]
        cs = stats.channel(channel)
        cs.raw += 1
        i = bisect.bisect_right(connects, ts_recv)
        epoch = connects[i - 1] if i else 0
        try:
            rows = list(parse(msg))
        except (KeyError, ValueError, TypeError) as e:
            log.warning("%s/%s unparseable message at %d: %r", venue, channel, ts_recv, e)
            cs.unparseable += 1
            if len(cs.examples) < MAX_EXAMPLES:
                cs.examples.append(f"{e!r} in {str(msg)[:200]}")
            continue
        if not rows:
            cs.empty += 1
            continue
        cs.parsed += 1
        cs.rows += len(rows)
        msg_id += 1
        for row in rows:
            if isinstance(row, Trade):
                cols = trades.cols
                cols["trade_id"].append(row.trade_id)
                cols["replay"].append(row.replay)
                cols["price"].append(row.price)
                cols["size"].append(row.size)
                cols["side"].append(row.side)
            else:
                assert isinstance(row, BookRow) and table == "book"
                cols = book.cols
                cols["msg_id"].append(msg_id)
                cols["kind"].append(row.kind)
                cols["side"].append(row.side)
                cols["price"].append(row.price)
                cols["size"].append(row.size)
            cols["venue"].append(venue)
            cols["symbol"].append(row.symbol)
            cols["epoch"].append(epoch)
            cols["ts_recv"].append(ts_recv)
            cols["ts_exch"].append(row.ts_exch)
        trades.maybe_flush()
        book.maybe_flush()
    return trades.frame(), book.frame()


def normalize_hour(
    raw_root: Path, norm_root: Path, venue: str, date: str, hour: int, force: bool = False
) -> dict[str, int]:
    """Normalize one hour. Returns row counts per table (empty dict if skipped).

    Every raw message is tallied in an accounting sidecar (see `accounting.py`); unexplained
    loss is logged as an error here and raised by `normalize_all` once every hour is done."""
    files = _hour_files(raw_root, venue, date, hour)
    unread = _unread_channels(raw_root, venue, date, hour)
    if not files:
        if unread:  # only undeclared channels this hour: nothing to normalize, but say so
            stats = HourStats(unread_channels=unread)
            for problem in stats.problems():
                log.error("%s %s %02d: %s", venue, date, hour, problem)
            write_sidecar(norm_root, venue, date, hour, stats)
        return {}
    outs = [out_path(norm_root, t, venue, date, hour) for t in ("trades", "book_events")]
    newest_input = max(p.stat().st_mtime for p in files.values())
    # An hour with no accounting sidecar (normalized before #20) is redone once, so it gets one.
    done = all(o.exists() and o.stat().st_mtime >= newest_input for o in outs)
    if not force and done and sidecar_path(norm_root, venue, date, hour).exists():
        return {}

    connects = connect_times(raw_root, venue)
    # Each channel file is normally in receive-time order, so a k-way merge yields the same global
    # order as sorting everything while holding one record per channel. A file that is not (Kalshi
    # books carry explicit times) is materialised and sorted on its own; the others stay lazy.
    # Found on the fly: restart with that channel marked, so each channel costs one extra pass.
    in_memory: set[str] = set()
    while True:
        stats = HourStats(unread_channels=unread)
        bads: dict[str, list[int]] = {ch: [] for ch in files}
        try:
            streams = []
            for ch, p in files.items():
                if ch in in_memory:
                    records = sorted(
                        _channel_stream(venue, ch, p, strict=False, bad=bads[ch]), key=_sort_key
                    )
                    streams.append(iter(records))
                else:
                    streams.append(_channel_stream(venue, ch, p, strict=True, bad=bads[ch]))
            trades_df, book_df = _build(
                heapq.merge(*streams, key=_sort_key), venue, connects, stats
            )
            break
        except _Unsorted as e:
            in_memory.add(e.channel)
            log.warning(
                "%s %s %02d: %s; sorting channel %s in memory (needs more memory)",
                venue,
                date,
                hour,
                e,
                e.channel,
            )

    for ch, bad in bads.items():
        stats.channel(ch).bad_lines = len(bad)
    counts = {}
    for name, df in (("trades", trades_df), ("book_events", book_df)):
        counts[name] = df.height
        stats.rows[name] = df.height
        path = out_path(norm_root, name, venue, date, hour)
        if df.height == 0:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        df.write_parquet(tmp, compression="zstd", compression_level=PARQUET_ZSTD_LEVEL)
        tmp.replace(path)
    for problem in stats.problems():
        log.error("%s %s %02d: UNEXPLAINED LOSS: %s", venue, date, hour, problem)
    write_sidecar(norm_root, venue, date, hour, stats)
    return counts


class UnexplainedLoss(Exception):
    """Some raw messages were not turned into rows and not accounted for as ignorable."""

    def __init__(self, lossy: dict[tuple[str, str, int], list[str]]) -> None:
        self.lossy = lossy
        lines = [f"{v} {d} {h:02d}: {'; '.join(p)}" for (v, d, h), p in sorted(lossy.items())]
        super().__init__(f"unexplained loss in {len(lossy)} hours:\n" + "\n".join(lines))


def _is_open_hour(date: str, hour: int, now: float) -> bool:
    """True for the UTC hour containing `now` (or later): the recorder may still be writing it."""
    start = calendar.timegm(time.strptime(date, "%Y-%m-%d")) + hour * 3600
    return start + 3600 > now


def normalize_all(
    raw_root: Path,
    norm_root: Path,
    venues: list[str] | None = None,
    force: bool = False,
    closed_only: bool = False,
    now: float | None = None,
) -> dict[tuple[str, str, int], dict[str, int]]:
    """Normalize every hour found. With `closed_only`, skip the hour still being recorded so no
    partial Parquet is written (and later uploaded) for it.

    Raises `UnexplainedLoss` after all hours are done if any hour (this run or an earlier one)
    has unparseable messages, invalid lines, or raw channels nobody declared."""
    now = time.time() if now is None else now
    done = {}
    lossy: dict[tuple[str, str, int], list[str]] = {}
    for venue in venues or sorted({v for v, _ in PARSERS}):
        for date, hour in list_hours(raw_root, venue):
            if closed_only and _is_open_hour(date, hour, now):
                continue
            counts = normalize_hour(raw_root, norm_root, venue, date, hour, force)
            if counts:
                done[(venue, date, hour)] = counts
                log.info("normalized %s %s %02d: %s", venue, date, hour, counts)
            if problems := read_problems(norm_root, venue, date, hour):
                lossy[(venue, date, hour)] = problems
    if lossy:
        raise UnexplainedLoss(lossy)
    return done
