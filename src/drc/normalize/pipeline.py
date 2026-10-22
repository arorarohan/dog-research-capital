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
import logging
from pathlib import Path

import polars as pl

from drc.normalize.parsers import PARSERS
from drc.normalize.rows import BOOK_EVENTS_SCHEMA, TRADES_SCHEMA, BookRow, Trade
from drc.recorder.writer import META_CHANNEL, read_lines

log = logging.getLogger(__name__)

CHANNEL_ORDER = {key: i for i, key in enumerate(PARSERS)}


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

    # Merge every channel of the hour into one stream ordered by receive time.
    records = []
    for channel, path in files.items():
        for line_idx, rec in enumerate(read_lines(path)):
            records.append((rec["t"], CHANNEL_ORDER[(venue, channel)], line_idx, channel, rec["m"]))
    records.sort(key=lambda r: r[:3])

    connects = connect_times(raw_root, venue)
    trades: dict[str, list] = {k: [] for k in TRADES_SCHEMA}
    book: dict[str, list] = {k: [] for k in BOOK_EVENTS_SCHEMA}
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
                cols = trades
                cols["trade_id"].append(row.trade_id)
                cols["replay"].append(row.replay)
                cols["price"].append(row.price)
                cols["size"].append(row.size)
                cols["side"].append(row.side)
            else:
                assert isinstance(row, BookRow) and table == "book"
                cols = book
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

    counts = {}
    for name, cols, schema in (
        ("trades", trades, TRADES_SCHEMA),
        ("book_events", book, BOOK_EVENTS_SCHEMA),
    ):
        df = pl.DataFrame(cols, schema=schema)
        counts[name] = df.height
        path = out_path(norm_root, name, venue, date, hour)
        if df.height == 0:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        df.write_parquet(tmp, compression="zstd")
        tmp.replace(path)
    return counts


def normalize_all(
    raw_root: Path, norm_root: Path, venues: list[str] | None = None, force: bool = False
) -> dict[tuple[str, str, int], dict[str, int]]:
    done = {}
    for venue in venues or sorted({v for v, _ in PARSERS}):
        for date, hour in list_hours(raw_root, venue):
            counts = normalize_hour(raw_root, norm_root, venue, date, hour, force)
            if counts:
                done[(venue, date, hour)] = counts
                log.info("normalized %s %s %02d: %s", venue, date, hour, counts)
    return done
