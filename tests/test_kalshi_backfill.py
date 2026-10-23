import httpx
import polars as pl

from drc.backfill.http import Client
from drc.backfill.kalshi import fetch_markets, market_row, sample_prices, sample_series

CLOSE = "2026-10-22T19:30:00Z"
CLOSE_S = 1792697400


def mkt(ticker, result="yes", **kw):
    return {
        "ticker": ticker, "event_ticker": "KXBTC15M-26OCT221530", "title": "BTC up?",
        "open_time": "2026-10-22T19:15:00Z", "close_time": CLOSE, "result": result,
        "floor_strike": 81246.86, "strike_type": "greater_or_equal", "volume_fp": "100.00",
        "rules_primary": "rules", **kw,
    }  # fmt: skip


def test_market_row_maps_result_strike_and_series():
    r = market_row(mkt("T1"), "live")
    assert r["result_value"] == 1.0 and r["strike"] == 81246.86 and r["series"] == "KXBTC15M"
    assert r["close_ts"] == CLOSE_S * 10**9 and r["open_ts"] == (CLOSE_S - 900) * 10**9
    assert market_row(mkt("T2", result="no"), "live")["result_value"] == 0.0
    assert market_row(mkt("T3", result=""), "live")["result_value"] is None  # void / unresolved


def client_for(handler):
    return Client(base="https://x", min_interval=0, transport=httpx.MockTransport(handler))


def test_fetch_markets_reads_both_tiers_dedupes_and_paginates():
    calls = []

    def handler(req):
        calls.append(str(req.url))
        if req.url.path.endswith("/historical/markets"):
            return httpx.Response(200, json={"markets": [mkt("OLD")], "cursor": ""})
        if req.url.params.get("cursor") == "p2":
            return httpx.Response(200, json={"markets": [mkt("NEW2")], "cursor": ""})
        return httpx.Response(200, json={"markets": [mkt("NEW1"), mkt("OLD")], "cursor": "p2"})

    df = fetch_markets(client_for(handler), "KXBTC15M")
    assert sorted(df["market_id"].to_list()) == ["NEW1", "NEW2", "OLD"]
    assert set(df["tier"].to_list()) == {"historical", "live"}


def test_sample_prices_takes_latest_trade_in_window_and_skips_empty_windows():
    def handler(req):
        max_ts = int(req.url.params["max_ts"])
        if max_ts == CLOSE_S - 600:  # empty window
            return httpx.Response(200, json={"trades": [], "cursor": ""})
        trades = [
            {"created_time": "2026-10-22T19:20:00Z", "yes_price_dollars": "0.40"},
            {"created_time": "2026-10-22T19:20:30Z", "yes_price_dollars": "0.45"},
        ]
        return httpx.Response(200, json={"trades": trades, "cursor": ""})

    m = market_row(mkt("T"), "live")
    rows = sample_prices(client_for(handler), m, [840, 600, 300])
    assert [r["secs_to_close"] for r in rows] == [840, 300]
    assert rows[0]["price"] == 0.45 and rows[0]["n_window"] == 2


def test_sample_series_is_resumable(tmp_path):
    hits = []

    def handler(req):
        hits.append(req.url.params["ticker"])
        return httpx.Response(
            200,
            json={"trades": [{"created_time": "2026-10-22T19:20:00Z", "yes_price_dollars": "0.5"}]},
        )

    markets = pl.DataFrame([market_row(mkt(f"T{i}"), "live") for i in range(3)])
    c = client_for(handler)
    sample_series(c, tmp_path, markets, [300], chunk=2)
    first = len(hits)
    sample_series(c, tmp_path, markets, [300], chunk=2)
    assert len(hits) == first  # nothing refetched
    got = pl.read_parquet(list((tmp_path / "price_samples").rglob("*.parquet")))
    assert sorted(got["market_id"].to_list()) == ["T0", "T1", "T2"]


def test_sample_series_skips_untraded_and_thin_markets(tmp_path):
    hits = []

    def handler(req):
        hits.append(req.url.params["ticker"])
        return httpx.Response(200, json={"trades": []})

    vols = {"T0": "0.00", "T1": "50.00", "T2": "5000.00"}
    markets = pl.DataFrame([market_row(mkt(t, volume_fp=v), "live") for t, v in vols.items()])
    sample_series(client_for(handler), tmp_path, markets, [300], min_volume=1000)
    assert hits == ["T2"]


def test_sample_prices_pages_long_windows_and_honours_lookback():
    seen = []

    def handler(req):
        seen.append((int(req.url.params["min_ts"]), req.url.params.get("cursor")))
        if req.url.params.get("cursor") == "p2":
            trades = [{"created_time": "2026-10-22T19:29:00Z", "yes_price_dollars": "0.70"}]
            return httpx.Response(200, json={"trades": trades, "cursor": ""})
        trades = [{"created_time": "2026-10-22T19:10:00Z", "yes_price_dollars": "0.30"}]
        return httpx.Response(200, json={"trades": trades, "cursor": "p2"})

    m = market_row(mkt("T"), "live")
    (row,) = sample_prices(client_for(handler), m, [300], lookback=7200)
    assert row["price"] == 0.70 and row["n_window"] == 2  # latest trade is on page 2
    assert seen[0][0] == CLOSE_S - 300 - 7200 and seen[1][1] == "p2"
