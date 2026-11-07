"""`drc-normalize`: convert raw recordings into Parquet tables."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from drc.normalize.pipeline import UnexplainedLoss, normalize_all


def main() -> None:
    ap = argparse.ArgumentParser(prog="drc-normalize")
    ap.add_argument("--raw", default="data/raw", type=Path)
    ap.add_argument("--norm", default="data/norm", type=Path)
    ap.add_argument("--venues", help="comma-separated, default all")
    ap.add_argument("--force", action="store_true", help="reprocess hours even if up to date")
    ap.add_argument(
        "--closed-only",
        action="store_true",
        help="skip the hour still being recorded (no partial Parquet); use in scheduled jobs",
    )
    args = ap.parse_args()
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")
    venues = args.venues.split(",") if args.venues else None
    try:
        done = normalize_all(args.raw, args.norm, venues, args.force, args.closed_only)
    except UnexplainedLoss as e:
        print(f"UNEXPLAINED LOSS (see {args.norm}/_accounting): {e}", file=sys.stderr)
        sys.exit(1)
    print(f"normalized {len(done)} venue-hours")
