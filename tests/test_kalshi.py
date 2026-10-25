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
