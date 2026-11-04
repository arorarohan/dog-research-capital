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

from drc.normalize.parsers import PARSERS
from drc.normalize.rows import BOOK_EVENTS_SCHEMA, TRADES_SCHEMA, BookRow, Trade
from drc.recorder.writer import META_CHANNEL, read_lines

log = logging.getLogger(__name__)

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


def list_hours(raw_root: Path, venue: str) -> list[tuple[str, int]]:
    hours: set[tuple[str, int]] = set()
    for (v, channel), _ in PARSERS.items():
        if v != venue:
            continue
        for p in (raw_root / venue / channel).glob("*/*.jsonl*"):
            hours.add((p.parent.name, int(p.name[:2])))
    return sorted(hours)


def out_path(norm_root: Path, table: str, venue: str, date: str, hour: int) -> Path:
    return norm_root / table / f"venue={venue}" / f"date={date}" / f"hour={hour:02d}.parquet"


# Rows are accumulated in Python lists and turned into a polars chunk every CHUNK_ROWS, so peak
# memory is bounded by the chunk plus the finished chunks (columnar, far smaller than lists).
CHUNK_ROWS = 500_000


class _Unsorted(Exception):
    """A channel file is not in receive-time order, so a streaming merge would not equal a sort."""


def _sort_key(rec: tuple) -> tuple:
    return rec[:3]  # (receive time, channel order, line index): unique, so the order is total


def _channel_stream(venue: str, channel: str, path: Path, strict: bool):
    """Records of one channel file as (ts_recv, channel order, line index, channel, message).
    With `strict`, raise _Unsorted the moment receive time goes backwards."""
    order = CHANNEL_ORDER[(venue, channel)]
    prev = None
    for line_idx, rec in enumerate(read_lines(path)):
        t = rec["t"]
        if strict and prev is not None and t < prev:
            raise _Unsorted(f"{channel} file goes back in time at line {line_idx}")
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


def _build(records, venue: str, connects: list[int]) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Parse receive-ordered records into the `trades` and `book_events` frames."""
    trades, book = _Table(TRADES_SCHEMA, CHUNK_ROWS), _Table(BOOK_EVENTS_SCHEMA, CHUNK_ROWS)
    msg_id = 0
    for ts_recv, _, _, channel, msg in records:
        table, parse = PARSERS[(venue, channel)]
        i = bisect.bisect_right(connects, ts_recv)
        epoch = connects[i - 1] if i else 0
        try:
            rows = list(parse(msg))
        except (KeyError, ValueError, TypeError) as e:
            log.warning("%s/%s unparseable message at %d: %r", venue, channel, ts_recv, e)
            continue
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
    """Normalize one hour. Returns row counts per table (empty dict if skipped)."""
    files = _hour_files(raw_root, venue, date, hour)
    if not files:
        return {}
    outs = [out_path(norm_root, t, venue, date, hour) for t in ("trades", "book_events")]
    newest_input = max(p.stat().st_mtime for p in files.values())
    if not force and all(o.exists() and o.stat().st_mtime >= newest_input for o in outs):
        return {}

    connects = connect_times(raw_root, venue)
    try:
        # Each channel file is written in receive-time order, so a k-way merge yields the same
        # global order as sorting everything, holding only one record per channel at a time.
        streams = [_channel_stream(venue, ch, p, strict=True) for ch, p in files.items()]
        trades_df, book_df = _build(heapq.merge(*streams, key=_sort_key), venue, connects)
    except _Unsorted as e:
        log.warning(
            "%s %s %02d: %s; falling back to a full sort (needs more memory)", venue, date, hour, e
        )
        records = sorted(
            (r for ch, p in files.items() for r in _channel_stream(venue, ch, p, strict=False)),
            key=_sort_key,
        )
        trades_df, book_df = _build(records, venue, connects)

    counts = {}
    for name, df in (("trades", trades_df), ("book_events", book_df)):
        counts[name] = df.height
        path = out_path(norm_root, name, venue, date, hour)
        if df.height == 0:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        df.write_parquet(tmp, compression="zstd", compression_level=PARQUET_ZSTD_LEVEL)
        tmp.replace(path)
    return counts


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
    partial Parquet is written (and later uploaded) for it."""
    now = time.time() if now is None else now
    done = {}
    for venue in venues or sorted({v for v, _ in PARSERS}):
        for date, hour in list_hours(raw_root, venue):
            if closed_only and _is_open_hour(date, hour, now):
                continue
            counts = normalize_hour(raw_root, norm_root, venue, date, hour, force)
            if counts:
                done[(venue, date, hour)] = counts
                log.info("normalized %s %s %02d: %s", venue, date, hour, counts)
    return done
