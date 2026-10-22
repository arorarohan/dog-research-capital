"""Validate our book reconstruction against Kraken's own CRC32 book checksums.

Every Kraken book update carries a checksum of the top 10 asks then top 10 bids, computed by
the exchange. Rebuilding the book from raw messages and comparing is ground truth for
"is our replay logic correct". Decimal parsing preserves the trailing zeros the checksum needs.
"""

from __future__ import annotations

import zlib
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path

import zstandard
from sortedcontainers import SortedDict


def _digits(d: Decimal) -> str:
    return str(d).replace(".", "").lstrip("0")


def book_checksum(bids: SortedDict, asks: SortedDict) -> int:
    s = "".join(_digits(p) + _digits(q) for p, q in asks.items()[:10])
    s += "".join(_digits(p) + _digits(q) for p, q in reversed(bids.items()[-10:]))
    return zlib.crc32(s.encode())


def _raw_lines(path: Path) -> list[bytes]:
    if path.suffix == ".zst":
        with path.open("rb") as fh:
            return zstandard.ZstdDecompressor().stream_reader(fh).read().splitlines()
    return path.read_bytes().splitlines()


def _decimal_loads(line: bytes):
    import json

    return json.loads(line, parse_float=Decimal)


def check_messages(messages: Iterable[dict], symbol: str, depth: int = 100) -> tuple[int, int]:
    """Replay Kraken `book` messages for one symbol. Returns (matching, mismatching) updates."""
    bids: SortedDict = SortedDict()
    asks: SortedDict = SortedDict()
    ok = bad = 0
    for m in messages:
        for d in m["data"]:
            if d["symbol"] != symbol:
                continue
            if m["type"] == "snapshot":
                bids.clear()
                asks.clear()
            for levels, book in ((d["bids"], bids), (d["asks"], asks)):
                for lv in levels:
                    if lv["qty"] > 0:
                        book[lv["price"]] = lv["qty"]
                    else:
                        book.pop(lv["price"], None)
            while len(bids) > depth:
                bids.popitem(0)
            while len(asks) > depth:
                asks.popitem(-1)
            if "checksum" in d:
                if book_checksum(bids, asks) == d["checksum"]:
                    ok += 1
                else:
                    bad += 1
    return ok, bad


def check_raw_dir(raw_root: Path, symbol: str = "BTC/USD", depth: int = 100) -> tuple[int, int]:
    def messages():
        for f in sorted((raw_root / "kraken" / "book").rglob("*.jsonl*")):
            for line in _raw_lines(f):
                try:
                    rec = _decimal_loads(line)
                except ValueError:
                    continue
                m = rec.get("m")
                if isinstance(m, dict) and m.get("channel") == "book":
                    yield m

    return check_messages(messages(), symbol, depth)


if __name__ == "__main__":
    import sys

    root = Path(sys.argv[1] if len(sys.argv) > 1 else "data/raw")
    for sym in ("BTC/USD", "ETH/USD", "SOL/USD"):
        ok, bad = check_raw_dir(root, sym)
        print(f"{sym}: {ok} match, {bad} mismatch ({ok / max(ok + bad, 1):.4%})")
