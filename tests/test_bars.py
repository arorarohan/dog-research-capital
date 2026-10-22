import polars as pl

from drc.market.bars import make_bars
from drc.market.replay import TOPS_SCHEMA
from drc.normalize.rows import TRADES_SCHEMA

S = 1_000_000_000


def _tops(rows):
    cols = list(TOPS_SCHEMA)
    return pl.DataFrame([dict(zip(cols, r, strict=True)) for r in rows], schema=TOPS_SCHEMA)


def _trades(rows):
    cols = list(TRADES_SCHEMA)
    return pl.DataFrame([dict(zip(cols, r, strict=True)) for r in rows], schema=TRADES_SCHEMA)


def top(epoch, ts_exch, bid, ask, bsz=1.0, asz=1.0):
    return ("v", "S", epoch, ts_exch, ts_exch, bid, bsz, ask, asz, bsz, asz, False)


def trade(epoch, ts, price, size, side):
    return ("v", "S", epoch, ts, ts, None, price, size, side, False)


def test_last_state_wins_and_quiet_seconds_forward_fill_within_epoch():
    tops = _tops(
        [top(1, 0 * S + 5, 9.0, 10.0), top(1, 0 * S + 9, 9.5, 10.5), top(1, 3 * S, 9.0, 11.0)]
    )
    bars = make_bars(tops, _trades([])).sort("ts")
    assert bars["ts"].to_list() == [0, S, 2 * S, 3 * S]
    assert bars["bid"].to_list() == [9.5, 9.5, 9.5, 9.0]
    assert bars["n_book"].to_list() == [2, 0, 0, 1]
    assert bars["mid"].to_list()[0] == 10.0 and bars["spread"].to_list()[0] == 1.0


def test_epochs_are_never_bridged():
    tops = _tops([top(1, 0, 9.0, 10.0), top(2, 5 * S, 20.0, 21.0)])
    bars = make_bars(tops, _trades([]))
    e2 = bars.filter(pl.col("epoch") == 2)
    assert e2["ts"].to_list() == [5 * S] and e2["bid"].to_list() == [20.0]
    assert bars.filter(pl.col("epoch") == 1)["ts"].to_list() == [0]  # no extension to t=5s


def test_trade_flow_aggregation_and_buy_sell_split():
    tops = _tops([top(1, 0, 9.0, 10.0)])
    trades = _trades(
        [
            trade(1, 100, 10.0, 1.0, "buy"),
            trade(1, 200, 12.0, 3.0, "sell"),
        ]
    )
    (bar,) = make_bars(tops, trades).to_dicts()
    assert bar["n_trades"] == 2 and bar["volume"] == 4.0
    assert bar["buy_volume"] == 1.0 and bar["sell_volume"] == 3.0
    assert bar["vwap"] == (10.0 * 1 + 12.0 * 3) / 4 and bar["last_price"] == 12.0


def test_intervals_before_first_book_state_have_null_book():
    tops = _tops([top(1, 2 * S, 9.0, 10.0)])
    trades = _trades([trade(1, 0, 9.5, 1.0, "buy")])
    bars = make_bars(tops, trades).sort("ts")
    assert bars["bid"].to_list() == [None, None, 9.0]
    assert bars["n_trades"].to_list() == [1, 0, 0]


def test_trade_only_symbols_are_omitted():
    tops = _tops([top(1, 0, 9.0, 10.0)])
    other = ("v", "OTHER", 1, 0, 0, None, 5.0, 1.0, "buy", False)
    bars = make_bars(tops, _trades([trade(1, 100, 9.5, 1.0, "buy"), other]))
    assert bars["symbol"].unique().to_list() == ["S"]
