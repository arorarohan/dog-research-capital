from decimal import Decimal as D

from sortedcontainers import SortedDict

from drc.validate.kraken import book_checksum, check_messages


def _lv(p, q):
    return {"price": D(p), "qty": D(q)}


def test_checksum_uses_top10_asks_then_bids_without_dots_or_leading_zeros():
    bids = SortedDict({D("9.5"): D("0.10000000")})
    asks = SortedDict({D("10.0"): D("2.50000000")})
    # asks first: "100" + "250000000", then bids: "95" + "10000000"
    import zlib

    assert book_checksum(bids, asks) == zlib.crc32(b"10025000000095" + b"10000000")


def test_check_messages_counts_matches_and_mismatches_and_trims_depth():
    snap = {
        "type": "snapshot",
        "data": [{"symbol": "X", "bids": [_lv("9", "1"), _lv("8", "1")], "asks": [_lv("10", "1")]}],
    }
    b, a = SortedDict({D("9"): D("1"), D("8"): D("1")}), SortedDict({D("10"): D("1")})
    good = book_checksum(b, a)
    upd = {
        "type": "update",
        "data": [{"symbol": "X", "bids": [], "asks": [_lv("11", "2")], "checksum": 0}],
    }
    assert check_messages([snap, upd], "X") == (0, 1)  # wrong checksum -> mismatch
    # after update asks are {10, 11}; build the matching checksum and expect a match
    a[D("11")] = D("2")
    upd["data"][0]["checksum"] = book_checksum(b, a)
    assert check_messages([snap, upd], "X") == (1, 0)
    assert good != upd["data"][0]["checksum"]
