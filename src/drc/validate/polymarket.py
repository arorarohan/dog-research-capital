"""Check Polymarket book reconstruction against the exchange's own numbers.

    uv run python -m drc.validate.polymarket data/raw

Replays raw messages through the real parsers (`drc.normalize.parsers`) into `Book`s and compares,
in receive order, with two things the exchange sends us:
1. `best_bid` / `best_ask` on every `price_change` item: the exchange's top of book after the
   change. Compared after each item and after the whole message (they differ only when one
   message changes several levels of the same asset).
2. Every `book` message after an asset's first: a full snapshot, compared level by level with
   what replaying the earlier messages produced.
Resets on reconnect (`_meta` connect events), exactly like `drc.market.replay`. Updates for an
asset with no snapshot yet in the epoch are skipped and counted (never applied to an empty book).
"""

from __future__ import annotations

import bisect
import heapq
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from drc.market.book import Book
from drc.normalize.parsers import polymarket_book, polymarket_price_change
from drc.normalize.pipeline import connect_times
from drc.recorder.writer import read_lines

VENUE = "polymarket"


@dataclass
class Result:
    top_items: int = 0
    top_match_item: int = 0  # best bid and ask equal after applying that single item
    top_match_msg: int = 0  # ... equal after applying the whole message
    top_skipped_unsynced: int = 0
    top_mismatch_examples: list[str] = field(default_factory=list)
    snapshots: int = 0  # book messages for an asset we already had a book for
    snapshot_match: int = 0
    snapshot_mismatch_examples: list[str] = field(default_factory=list)

    def report(self) -> str:
        def pct(a, b):
            return f"{a}/{b} = {a / b:.4%}" if b else "n/a"

        return "\n".join(
            [
                f"price_change top of book vs exchange best: item-wise "
                f"{pct(self.top_match_item, self.top_items)}, message-wise "
                f"{pct(self.top_match_msg, self.top_items)} "
                f"({self.top_skipped_unsynced} items skipped: asset not yet snapshotted)",
                f"full book snapshots vs replay: {pct(self.snapshot_match, self.snapshots)}",
                *self.top_mismatch_examples,
                *self.snapshot_mismatch_examples,
            ]
        )


def _stream(raw_root: Path, channel: str):
    files = sorted((raw_root / VENUE / channel).glob("*/*.jsonl*"))
    for f in files:
        for rec in read_lines(f):
            yield rec["t"], channel, rec["m"]


def _top(book: Book):
    bb, ba = book.best_bid(), book.best_ask()
    return (bb[0] if bb else 0.0), (ba[0] if ba else 1.0)  # the exchange reports 0 / 1 if empty


def check(raw_root: Path, max_examples: int = 5) -> Result:
    res = Result()
    connects = connect_times(raw_root, VENUE)
    merged = heapq.merge(
        _stream(raw_root, "book"), _stream(raw_root, "price_change"), key=lambda x: x[0]
    )
    books: dict[str, Book] = defaultdict(Book)
    synced: set[str] = set()
    epoch = None
    for t, channel, msg in merged:
        i = bisect.bisect_right(connects, t)
        e = connects[i - 1] if i else 0
        if e != epoch:
            epoch, synced = e, set()
            books.clear()
        if channel == "book":
            # group the parser's rows by asset: each asset's reset starts its snapshot
            fresh: dict[str, Book] = {}
            for row in polymarket_book(msg):
                if row.kind == "reset":
                    fresh[row.symbol] = Book()
                else:
                    fresh[row.symbol].set_level(row.side, row.price, row.size)
            for sym, new in fresh.items():
                if sym in synced:
                    old = books[sym]
                    res.snapshots += 1
                    if dict(old.bids) == dict(new.bids) and dict(old.asks) == dict(new.asks):
                        res.snapshot_match += 1
                    elif len(res.snapshot_mismatch_examples) < max_examples:
                        res.snapshot_mismatch_examples.append(
                            f"snapshot mismatch {sym[:12]} t={t}: replay has "
                            f"{len(old.bids)}b/{len(old.asks)}a levels, exchange "
                            f"{len(new.bids)}b/{len(new.asks)}a"
                        )
                books[sym] = new
                synced.add(sym)
        else:
            rows = list(polymarket_price_change(msg))
            items = msg["price_changes"]
            ok_item = {}
            for row, item in zip(rows, items, strict=True):
                if row.symbol not in synced:
                    res.top_skipped_unsynced += 1
                    continue
                books[row.symbol].set_level(row.side, row.price, row.size)
                want = (float(item["best_bid"]), float(item["best_ask"]))
                ok_item[id(item)] = _top(books[row.symbol]) == want
            for row, item in zip(rows, items, strict=True):
                if row.symbol not in synced:
                    continue
                want = (float(item["best_bid"]), float(item["best_ask"]))
                res.top_items += 1
                res.top_match_item += ok_item[id(item)]
                got = _top(books[row.symbol])
                if got == want:
                    res.top_match_msg += 1
                elif len(res.top_mismatch_examples) < max_examples:
                    res.top_mismatch_examples.append(
                        f"top mismatch {row.symbol[:12]} t={t}: replay {got}, exchange {want}"
                    )
    return res


if __name__ == "__main__":
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "data/raw")
    print(check(root).report())
