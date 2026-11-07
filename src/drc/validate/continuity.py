"""Did we lose messages? Check the sequence numbers and trade ids the exchanges give us.

    uv run python -m drc.validate.continuity data/raw

- Coinbase `sequence_num` is one counter per connection shared by all channels, so merging every
  Coinbase channel in receive order must give consecutive numbers within a connection epoch.
  (Checking one channel alone shows false gaps: the other channels use the missing numbers.)
- Coinbase and Kraken trade ids are consecutive integers per product. A run of missing ids is
  lost trades; it is "explained" if a recorded reconnect lies between the neighbours, otherwise
  it is unexplained loss. Replay snapshots (sent after subscribing) count as seen.
Kalshi trade ids are random UUIDs and Polymarket's `transaction_hash` is not sequential, so
neither can be checked this way (see docs/validation.md for what covers them).
"""

from __future__ import annotations

import bisect
import heapq
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from drc.normalize.pipeline import load_epochs
from drc.recorder.writer import read_lines


def _stream(raw_root: Path, venue: str, channel: str):
    for f in sorted((raw_root / venue / channel).glob("*/*.jsonl*")):
        for rec in read_lines(f):
            yield rec["t"], channel, rec["m"]


@dataclass
class SequenceResult:
    messages: int = 0
    gaps: int = 0  # places where the next number is not previous + 1 within an epoch
    missing: int = 0  # how many numbers are missing in total
    examples: list[tuple[int, int, str]] = field(default_factory=list)


def coinbase_sequence(raw_root: Path) -> SequenceResult:
    res = SequenceResult()
    connects = load_epochs(raw_root, "coinbase").venue_wide
    chans = [
        _stream(raw_root, "coinbase", c) for c in ("l2_data", "market_trades", "subscriptions")
    ]
    prev = prev_epoch = None
    for t, channel, m in heapq.merge(*chans, key=lambda x: x[0]):
        seq, epoch = m["sequence_num"], bisect.bisect_right(connects, t)
        res.messages += 1
        if prev is not None and epoch == prev_epoch and seq != prev + 1:
            res.gaps += 1
            res.missing += abs(seq - prev - 1)
            if len(res.examples) < 5:
                res.examples.append((prev, seq, channel))
        prev, prev_epoch = seq, epoch
    return res


@dataclass
class IdResult:
    trades: int = 0
    missing: int = 0
    explained: int = 0  # missing ids that straddle a recorded reconnect
    unexplained: int = 0
    examples: list[str] = field(default_factory=list)


def _trade_ids(raw_root: Path, venue: str):
    """Yield (t_recv, symbol, trade_id) for every trade, replay snapshots included."""
    if venue == "coinbase":
        for t, _, m in _stream(raw_root, venue, "market_trades"):
            for ev in m["events"]:
                for tr in ev["trades"]:
                    yield t, tr["product_id"], int(tr["trade_id"])
    else:
        for t, _, m in _stream(raw_root, venue, "trade"):
            for tr in m["data"]:
                yield t, tr["symbol"], int(tr["trade_id"])


def trade_id_continuity(raw_root: Path, venue: str) -> IdResult:
    res = IdResult()
    connects = load_epochs(raw_root, venue).venue_wide
    seen: dict[str, dict[int, int]] = defaultdict(dict)  # symbol -> id -> first receive time
    for t, sym, tid in _trade_ids(raw_root, venue):
        res.trades += 1
        seen[sym].setdefault(tid, t)
    for sym, ids in seen.items():
        ordered = sorted(ids)
        for a, b in zip(ordered, ordered[1:], strict=False):
            if b == a + 1:
                continue
            n = b - a - 1
            lo, hi = sorted((ids[a], ids[b]))
            res.missing += n
            if bisect.bisect_right(connects, hi) > bisect.bisect_right(connects, lo):
                res.explained += n
            else:
                res.unexplained += n
                if len(res.examples) < 5:
                    res.examples.append(f"{sym}: ids {a + 1}..{b - 1} missing, no reconnect")
    return res


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "data/raw")
    if (root / "coinbase").exists():
        s = coinbase_sequence(root)
        print(
            f"coinbase sequence_num: {s.messages} messages, {s.gaps} gaps, {s.missing} numbers "
            f"missing {s.examples}"
        )
    for venue in ("coinbase", "kraken"):
        if (root / venue).exists():
            r = trade_id_continuity(root, venue)
            print(
                f"{venue} trade ids: {r.trades} trades, {r.missing} ids missing "
                f"({r.explained} across a recorded reconnect, {r.unexplained} unexplained) "
                f"{r.examples}"
            )


if __name__ == "__main__":
    main()
