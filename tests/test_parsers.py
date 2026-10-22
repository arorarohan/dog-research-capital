from drc.normalize import parsers as P
from drc.normalize.rows import BookRow, Trade
from drc.normalize.timeutil import iso_to_ns


def test_iso_to_ns_handles_micro_and_nano():
    assert iso_to_ns("1970-01-01T00:00:01.5Z") == 1_500_000_000
    assert iso_to_ns("1970-01-01T00:00:01.123456789Z") == 1_123_456_789
    assert iso_to_ns("1970-01-01T00:00:02Z") == 2_000_000_000


def test_coinbase_snapshot_emits_reset_then_levels_and_offer_is_ask():
    msg = {
        "timestamp": "1970-01-01T00:00:01Z",
        "events": [
            {
                "type": "snapshot",
                "product_id": "BTC-USD",
                "updates": [
                    {
                        "side": "bid",
                        "event_time": "1970-01-01T00:00:01Z",
                        "price_level": "9",
                        "new_quantity": "2",
                    },
                    {
                        "side": "offer",
                        "event_time": "1970-01-01T00:00:01Z",
                        "price_level": "10",
                        "new_quantity": "3",
                    },
                ],
            }
        ],
    }
    rows = list(P.coinbase_l2(msg))
    assert rows[0] == BookRow("BTC-USD", 1_000_000_000, "reset", None, None, None)
    assert [(r.kind, r.side, r.price, r.size) for r in rows[1:]] == [
        ("snapshot", "bid", 9.0, 2.0),
        ("snapshot", "ask", 10.0, 3.0),
    ]


def test_coinbase_update_has_no_reset_and_zero_size_removes():
    msg = {
        "timestamp": "1970-01-01T00:00:01Z",
        "events": [
            {
                "type": "update",
                "product_id": "ETH-USD",
                "updates": [
                    {
                        "side": "bid",
                        "event_time": "1970-01-01T00:00:01Z",
                        "price_level": "5",
                        "new_quantity": "0",
                    }
                ],
            }
        ],
    }
    (row,) = P.coinbase_l2(msg)
    assert (row.kind, row.size) == ("update", 0.0)


def test_coinbase_trades_snapshot_marked_replay():
    msg = {
        "events": [
            {
                "type": "snapshot",
                "trades": [
                    {
                        "product_id": "BTC-USD",
                        "trade_id": "7",
                        "price": "1.5",
                        "size": "2",
                        "time": "1970-01-01T00:00:01Z",
                        "side": "SELL",
                    }
                ],
            }
        ]
    }
    (t,) = P.coinbase_trades(msg)
    assert t == Trade("BTC-USD", "7", 1_000_000_000, 1.5, 2.0, "sell", True)


def test_kraken_book_symbol_normalized_and_qty_zero_kept():
    msg = {
        "type": "update",
        "data": [
            {
                "symbol": "BTC/USD",
                "bids": [],
                "asks": [{"price": 10.0, "qty": 0.0}],
                "timestamp": "1970-01-01T00:00:01Z",
            }
        ],
    }
    (row,) = P.kraken_book(msg)
    assert row == BookRow("BTC-USD", 1_000_000_000, "update", "ask", 10.0, 0.0)


def test_kraken_snapshot_without_timestamp():
    msg = {
        "type": "snapshot",
        "data": [{"symbol": "BTC/USD", "bids": [{"price": 1.0, "qty": 2.0}], "asks": []}],
    }
    rows = list(P.kraken_book(msg))
    assert [r.kind for r in rows] == ["reset", "snapshot"]
    assert rows[0].ts_exch is None


def test_polymarket_book_list_and_price_change_sides():
    book = [
        {"asset_id": "A", "timestamp": "1500", "bids": [{"price": "0.4", "size": "10"}], "asks": []}
    ]
    rows = list(P.polymarket_book(book))
    assert rows[0].kind == "reset" and rows[1] == BookRow(
        "A", 1_500_000_000, "snapshot", "bid", 0.4, 10.0
    )

    pc = {
        "timestamp": "2000",
        "price_changes": [
            {"asset_id": "A", "price": "0.41", "size": "0", "side": "BUY"},
            {"asset_id": "A", "price": "0.43", "size": "7", "side": "SELL"},
        ],
    }
    rows = list(P.polymarket_price_change(pc))
    assert [(r.side, r.price, r.size) for r in rows] == [("bid", 0.41, 0.0), ("ask", 0.43, 7.0)]


def test_kalshi_no_bids_become_yes_asks():
    msg = {
        "ticker": "T",
        "resp": {
            "orderbook_fp": {"yes_dollars": [["0.40", "5.00"]], "no_dollars": [["0.55", "9.00"]]}
        },
    }
    rows = list(P.kalshi_book(msg))
    assert rows[0].kind == "reset"
    assert [(r.side, r.price, r.size) for r in rows[1:]] == [("bid", 0.40, 5.0), ("ask", 0.45, 9.0)]


def test_kalshi_empty_book_still_emits_reset():
    rows = list(
        P.kalshi_book(
            {"ticker": "T", "resp": {"orderbook_fp": {"yes_dollars": [], "no_dollars": []}}}
        )
    )
    assert [r.kind for r in rows] == ["reset"]


def test_kalshi_trade_side_is_yes_contract_taker():
    base = {
        "ticker": "T",
        "trade_id": "x",
        "created_time": "1970-01-01T00:00:01Z",
        "yes_price_dollars": "0.56",
        "count_fp": "100.00",
    }
    (buy,) = P.kalshi_trades({**base, "taker_side": "yes"})
    (sell,) = P.kalshi_trades({**base, "taker_side": "no"})
    assert (buy.side, sell.side, buy.price, buy.size) == ("buy", "sell", 0.56, 100.0)
