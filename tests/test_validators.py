"""The ground-truth validators must catch the failures they exist for (#20)."""

import json

from drc.recorder.writer import NS_PER_HOUR, RawWriter
from drc.validate import continuity, kalshi, polymarket, timing

T0 = 1_760_000_000 * 1_000_000_000
T0 -= T0 % NS_PER_HOUR
S = 1_000_000_000


def _cb(seq, channel="l2_data", events=None):
    return json.dumps({"channel": channel, "sequence_num": seq, "events": events or []})


def test_coinbase_sequence_is_checked_across_channels_and_epochs(tmp_path):
    w = RawWriter(tmp_path)
    w.event("coinbase", "connect", ts_ns=T0)
    w.write("coinbase", "l2_data", _cb(0), ts_ns=T0 + 1)
    w.write("coinbase", "market_trades", _cb(1, "market_trades"), ts_ns=T0 + 2)  # shared counter
    w.write("coinbase", "l2_data", _cb(4), ts_ns=T0 + 3)  # 2 and 3 never arrived
    w.event("coinbase", "disconnect", ts_ns=T0 + 4)
    w.event("coinbase", "connect", ts_ns=T0 + 5)
    w.write("coinbase", "l2_data", _cb(0), ts_ns=T0 + 6)  # new connection restarts the counter
    w.close()
    r = continuity.coinbase_sequence(tmp_path)
    assert (r.messages, r.gaps, r.missing) == (4, 1, 2)


def _trade_msg(ids):
    return json.dumps(
        {
            "channel": "trade",
            "type": "update",
            "data": [{"symbol": "BTC/USD", "trade_id": i, "side": "buy"} for i in ids],
        }
    )


def test_trade_id_gaps_are_explained_only_by_a_recorded_reconnect(tmp_path):
    w = RawWriter(tmp_path)
    w.event("kraken", "connect", ts_ns=T0)
    w.write("kraken", "trade", _trade_msg([1, 2, 3]), ts_ns=T0 + 10)
    w.write("kraken", "trade", _trade_msg([6]), ts_ns=T0 + 20)  # 4, 5 lost with no reconnect
    w.event("kraken", "disconnect", ts_ns=T0 + 30)
    w.event("kraken", "connect", ts_ns=T0 + 40)
    w.write("kraken", "trade", _trade_msg([10]), ts_ns=T0 + 50)  # 7..9 lost across the reconnect
    w.close()
    r = continuity.trade_id_continuity(tmp_path, "kraken")
    assert (r.missing, r.explained, r.unexplained) == (5, 3, 2)


def test_timing_finds_backward_steps_skew_and_unexplained_gaps(tmp_path):
    w = RawWriter(tmp_path)
    w.event("kraken", "connect", ts_ns=T0)
    book = (
        '{"channel":"book","type":"update","data":[{"symbol":"BTC/USD","bids":[{"price":1,"qty":1}],"asks":[],'
        '"timestamp":"%s"}]}'
    )
    exch = "1970-01-01T00:00:00Z"  # exchange stamp: epoch 0, so skew is just our clock
    for t in (T0 + 1 * S, T0 + 2 * S, T0 + 1 * S, T0 + 9 * S):  # one backward step, one 8 s gap
        w.write("kraken", "book", book % exch, ts_ns=t)
    w.close()
    assert timing.backward_steps(tmp_path)[("kraken", "book")] == (1, 4)
    g = timing.gaps(tmp_path, "kraken", "book", min_gap_s=5)
    assert g.gaps == 1 and g.explained == 0 and len(g.unexplained) == 1
    assert 7.9 < g.longest_s < 8.1
    assert timing.skew_ms(tmp_path)[("kraken", "book")]["n"] == 4


def test_a_gap_across_a_recorded_disconnect_is_explained(tmp_path):
    w = RawWriter(tmp_path)
    w.event("kraken", "connect", ts_ns=T0)
    msg = '{"channel":"book","type":"update","data":[{"symbol":"BTC/USD","bids":[],"asks":[]}]}'
    w.write("kraken", "book", msg, ts_ns=T0 + 1 * S)
    w.event("kraken", "disconnect", ts_ns=T0 + 2 * S)
    w.event("kraken", "connect", ts_ns=T0 + 8 * S)
    w.write("kraken", "book", msg, ts_ns=T0 + 9 * S)
    w.close()
    g = timing.gaps(tmp_path, "kraken", "book", min_gap_s=5)
    assert (g.gaps, g.explained, g.unexplained) == (1, 1, [])


# ---- polymarket book vs exchange best bid/ask --------------------------------------------


def _pm_book(asset, bids, asks, ts="1000"):
    return json.dumps(
        {
            "market": "m",
            "asset_id": asset,
            "timestamp": ts,
            "bids": [{"price": p, "size": s} for p, s in bids],
            "asks": [{"price": p, "size": s} for p, s in asks],
            "event_type": "book",
        }
    )


def _pm_change(asset, price, size, side, best_bid, best_ask):
    return json.dumps(
        {
            "market": "m",
            "timestamp": "2000",
            "event_type": "price_change",
            "price_changes": [
                {
                    "asset_id": asset,
                    "price": price,
                    "size": size,
                    "side": side,
                    "hash": "h",
                    "best_bid": best_bid,
                    "best_ask": best_ask,
                }
            ],
        }
    )


def test_polymarket_replay_matches_exchange_best_and_flags_a_missed_update(tmp_path):
    w = RawWriter(tmp_path)
    w.event("polymarket", "connect", ts_ns=T0)
    w.write("polymarket", "book", _pm_book("A", [("0.40", "10")], [("0.60", "10")]), ts_ns=T0 + 1)
    # a new best bid at 0.45: replay agrees with the exchange
    w.write(
        "polymarket",
        "price_change",
        _pm_change("A", "0.45", "5", "BUY", "0.45", "0.60"),
        ts_ns=T0 + 2,
    )
    # the exchange says best ask is now 0.55 but the message that created it was never received
    w.write(
        "polymarket",
        "price_change",
        _pm_change("A", "0.45", "6", "BUY", "0.45", "0.55"),
        ts_ns=T0 + 3,
    )
    # a full snapshot that disagrees with the replayed levels
    w.write("polymarket", "book", _pm_book("A", [("0.45", "6")], [("0.55", "3")]), ts_ns=T0 + 4)
    w.close()
    r = polymarket.check(tmp_path)
    assert (r.top_items, r.top_match_msg) == (2, 1)
    assert (r.snapshots, r.snapshot_match) == (1, 0)


def test_polymarket_updates_before_a_snapshot_are_skipped_not_applied(tmp_path):
    w = RawWriter(tmp_path)
    w.event("polymarket", "connect", ts_ns=T0)
    w.write(
        "polymarket", "price_change", _pm_change("A", "0.45", "5", "BUY", "0.45", "1"), ts_ns=T0 + 1
    )
    w.close()
    r = polymarket.check(tmp_path)
    assert (r.top_items, r.top_skipped_unsynced) == (0, 1)


# ---- kalshi trades vs REST ---------------------------------------------------------------


def _ktrade(i, created="2025-10-09T08:00:%02dZ"):
    return {
        "trade_id": i,
        "ticker": "T",
        "created_time": created % int(i[-1]),
        "yes_price_dollars": "0.50",
        "count_fp": "1.00",
        "taker_side": "yes",
    }


def test_kalshi_check_counts_trades_rest_has_that_we_lack(tmp_path):
    w = RawWriter(tmp_path)
    for i in ("a1", "a2", "a3", "a4"):
        w.write("kalshi", "trades", json.dumps(_ktrade(i, "2025-10-09T08:00:0%sZ")), ts_ns=T0)
    w.close()
    # REST also has a5, which we missed, and shows a different price for a2
    rest = [_ktrade(i, "2025-10-09T08:00:0%sZ") for i in ("a1", "a2", "a3", "a4", "a5")]
    rest[1]["yes_price_dollars"] = "0.51"
    r = kalshi.check(tmp_path, n_tickers=2, fetch=lambda *_: rest, margin_s=0)
    assert (r.missing_from_raw, r.not_in_rest, r.field_mismatch) == (1, 0, 1)
