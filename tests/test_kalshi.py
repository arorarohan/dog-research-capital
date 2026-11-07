import httpx

from drc.recorder.kalshi import KalshiPoller
from drc.recorder.writer import RawWriter, read_lines


def _mock_client():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/markets") and "trades" not in path:
            return httpx.Response(
                200,
                json={
                    "cursor": "",
                    "markets": [
                        {"ticker": "LOW", "volume_24h_fp": "5.00"},
                        {"ticker": "HIGH", "volume_24h_fp": "900.00"},
                        {"ticker": "MID", "volume_24h_fp": "50.00"},
                    ],
                },
            )
        if path.endswith("/orderbook"):
            return httpx.Response(200, json={"orderbook_fp": {"yes_dollars": [], "no_dollars": []}})
        if path.endswith("/trades"):
            return httpx.Response(
                200, json={"cursor": "", "trades": [{"trade_id": "a", "ticker": "HIGH"}]}
            )
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _all(root, venue, channel):
    out = []
    for p in sorted((root / venue / channel).rglob("*.jsonl*")):
        out += [r["m"] for r in read_lines(p)]
    return out


async def test_catalog_ranks_by_volume_and_books_are_ticker_wrapped(tmp_path):
    w = RawWriter(tmp_path)
    poller = KalshiPoller(top_n=2)
    async with _mock_client() as client:
        await poller.refresh_catalog(client, w)
        assert poller.tickers == ["HIGH", "MID"]
        await poller.poll_book(client, w, "HIGH")
    w.close()
    (rec,) = _all(tmp_path, "kalshi", "orderbook")
    assert rec == {
        "ticker": "HIGH",
        "resp": {"orderbook_fp": {"yes_dollars": [], "no_dollars": []}},
    }
    (catalog,) = _all(tmp_path, "kalshi", "_catalog")
    assert [m["ticker"] for m in catalog] == ["HIGH", "MID"]


async def test_trades_are_deduplicated_across_polls(tmp_path):
    w = RawWriter(tmp_path)
    poller = KalshiPoller()
    async with _mock_client() as client:
        await poller.poll_trades(client, w)
        await poller.poll_trades(client, w)  # same trade returned again
    w.close()
    assert [t["trade_id"] for t in _all(tmp_path, "kalshi", "trades")] == ["a"]


async def test_parlay_trades_are_excluded(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        trades = [
            {"trade_id": "p", "ticker": "KXMVECROSSCATEGORY-X"},
            {"trade_id": "n", "ticker": "KXBTC15M-Y"},
        ]
        return httpx.Response(200, json={"cursor": "", "trades": trades})

    w = RawWriter(tmp_path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await KalshiPoller().poll_trades(client, w)
    w.close()
    assert [t["trade_id"] for t in _all(tmp_path, "kalshi", "trades")] == ["n"]


async def test_series_mode_tracks_every_open_market_of_those_series_only(tmp_path):
    asked = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/trades"):
            trades = [
                {"trade_id": "w", "ticker": "KXHIGHNY-26OCT25-B70.5"},
                {"trade_id": "o", "ticker": "KXBTC15M-X"},
                {"trade_id": "x", "ticker": "KXHIGHNYC-LOOKALIKE"},
            ]
            return httpx.Response(200, json={"cursor": "", "trades": trades})
        series = request.url.params["series_ticker"]
        asked.append((series, request.url.params["status"]))
        return httpx.Response(
            200,
            json={"cursor": "", "markets": [{"ticker": f"{series}-26OCT25-T{i}"} for i in (1, 2)]},
        )

    w = RawWriter(tmp_path)
    poller = KalshiPoller(top_n=1, series=("KXHIGHNY", "KXHIGHCHI"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await poller.refresh_catalog(client, w)
        await poller.poll_trades(client, w)
    w.close()
    assert asked == [("KXHIGHNY", "open"), ("KXHIGHCHI", "open")]
    assert len(poller.tickers) == 4  # top_n is ignored in series mode
    assert [t["trade_id"] for t in _all(tmp_path, "kalshi", "trades")] == ["w"]


def _fake_time(poller):
    """Replace the poller's clock and sleep so tests are instant and exact."""
    state = {"now": 100.0, "sleeps": []}

    async def sleep(d):
        state["sleeps"].append(d)
        state["now"] += d

    poller._now = lambda: state["now"]
    poller._sleep = sleep
    return state


async def test_requests_are_paced_to_max_rps():
    poller = KalshiPoller(max_rps=10)
    state = _fake_time(poller)
    for _ in range(4):
        await poller._pace()  # each call waits for its slot; the fake clock advances by that wait
    assert [round(d, 3) for d in state["sleeps"]] == [0.1, 0.1, 0.1]


def _throttling_client(failures, retry_after="2"):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= failures:
            return httpx.Response(429, headers={"retry-after": retry_after})
        return httpx.Response(200, json={"orderbook_fp": {"yes_dollars": [], "no_dollars": []}})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls


async def test_a_429_is_retried_after_retry_after_and_data_is_kept(tmp_path):
    w = RawWriter(tmp_path)
    poller = KalshiPoller()
    state = _fake_time(poller)
    client, calls = _throttling_client(failures=1)
    async with client:
        await poller.poll_book(client, w, "HIGH")
    w.close()
    assert calls["n"] == 2 and poller.n_throttled == 1
    assert len(_all(tmp_path, "kalshi", "orderbook")) == 1  # the book was not lost
    assert any(2.0 <= d <= 2.5 for d in state["sleeps"])  # Retry-After honored, plus small jitter


async def test_a_huge_retry_after_is_clamped(tmp_path):
    w = RawWriter(tmp_path)
    poller = KalshiPoller(max_retry_wait=60.0)
    state = _fake_time(poller)
    client, calls = _throttling_client(failures=1, retry_after="3600")
    async with client:
        await poller.poll_book(client, w, "HIGH")
    w.close()
    assert calls["n"] == 2
    assert len(_all(tmp_path, "kalshi", "orderbook")) == 1
    assert max(state["sleeps"]) <= 60.0 * 1.25


async def test_a_429_pauses_every_task_not_just_the_one_that_hit_it():
    poller = KalshiPoller(max_rps=10)
    _fake_time(poller)
    client, _ = _throttling_client(failures=1, retry_after="3")
    async with client:
        resp = await poller._get(client, "https://x/markets/T/orderbook")
    assert resp.status_code == 200
    assert poller._next_slot >= 100.0 + 3.0  # the shared slot moved, so other tasks wait too


async def test_persistent_429_raises_after_max_retries(tmp_path):
    w = RawWriter(tmp_path)
    poller = KalshiPoller(max_retries=2)
    _fake_time(poller)
    client, calls = _throttling_client(failures=99, retry_after="0")
    status = None
    async with client:
        try:
            await poller.poll_book(client, w, "HIGH")
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
    assert status == 429 and calls["n"] == 3  # first try plus two retries
    assert _all(tmp_path, "kalshi", "orderbook") == []


async def test_book_stamps_never_go_back_when_the_wall_clock_does(tmp_path, monkeypatch):
    """WSL2's clock steps backwards; books must still be in receive order (#20)."""
    import time as time_mod

    ticks = iter([5_000_000_000_000, 4_000_000_000_000, 4_000_000_000_001, 6_000_000_000_000])
    monkeypatch.setattr(time_mod, "time_ns", lambda: next(ticks))
    w = RawWriter(tmp_path)
    poller = KalshiPoller()
    _fake_time(poller)
    client, _ = _throttling_client(failures=0)
    async with client:
        for _ in range(3):
            await poller.poll_book(client, w, "HIGH")
    w.close()
    stamps = [
        r["t"]
        for p in sorted((tmp_path / "kalshi" / "orderbook").rglob("*.jsonl*"))
        for r in read_lines(p)
    ]
    assert len(stamps) == 3 and stamps == sorted(set(stamps))
