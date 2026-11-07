"""Every raw message is parsed, counted as empty, or counted as lost; lost fails loudly (#20)."""

import json

import polars as pl
import pytest

from drc.normalize.accounting import read_problems, sidecar_path
from drc.normalize.parsers import coinbase_l2, kalshi_book, kalshi_trades, polymarket_trades
from drc.normalize.pipeline import UnexplainedLoss, normalize_all, normalize_hour, out_path
from drc.recorder.writer import NS_PER_HOUR, RawWriter
from drc.validate import fields

T0 = 1_760_000_000 * 1_000_000_000
T0 -= T0 % NS_PER_HOUR
DATE, HOUR = "2025-10-09", 8

BOOK = (
    '{"channel":"book","type":"snapshot","data":[{"symbol":"BTC/USD",'
    '"bids":[{"price":10.0,"qty":1.0}],"asks":[]}]}'
)


TRADE = (
    '{"channel":"trade","type":"update","data":[{"symbol":"BTC/USD","side":"buy","price":1.0,'
    '"qty":1.0,"trade_id":1,"timestamp":"2025-10-09T08:00:00Z"}]}'
)


def _sidecar(norm):
    return json.loads(sidecar_path(norm, "kraken", DATE, HOUR).read_text())


def test_clean_hour_balances(tmp_path):
    raw, norm = tmp_path / "raw", tmp_path / "norm"
    w = RawWriter(raw)
    w.write("kraken", "book", BOOK, ts_ns=T0 + 10)
    w.close()
    assert normalize_all(raw, norm)  # no exception
    s = _sidecar(norm)
    assert s["ok"] and s["problems"] == []
    assert s["channels"]["book"] | {"examples": []} == {
        "raw": 1,
        "parsed": 1,
        "empty": 0,
        "unparseable": 0,
        "bad_lines": 0,
        "rows": 2,
        "examples": [],
    }
    assert s["rows"] == {"trades": 0, "book_events": 2}


def test_unparseable_message_fails_loudly_but_keeps_the_rest(tmp_path):
    raw, norm = tmp_path / "raw", tmp_path / "norm"
    w = RawWriter(raw)
    w.write("kraken", "book", BOOK, ts_ns=T0 + 10)
    w.write("kraken", "book", "PONG", ts_ns=T0 + 20)  # valid JSON, not a book
    w.write(
        "kraken",
        "trade",
        '{"type":"update","data":[{"symbol":"BTC/USD","side":"?"}]}',
        ts_ns=T0 + 30,
    )
    w.close()
    with pytest.raises(UnexplainedLoss) as e:
        normalize_all(raw, norm)
    assert "unparseable" in str(e.value)
    # the good message was still written
    assert pl.read_parquet(out_path(norm, "book_events", "kraken", DATE, HOUR)).height == 2
    s = _sidecar(norm)
    assert not s["ok"]
    assert s["channels"]["book"]["unparseable"] == 1 and s["channels"]["trade"]["unparseable"] == 1
    # a later run re-reports it from the sidecar even though the hour is up to date
    with pytest.raises(UnexplainedLoss):
        normalize_all(raw, norm)
    assert read_problems(norm, "kraken", DATE, HOUR)


def test_invalid_json_line_is_counted(tmp_path):
    raw, norm = tmp_path / "raw", tmp_path / "norm"
    w = RawWriter(raw)
    w.write("kraken", "book", BOOK, ts_ns=T0 + 10)
    w.close()
    f = next(raw.rglob("book/*/*.jsonl"))
    f.write_bytes(f.read_bytes() + b"{not json\n")
    with pytest.raises(UnexplainedLoss):
        normalize_all(raw, norm)
    assert _sidecar(norm)["channels"]["book"]["bad_lines"] == 1


def test_undeclared_raw_channel_is_flagged(tmp_path):
    raw, norm = tmp_path / "raw", tmp_path / "norm"
    w = RawWriter(raw)
    w.write("kraken", "book", BOOK, ts_ns=T0 + 10)
    w.write("kraken", "instrument", '{"channel":"instrument","data":[]}', ts_ns=T0 + 11)
    w.write("kraken", "status", '{"channel":"status","data":[]}', ts_ns=T0 + 12)  # declared
    w.close()
    with pytest.raises(UnexplainedLoss) as e:
        normalize_all(raw, norm)
    assert "instrument" in str(e.value) and "status" not in str(e.value)


def test_empty_message_is_an_explained_ignore(tmp_path):
    raw, norm = tmp_path / "raw", tmp_path / "norm"
    w = RawWriter(raw)
    w.write("coinbase", "l2_data", '{"channel":"l2_data","events":[]}', ts_ns=T0 + 10)
    w.close()
    normalize_hour(raw, norm, "coinbase", DATE, HOUR)
    s = json.loads(sidecar_path(norm, "coinbase", DATE, HOUR).read_text())
    assert s["ok"] and s["channels"]["l2_data"]["empty"] == 1


def test_hour_without_sidecar_is_redone(tmp_path):
    raw, norm = tmp_path / "raw", tmp_path / "norm"
    w = RawWriter(raw)
    w.write("kraken", "book", BOOK, ts_ns=T0 + 10)
    w.write("kraken", "trade", TRADE, ts_ns=T0 + 11)  # (an empty table is never "up to date")
    w.close()
    normalize_all(raw, norm)
    assert normalize_all(raw, norm) == {}
    sidecar_path(norm, "kraken", DATE, HOUR).unlink()  # normalized before accounting existed
    assert len(normalize_all(raw, norm)) == 1
    assert sidecar_path(norm, "kraken", DATE, HOUR).exists()


# ---- parsers refuse to guess -------------------------------------------------------------


@pytest.mark.parametrize(
    "parse, msg",
    [
        (
            coinbase_l2,
            {
                "timestamp": "2025-01-01T00:00:00Z",
                "events": [{"type": "weird", "product_id": "X", "updates": []}],
            },
        ),
        (
            coinbase_l2,
            {
                "timestamp": "2025-01-01T00:00:00Z",
                "events": [
                    {
                        "type": "update",
                        "product_id": "X",
                        "updates": [
                            {
                                "side": "buy",
                                "event_time": "2025-01-01T00:00:00Z",
                                "price_level": "1",
                                "new_quantity": "1",
                            }
                        ],
                    }
                ],
            },
        ),
        (polymarket_trades, {"asset_id": "a", "price": "0.5", "size": "1", "side": "HOLD"}),
        (
            kalshi_trades,
            {
                "ticker": "T",
                "trade_id": "1",
                "created_time": "2025-01-01T00:00:00Z",
                "yes_price_dollars": "0.5",
                "count_fp": "1",
                "taker_side": "maybe",
            },
        ),
        (kalshi_book, {"ticker": "T", "resp": {"error": "x"}}),
        (kalshi_book, {"ticker": "T", "resp": {"orderbook_fp": {"yes_dollars": []}}}),
    ],
)
def test_parsers_raise_on_unknown_shapes(parse, msg):
    with pytest.raises((KeyError, ValueError)):
        list(parse(msg))


def test_kalshi_empty_book_is_valid():
    rows = list(
        kalshi_book(
            {"ticker": "T", "resp": {"orderbook_fp": {"yes_dollars": [], "no_dollars": []}}}
        )
    )
    assert [r.kind for r in rows] == ["reset"]


# ---- field accounting --------------------------------------------------------------------


def _write_trade(root, extra=None):
    m = {
        "count_fp": "1.00",
        "created_time": "2025-10-09T08:00:00Z",
        "is_block_trade": False,
        "no_price_dollars": "0.40",
        "taker_book_side": "bid",
        "taker_outcome_side": "yes",
        "taker_side": "yes",
        "ticker": "T",
        "trade_id": "1",
        "yes_price_dollars": "0.60",
        **(extra or {}),
    }
    w = RawWriter(root)
    w.write("kalshi", "trades", json.dumps(m), ts_ns=T0 + 1)
    w.close()


def test_field_check_passes_when_everything_is_declared(tmp_path):
    _write_trade(tmp_path)
    problems, report = fields.check([tmp_path])
    assert "undeclared" not in " ".join(problems)
    assert any("kalshi/trades" in r for r in report)


def test_field_check_flags_a_new_field_and_a_block_trade(tmp_path):
    _write_trade(tmp_path, {"surprise": 1, "is_block_trade": True})
    problems, _ = fields.check([tmp_path])
    assert any("undeclared field 'surprise'" in p for p in problems)
    assert any("block trade" in p for p in problems)


def test_field_check_flags_an_undeclared_channel(tmp_path):
    w = RawWriter(tmp_path)
    w.write("polymarket", "best_bid_ask", '{"event_type":"best_bid_ask"}', ts_ns=T0 + 1)
    w.close()
    problems, _ = fields.check([tmp_path])
    assert any("polymarket/best_bid_ask" in p for p in problems)
