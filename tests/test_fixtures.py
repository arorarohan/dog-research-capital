"""Regression tests on small *real* raw samples, one per venue and channel (tests/fixtures/raw).

The expected values are derived here from the raw JSON by independent, deliberately simple code,
not by calling the parsers, so a parser change that alters meaning fails loudly."""

from pathlib import Path

import polars as pl
import pytest

from drc.normalize.pipeline import list_hours, normalize_all, out_path
from drc.recorder.writer import read_lines
from drc.validate import fields

RAW = Path(__file__).parent / "fixtures" / "raw"


@pytest.fixture(scope="module")
def norm(tmp_path_factory):
    out = tmp_path_factory.mktemp("norm")
    normalize_all(RAW, out)  # raises UnexplainedLoss if any raw message is not accounted for
    return out


def raw_msgs(venue, channel):
    for f in sorted((RAW / venue / channel).glob("*/*.jsonl")):
        for rec in read_lines(f):
            yield rec["m"]


def table(norm, name, venue):
    (date, hour) = list_hours(RAW, venue)[0]
    return pl.read_parquet(out_path(norm, name, venue, date, hour))


def test_every_fixture_field_and_channel_is_declared():
    problems, _ = fields.check([RAW])
    assert [p for p in problems if "undeclared" in p] == []


def test_every_parsed_channel_has_a_fixture():
    from drc.normalize.pipeline import PARSERS

    missing = [k for k in PARSERS if not (RAW / k[0] / k[1]).exists()]
    assert missing == []


def test_coinbase_trades_flip_the_maker_label(norm):
    expected = [
        (float(t["price"]), float(t["size"]), {"BUY": "sell", "SELL": "buy"}[t["side"]])
        for m in raw_msgs("coinbase", "market_trades")
        for ev in m["events"]
        for t in ev["trades"]
    ]
    t = table(norm, "trades", "coinbase")
    assert list(zip(t["price"], t["size"], t["side"], strict=True)) == expected
    assert t["replay"].to_list()[:2] == [True, True]  # the snapshot's trades are history


def test_kraken_trades(norm):
    expected = [
        (t["price"], t["qty"], t["side"]) for m in raw_msgs("kraken", "trade") for t in m["data"]
    ]
    t = table(norm, "trades", "kraken")
    assert list(zip(t["price"], t["size"], t["side"], strict=True)) == expected


def test_kalshi_trades_buy_means_taker_took_yes(norm):
    expected = [
        (
            float(m["yes_price_dollars"]),
            float(m["count_fp"]),
            "buy" if m["taker_side"] == "yes" else "sell",
        )
        for m in raw_msgs("kalshi", "trades")
    ]
    t = table(norm, "trades", "kalshi")
    assert list(zip(t["price"], t["size"], t["side"], strict=True)) == expected


def test_polymarket_trades(norm):
    expected = [
        (float(m["price"]), float(m["size"]), m["side"].lower())
        for m in raw_msgs("polymarket", "last_trade_price")
    ]
    t = table(norm, "trades", "polymarket")
    assert list(zip(t["price"], t["size"], t["side"], strict=True)) == expected


def test_book_row_counts_equal_levels_plus_resets(norm):
    """Row counts per venue from first principles: a snapshot is one reset plus its levels."""
    cb = sum(
        (ev["type"] == "snapshot") + len(ev["updates"])
        for m in raw_msgs("coinbase", "l2_data")
        for ev in m["events"]
    )
    kr = sum(
        (m["type"] == "snapshot") * 1 + len(d["bids"]) + len(d["asks"])
        for m in raw_msgs("kraken", "book")
        for d in m["data"]
    )
    pm_books = [
        b for m in raw_msgs("polymarket", "book") for b in (m if isinstance(m, list) else [m])
    ]
    pm = (
        len(pm_books)
        + sum(len(b["bids"]) + len(b["asks"]) for b in pm_books)
        + sum(len(m["price_changes"]) for m in raw_msgs("polymarket", "price_change"))
    )
    ka = sum(
        1
        + len(m["resp"]["orderbook_fp"]["yes_dollars"])
        + len(m["resp"]["orderbook_fp"]["no_dollars"])
        for m in raw_msgs("kalshi", "orderbook")
    )
    got = {
        v: table(norm, "book_events", v).height
        for v in ("coinbase", "kraken", "polymarket", "kalshi")
    }
    assert got == {"coinbase": cb, "kraken": kr, "polymarket": pm, "kalshi": ka}


def test_kalshi_no_bids_become_yes_asks(norm):
    book = table(norm, "book_events", "kalshi")
    first = next(
        m for m in raw_msgs("kalshi", "orderbook") if m["resp"]["orderbook_fp"]["no_dollars"]
    )
    no_prices = sorted(
        round(1 - float(p), 4) for p, _ in first["resp"]["orderbook_fp"]["no_dollars"]
    )
    asks = book.filter((pl.col("symbol") == first["ticker"]) & (pl.col("side") == "ask"))["price"]
    assert set(no_prices) <= set(asks.to_list())


def test_polymarket_book_has_both_sides_in_receive_order(norm):
    book = table(norm, "book_events", "polymarket")
    assert set(book["side"].drop_nulls().unique()) == {"bid", "ask"}
    assert book["ts_recv"].is_sorted()


def test_epochs_come_from_connect_events(norm):
    t = table(norm, "book_events", "kraken")
    connects = [
        r["t"]
        for f in (RAW / "kraken" / "_meta").glob("*/*.jsonl")
        for r in read_lines(f)
        if r.get("ev") == "connect"
    ]
    assert set(t["epoch"].unique()) <= set(connects) | {0}
