"""`drc-normalize`: convert raw recordings into Parquet tables."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from drc.normalize.pipeline import normalize_all


def main() -> None:
    ap = argparse.ArgumentParser(prog="drc-normalize")
    ap.add_argument("--raw", default="data/raw", type=Path)
    ap.add_argument("--norm", default="data/norm", type=Path)
    ap.add_argument("--venues", help="comma-separated, default all")
    ap.add_argument("--force", action="store_true", help="reprocess hours even if up to date")
    args = ap.parse_args()
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")
    venues = args.venues.split(",") if args.venues else None
    done = normalize_all(args.raw, args.norm, venues, args.force)
    print(f"normalized {len(done)} venue-hours")
