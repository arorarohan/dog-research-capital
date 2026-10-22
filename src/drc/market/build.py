"""`drc-bars`: normalized Parquet -> replayed book states -> bars, written per venue."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from drc.market.bars import load_trades, make_bars
from drc.market.replay import load_book_events, replay_tops

log = logging.getLogger(__name__)
VENUES = ["coinbase", "kraken", "polymarket", "kalshi"]


def build_bars(norm_root: Path, venue: str, bar_secs: int = 1) -> Path | None:
    events = load_book_events(norm_root, venue)
    if events.is_empty():
        return None
    tops, stats = replay_tops(events)
    if stats.crossed_states or stats.dropped_unsynced:
        log.warning(
            "%s: %d crossed states, %d unsynced updates dropped",
            venue,
            stats.crossed_states,
            stats.dropped_unsynced,
        )
    bars = make_bars(tops, load_trades(norm_root, venue), bar_secs)
    path = norm_root / f"bars_{bar_secs}s" / f"venue={venue}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    bars.write_parquet(path, compression="zstd")
    log.info("%s: %d bars -> %s", venue, bars.height, path)
    return path


def main() -> None:
    ap = argparse.ArgumentParser(prog="drc-bars")
    ap.add_argument("--norm", default="data/norm", type=Path)
    ap.add_argument("--venues", help="comma-separated, default all")
    ap.add_argument("--bar-secs", type=int, default=1)
    args = ap.parse_args()
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")
    for venue in args.venues.split(",") if args.venues else VENUES:
        build_bars(args.norm, venue, args.bar_secs)
