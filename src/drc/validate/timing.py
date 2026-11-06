"""Timestamp checks on raw recordings: ordering, exchange-vs-receive skew, silent gaps.

    uv run python -m drc.validate.timing data/raw

1. Backward steps: receive times (`t`) must not decrease within a channel file. The writer makes
   its own stamps strictly increasing; a channel that stamps with the raw wall clock can step
   back when WSL2's clock does (found on Kalshi order books, fixed in the recorder).
2. Skew: `t - exchange timestamp` per channel. Large or negative values mean the local clock or
   the venue's timestamps cannot be trusted for cross-venue alignment (gotcha: WSL2 clock).
3. Gaps: stretches with no message on a busy channel. A gap overlapping a recorded
   disconnect/connect pair is *explained* (the recorder knows it was blind); any other gap is
   unexplained silence, which means either a quiet market or a loss we did not record.
Nothing here interpolates anything: gaps are reported, never filled.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from drc.normalize.parsers import PARSERS
from drc.normalize.pipeline import list_hours
from drc.normalize.rows import Trade
from drc.recorder.writer import META_CHANNEL, read_lines

BUSY_CHANNELS = {
    ("coinbase", "l2_data"),
    ("kraken", "book"),
    ("polymarket", "price_change"),
}
SKEW_SAMPLE = 20_000  # messages per file


def backward_steps(raw_root: Path) -> dict[tuple[str, str], tuple[int, int]]:
    """(venue, channel) -> (backward steps, messages), counted per file."""
    out: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for venue, channel in PARSERS:
        for f in sorted((raw_root / venue / channel).glob("*/*.jsonl*")):
            prev = None
            for rec in read_lines(f):
                out[(venue, channel)][1] += 1
                if prev is not None and rec["t"] < prev:
                    out[(venue, channel)][0] += 1
                prev = rec["t"]
    return {k: (v[0], v[1]) for k, v in out.items()}


def _pct(xs: list[float], q: float) -> float:
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def skew_ms(raw_root: Path) -> dict[tuple[str, str], dict[str, float]]:
    """(venue, channel) -> {n, median, p01, p99, negative share} of (receive - exchange) in ms."""
    out = {}
    for (venue, channel), (_, parse) in PARSERS.items():
        deltas: list[float] = []
        for f in sorted((raw_root / venue / channel).glob("*/*.jsonl*")):
            for i, rec in enumerate(read_lines(f)):
                if i >= SKEW_SAMPLE:
                    break
                for row in parse(rec["m"]):
                    if isinstance(row, Trade) and row.replay:
                        continue  # history sent on subscribe is old by design
                    if row.ts_exch is not None and getattr(row, "kind", "") != "reset":
                        deltas.append((rec["t"] - row.ts_exch) / 1e6)
                        break  # one sample per message is enough
        if deltas:
            deltas.sort()
            out[(venue, channel)] = {
                "n": len(deltas),
                "median": _pct(deltas, 0.5),
                "p01": _pct(deltas, 0.01),
                "p99": _pct(deltas, 0.99),
                "negative": sum(d < 0 for d in deltas) / len(deltas),
            }
    return out


@dataclass
class GapReport:
    messages: int = 0
    gaps: int = 0
    explained: int = 0
    unexplained: list[tuple[float, float]] = field(default_factory=list)  # (start t, seconds)
    longest_s: float = 0.0


def _outages(raw_root: Path, venue: str) -> list[tuple[int, int]]:
    """(disconnect, next connect) windows from the recorder's own event log."""
    events = []
    for f in sorted((raw_root / venue / META_CHANNEL).glob("*/*.jsonl*")):
        events += [(r["t"], r["ev"]) for r in read_lines(f) if "ev" in r]
    events.sort()
    out, down = [], None
    for t, ev in events:
        if ev in ("disconnect", "shutdown") and down is None:
            down = t
        elif ev == "connect" and down is not None:
            out.append((down, t))
            down = None
    return out


def gaps(raw_root: Path, venue: str, channel: str, min_gap_s: float = 2.0) -> GapReport:
    rep = GapReport()
    outages = _outages(raw_root, venue)
    prev = None
    for f in sorted((raw_root / venue / channel).glob("*/*.jsonl*")):
        for rec in read_lines(f):
            t = rec["t"]
            rep.messages += 1
            if prev is not None and (t - prev) / 1e9 >= min_gap_s:
                rep.gaps += 1
                rep.longest_s = max(rep.longest_s, (t - prev) / 1e9)
                if any(lo <= t and hi >= prev for lo, hi in outages):
                    rep.explained += 1
                else:
                    rep.unexplained.append((prev / 1e9, (t - prev) / 1e9))
            prev = t
    return rep


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "data/raw")
    print("== backward steps in receive time (per file)")
    for (v, c), (n, total) in sorted(backward_steps(root).items()):
        print(f"  {v}/{c}: {n} of {total} messages go back in time")
    print("== receive minus exchange timestamp (ms)")
    for (v, c), s in sorted(skew_ms(root).items()):
        print(
            f"  {v}/{c}: n={s['n']} median {s['median']:.0f}  p01 {s['p01']:.0f}  "
            f"p99 {s['p99']:.0f}  negative {s['negative']:.2%}"
        )
    print("== gaps >= 2 s on busy channels")
    for v, c in sorted(BUSY_CHANNELS):
        if not list_hours(root, v):
            continue
        g = gaps(root, v, c)
        print(
            f"  {v}/{c}: {g.messages} msgs, {g.gaps} gaps ({g.explained} across a recorded "
            f"disconnect, {len(g.unexplained)} unexplained), longest {g.longest_s:.1f}s"
        )
        for start, secs in g.unexplained:
            print(f"      unexplained: {secs:.1f}s starting at unix {start:.1f}")
    print(
        "  (an unexplained gap starting at the same instant on several venues is a local stall:\n"
        "   the process or machine paused; messages after it are stamped late, not lost)"
    )


if __name__ == "__main__":
    main()
