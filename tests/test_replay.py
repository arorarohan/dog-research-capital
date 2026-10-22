import polars as pl

from drc.market.book import Book
from drc.market.replay import replay_tops
from drc.normalize.rows import BOOK_EVENTS_SCHEMA

COLS = ["venue", "symbol", "epoch", "ts_recv", "msg_id", "ts_exch", "kind", "side", "price", "size"]


def _events(rows):
    full = [("v", "S") + r[:3] + (None,) + r[3:] for r in rows]
    return pl.DataFrame(full, schema=BOOK_EVENTS_SCHEMA, orient="row").select(COLS)


def test_book_levels_best_and_depth():
    b = Book()
    for side, p, s in [("bid", 9, 1), ("bid", 8, 2), ("ask", 10, 3), ("ask", 11, 4)]:
        b.set_level(side, p, s)
    assert b.best_bid() == (9, 1) and b.best_ask() == (10, 3)
    assert b.depth("bid", 2) == 3 and b.depth("ask", 1) == 3
    b.set_level("bid", 9, 0)
    assert b.best_bid() == (8, 2)
    assert not b.crossed()
    b.set_level("bid", 12, 1)
    assert b.crossed()


def test_replay_snapshot_updates_and_unchanged_states_dropped():
    ev = _events(
        [
            # epoch, ts, msg_id, kind, side, price, size
            (1, 100, 1, "reset", None, None, None),
            (1, 100, 1, "snapshot", "bid", 9.0, 1.0),
            (1, 100, 1, "snapshot", "ask", 10.0, 1.0),
            (1, 200, 2, "update", "bid", 8.0, 5.0),  # below the top: depth changes, top same
            (1, 300, 3, "update", "ask", 10.0, 0.0),  # best ask removed -> ask None
        ]
    )
    tops, stats = replay_tops(ev)
    assert tops["ts_recv"].to_list() == [100, 200, 300]
    assert tops["bid_depth"].to_list() == [1.0, 6.0, 6.0]
    assert tops["ask"].to_list() == [10.0, 10.0, None]
    assert stats.messages == 3 and stats.dropped_unsynced == 0


def test_new_epoch_wipes_books_and_unsynced_updates_are_dropped():
    ev = _events(
        [
            (1, 100, 1, "reset", None, None, None),
            (1, 100, 1, "snapshot", "bid", 9.0, 1.0),
            (2, 500, 2, "update", "bid", 9.5, 1.0),  # new epoch, no snapshot yet: must be dropped
            (2, 600, 3, "reset", None, None, None),
            (2, 600, 3, "snapshot", "ask", 10.0, 2.0),
        ]
    )
    tops, stats = replay_tops(ev)
    assert stats.dropped_unsynced == 1
    last = tops.row(-1, named=True)
    assert last["epoch"] == 2 and last["bid"] is None and last["ask"] == 10.0


def test_depth_limited_venue_is_trimmed_to_subscribed_depth():
    ev = _events(
        [
            (1, 100, 1, "reset", None, None, None),
            (1, 100, 1, "snapshot", "bid", 9.0, 1.0),
            (1, 100, 1, "snapshot", "bid", 8.0, 1.0),
            (1, 200, 2, "update", "bid", 10.0, 1.0),  # pushes 8.0 out of a depth-2 window
        ]
    )
    tops, _ = replay_tops(ev, max_levels={"v": 2})
    assert tops.row(-1, named=True)["bid_depth"] == 2.0  # only 10.0 and 9.0 remain
    tops, _ = replay_tops(ev, max_levels={})
    assert tops.row(-1, named=True)["bid_depth"] == 3.0
