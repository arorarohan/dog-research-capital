import datetime as dt
import io
import zipfile

from drc.backfill.binance import _months, _unzip_csv, parse_funding_csv, parse_klines_csv, to_ns


def test_timestamp_units_ms_and_us():
    assert to_ns(1_700_000_000_000) == 1_700_000_000_000_000_000  # ms
    assert to_ns(1_700_000_000_000_000) == 1_700_000_000_000_000_000  # us (2025 onward)


def test_parse_klines_handles_us_timestamps_and_columns():
    row = "1735689600000000,100.0,101.0,99.0,100.5,12.5,1735689659999999,1250.0,42,6.0,600.0,0\n"
    row2 = "1735689660000000,100.5,102.0,100.0,101.5,3.0,1735689719999999,300.0,7,1.0,100.0,0\n"
    df = parse_klines_csv((row + row2).encode(), "BTCUSDT")
    assert df.columns == [
        "symbol",
        "ts",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "n_trades",
        "taker_buy_volume",
    ]
    assert df["ts"].to_list() == [1735689600 * 10**9, 1735689660 * 10**9]
    assert df["close"].to_list() == [100.5, 101.5] and df["n_trades"].to_list() == [42, 7]


def test_parse_klines_skips_a_header_row():
    hdr = (
        "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
        "taker_buy_volume,taker_buy_quote_volume,ignore\n"
    )
    row = "1700000000000,1,2,0.5,1.5,10,1700000059999,15,3,4,6,0\n"
    df = parse_klines_csv((hdr + row).encode(), "X")
    assert df.height == 1 and df["ts"][0] == 1_700_000_000 * 10**9


def test_parse_funding():
    csv = (
        "calc_time,funding_interval_hours,last_funding_rate\n"
        "1700000000000,8,0.0001\n1700028800000,8,-0.00005\n"
    )
    df = parse_funding_csv(csv.encode(), "BTCUSDT")
    assert df["ts"].to_list() == [1_700_000_000 * 10**9, 1_700_028_800 * 10**9]
    assert df["rate"].to_list() == [0.0001, -0.00005]


def test_unzip_and_month_range():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("x.csv", "a,b\n1,2\n")
    assert _unzip_csv(buf.getvalue()) == b"a,b\n1,2\n"
    assert list(_months(dt.date(2024, 11, 5), dt.date(2025, 2, 1))) == [
        (2024, 11),
        (2024, 12),
        (2025, 1),
        (2025, 2),
    ]
