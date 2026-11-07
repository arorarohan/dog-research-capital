"""Regenerate the raw-sample fixtures from a real recording.

    uv run python tests/fixtures/make_fixtures.py data/raw [data/raw_forward]

Takes the first few messages of every (venue, channel) from the earliest hour file and writes
them verbatim (one `{"t":..,"m":..}` line each) under tests/fixtures/raw/. Only two things are
changed, to keep the files small: Polymarket's array-form book snapshot is cut to its first two
books, and every book side is cut to at most 12 levels. Cut books no longer match exchange
checksums, so checksum tests use their own data. These samples are the seed of the verbatim
sample archive decided on #15.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import orjson

from drc.recorder.writer import read_lines

OUT = Path(__file__).parent / "raw"
PER_CHANNEL = {"book": 3, "l2_data": 3, "price_change": 4, "orderbook": 2}
DEFAULT = 3
MAX_LEVELS = 12


def trim(m):
    if isinstance(m, list):
        return [trim(b) for b in m[:2]]
    if isinstance(m, dict):
        out = {}
        for k, v in m.items():
            if k in ("bids", "asks") and isinstance(v, list):
                out[k] = v[:MAX_LEVELS]
            elif k in ("yes_dollars", "no_dollars") and isinstance(v, list):
                out[k] = v[:MAX_LEVELS]
            elif k == "updates" and isinstance(v, list):
                out[k] = v[:MAX_LEVELS]
            elif k == "data" and isinstance(v, list):
                out[k] = [trim(x) for x in v]
            elif isinstance(v, (dict, list)):
                out[k] = trim(v)
            else:
                out[k] = v
        return out
    return m


def main(roots: list[Path]) -> None:
    for root in roots:
        for chan_dir in sorted(root.glob("*/*")):
            venue, channel = chan_dir.parent.name, chan_dir.name
            if channel.startswith("_") and channel != "_meta":
                continue  # catalogs are large and not normalized
            files = sorted(chan_dir.glob("*/*.jsonl*"))
            if not files:
                continue
            f = files[0]
            dest = OUT / venue / channel / f.parent.name / (f.name.split(".")[0] + ".jsonl")
            if dest.exists():
                continue
            want = PER_CHANNEL.get(channel, DEFAULT)
            picked = []
            for rec in read_lines(f):
                picked.append({**rec, "m": trim(rec["m"])} if "m" in rec else rec)
                if len(picked) >= want:
                    break
            # also keep the first list-form Polymarket book and a Kalshi empty book if present
            if (venue, channel) == ("polymarket", "book"):
                for rec in read_lines(f):
                    if isinstance(rec["m"], list):
                        picked.append({**rec, "m": trim(rec["m"])})
                        break
            if (venue, channel) == ("kalshi", "orderbook"):
                for rec in read_lines(f):
                    ob = rec["m"]["resp"]["orderbook_fp"]
                    if not ob["yes_dollars"] and not ob["no_dollars"]:
                        picked.append(rec)
                        break
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("wb") as fh:
                for rec in picked:
                    fh.write(orjson.dumps(rec) + b"\n")
            print(dest, len(picked), json.dumps(picked[0])[:80])


if __name__ == "__main__":
    main([Path(a) for a in sys.argv[1:]])
