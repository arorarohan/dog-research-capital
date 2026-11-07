import httpx

from drc.recorder import kalshi
from drc.recorder.kalshi import KalshiPoller
from drc.recorder.writer import RawWriter, read_lines


def _records(root, channel):
    return [r for p in sorted((root / "kalshi" / channel).rglob("*.jsonl*")) for r in read_lines(p)]


async def test_a_failed_trade_poll_does_not_advance_the_window(tmp_path):
    """The window used to advance before the request, so a poll that died lost its trades."""
    seen_min_ts = []
    fail = {"on": True}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_min_ts.append(request.url.params["min_ts"])
        if fail["on"]:
            return httpx.Response(500)
        return httpx.Response(
            200, json={"cursor": "", "trades": [{"trade_id": "a", "ticker": "T"}]}
        )

    w = RawWriter(tmp_path)
    poller = KalshiPoller()
    before = poller._last_trade_ts
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        try:
            await poller.poll_trades(client, w)
        except httpx.HTTPStatusError:
            pass
        assert poller._last_trade_ts == before  # not advanced
        fail["on"] = False
        await poller.poll_trades(client, w)
    w.close()
    assert seen_min_ts[0] == seen_min_ts[1] == str(before - 2)  # same window asked again
    assert poller._last_trade_ts >= before  # advanced to the start of the poll that completed
    assert [r["m"]["trade_id"] for r in _records(tmp_path, "trades")] == ["a"]


async def test_a_truncated_trade_poll_is_recorded_as_an_event(tmp_path, monkeypatch):
    monkeypatch.setattr(kalshi, "MAX_TRADE_PAGES", 2)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:  # an endless feed of trades
        calls.append(1)
        n = len(calls)
        return httpx.Response(
            200, json={"cursor": "more", "trades": [{"trade_id": f"t{n}", "ticker": "T"}]}
        )

    w = RawWriter(tmp_path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await KalshiPoller().poll_trades(client, w)
    w.close()
    assert [e["ev"] for e in _records(tmp_path, "_meta")] == ["trades_truncated"]
    assert len(calls) == 2
