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


# ---- general markets ----

from drc.backfill.polymarket import (  # noqa: E402
    fetch_general_markets,
    fetch_history,
    general_market_row,
    sample_general,
)


def gm(cond, vol=1e6, prices=("1", "0"), outcomes=("Yes", "No"), closed=True, slug="will-x"):
    return {
        "conditionId": cond, "question": f"Q {cond}", "slug": slug, "closed": closed,
        "outcomes": orjson.dumps(list(outcomes)).decode(),
        "clobTokenIds": orjson.dumps([f"{cond}-yes", f"{cond}-no"]).decode(),
        "outcomePrices": orjson.dumps(list(prices)).decode(), "volumeNum": vol,
        "startDate": "2024-01-01T00:00:00Z", "endDate": "2024-03-01T00:00:00Z",
        "closedTime": "2024-02-20 12:00:00+00", "category": "Politics", "description": "r",
        "events": [{"id": 77}],
    }  # fmt: skip


def test_general_row_filters_and_maps():
    r = general_market_row(gm("c1"))
    assert r["token_id"] == "c1-yes" and r["result_value"] == 1.0 and r["event_id"] == "77"
    assert r["close_ts"] == 1708430400 * 10**9  # closedTime, not endDate
    assert general_market_row(gm("c", prices=("0", "1")))["result_value"] == 0.0
    assert general_market_row(gm("c", prices=("0.5", "0.5"))) is None  # void
    assert general_market_row(gm("c", outcomes=("Up", "Down"))) is None
    assert general_market_row(gm("c", closed=False)) is None
    assert general_market_row(gm("c", slug="btc-updown-15m-1")) is None


def test_fetch_general_queries_scheduled_window_and_follows_cursor():
    pages = {
        None: {"markets": [gm("a", 9e6), gm("b", 8e6, prices=("0.5", "0.5"))], "next_cursor": "p2"},
        "p2": {"markets": [gm("c", 7e6)], "next_cursor": None},
    }
    seen = []

    def handler(req):
        seen.append(dict(req.url.params))
        return httpx.Response(200, json=pages[req.url.params.get("after_cursor")])

    c = Client(min_interval=0, transport=httpx.MockTransport(handler))
    df = fetch_general_markets(c, "2024-01-01T00:00:00Z", "2024-06-01T00:00:00Z", 1000)
    assert sorted(df["market_id"].to_list()) == ["a", "c"]  # b void
    assert set(df["series"]) == {"general-sched"}
    assert df["end_ts"][0] == 1709251200 * 10**9  # scheduled endDate, not closedTime
    q = seen[0]
    assert q["order"] == "endDate" and q["end_date_min"].startswith("2024-01-01")
    assert q["end_date_max"].startswith("2024-06-01") and q["volume_num_min"] == "1000"


def test_scheduled_anchor_skips_moments_the_market_was_not_open():
    day = 86400
    end = 1709251200  # scheduled end
    m = {
        "market_id": "m", "open_ts": (end - 10 * day) * 10**9,
        "close_ts": (end - 2 * day) * 10**9, "end_ts": end * 10**9,
    }  # fmt: skip
    hist = [
        {"t": end - 8 * day, "p": 0.3},
        {"t": end - 4 * day, "p": 0.4},
        {"t": end - 2 * day - 10, "p": 0.95},
    ]
    got = samples_from_history(
        hist, m, [20 * day, 7 * day, 3 * day, 1 * day], lookback=2 * day, anchor="end_ts"
    )
    # 20d is before the market opened; 1d is after it closed early: neither was tradable then
    assert sorted(g["secs_to_close"] for g in got) == [3 * day, 7 * day]
    assert {g["secs_to_close"]: g["price"] for g in got} == {7 * day: 0.3, 3 * day: 0.4}


def test_history_fidelity_ladder_and_general_sampling(tmp_path):
    def handler(req):
        if req.url.path.endswith("/markets/keyset"):
            return httpx.Response(200, json={"markets": []})
        fid = int(req.url.params["fidelity"])
        close = 1708430400
        pts = [{"t": close - 3 * 86400 - 100, "p": 0.7}, {"t": close - 3600, "p": 0.9}]
        return httpx.Response(200, json={"history": pts if fid >= 720 else []})

    c = Client(min_interval=0, transport=httpx.MockTransport(handler))
    hist, fid = fetch_history(c, "tok")
    assert fid == 720 and len(hist) == 2
    df = pl.DataFrame([general_market_row(gm("a"))])
    sample_general(c, tmp_path, df, [3 * 86400, 3600], anchor="close_ts")
    got = pl.read_parquet(list((tmp_path / "price_samples").rglob("*.parquet"))).sort(
        "secs_to_close"
    )
    assert got["price"].to_list() == [0.9, 0.7]
