import asyncio

import pytest
from websockets.asyncio.server import serve

from drc.recorder import base
from drc.recorder.base import WsSource, json_channel
from drc.recorder.kraken import KrakenSource
from drc.recorder.writer import RawWriter, read_lines


class FakeSource(WsSource):
    venue = "fake"

    def __init__(self, port: int) -> None:
        self.url = f"ws://127.0.0.1:{port}"

    async def subscriptions(self):
        return [{"hello": 1}]

    def channel_of(self, raw):
        return json_channel(raw)


def _records(root, channel):
    out = []
    for p in sorted((root / "fake" / channel).rglob("*.jsonl*")):
        out += list(read_lines(p))
    return out


async def test_records_messages_and_reconnects_with_gap_events(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "MAX_BACKOFF", 0.05)
    connections = 0

    async def handler(ws):
        nonlocal connections
        connections += 1
        assert await ws.recv() == '{"hello":1}'
        await ws.send(f'{{"channel":"book","n":{connections}}}')
        await ws.send("not json")  # dropped: no channel
        if connections == 1:
            await ws.close(code=1011)  # force a disconnect
        else:
            await ws.wait_closed()

    writer = RawWriter(tmp_path)
    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        task = asyncio.create_task(FakeSource(port).run(writer))
        for _ in range(100):
            await asyncio.sleep(0.05)
            if connections >= 2:
                break
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    writer.close()

    assert [r["m"]["n"] for r in _records(tmp_path, "book")] == [1, 2]
    events = [r["ev"] for r in _records(tmp_path, "_meta")]
    assert events == ["connect", "disconnect", "connect", "shutdown"]


def test_kraken_routing():
    k = KrakenSource(["BTC/USD"])
    assert k.channel_of('{"channel":"heartbeat"}') is None
    assert k.channel_of('{"method":"subscribe","success":true}') == "subscriptions"
    assert k.channel_of('{"channel":"book","type":"update","data":[]}') == "book"
