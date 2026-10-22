import httpx
import orjson
import polars as pl

from drc.backfill.http import Client
from drc.backfill.polymarket import (
    fetch_updown_markets,
    market_row,
    sample_series,
    samples_from_history,
    slugs,
)

START = 1792693800  # aligned to 15 minutes


def event(start=START, closed=True, prices=("1", "0")):
    return {
        "slug": f"btc-updown-15m-{start}", "title": "Bitcoin Up or Down", "closed": closed,
        "markets": [{
            "conditionId": f"0xc{start}", "outcomes": orjson.dumps(["Up", "Down"]).decode(),
            "clobTokenIds": orjson.dumps(["tokUp", "tokDown"]).decode(),
            "outcomePrices": orjson.dumps(list(prices)).decode(), "volumeNum": 123.0,
            "description": "rules",
        }],
    }  # fmt: skip


def test_slugs_align_to_the_interval():
    got = slugs("btc", "15m", START + 100, START + 1900)
    assert got == [
        (f"btc-updown-15m-{START}", START),
        (f"btc-updown-15m-{START + 900}", START + 900),
        (f"btc-updown-15m-{START + 1800}", START + 1800),
    ]


def test_market_row_resolution_cases():
    r = market_row(event(), "btc-updown-15m", START, "15m")
    assert (
        r["result_value"] == 1.0
        and r["token_id"] == "tokUp"
        and r["close_ts"] == (START + 900) * 10**9
    )
    assert market_row(event(prices=("0", "1")), "s", START, "15m")["result_value"] == 0.0
    assert market_row(event(prices=("0.5", "0.5")), "s", START, "15m")["result_value"] is None
    assert market_row(event(closed=False), "s", START, "15m")["result_value"] is None
    assert market_row({"markets": []}, "s", START, "15m") is None


def test_samples_use_last_point_in_window_before_target():
    m = market_row(event(), "s", START, "15m")
    close = START + 900
    hist = [
        {"t": close - 700, "p": 0.40},
        {"t": close - 610, "p": 0.55},
        {"t": close - 30, "p": 0.9},
    ]
    rows = samples_from_history(hist, m, [600, 300, 30])
    assert [(r["secs_to_close"], r["price"], r["n_window"]) for r in rows] == [
        (600, 0.55, 2),
        (30, 0.9, 1),
    ]


def test_fetch_and_sample_end_to_end_with_resume(tmp_path):
    calls = {"prices": 0}

    def handler(req):
        if req.url.path.endswith("/events"):
            slug = req.url.params["slug"]
            start = int(slug.rsplit("-", 1)[1])
            return httpx.Response(200, json=[event(start)] if start != START + 900 else [])
        calls["prices"] += 1
        close = int(req.url.params["endTs"])
        return httpx.Response(200, json={"history": [{"t": close - 40, "p": 0.8}]})

    c = Client(min_interval=0, transport=httpx.MockTransport(handler))
    df = fetch_updown_markets(c, "btc", "15m", START, START + 2700)
    assert df.height == 2  # the missing event is skipped
    sample_series(c, tmp_path, df, [30])
    sample_series(c, tmp_path, df, [30])
    assert calls["prices"] == 2  # resumable: second pass fetched nothing
    got = pl.read_parquet(list((tmp_path / "price_samples").rglob("*.parquet")))
    assert got["price"].to_list() == [0.8, 0.8]
