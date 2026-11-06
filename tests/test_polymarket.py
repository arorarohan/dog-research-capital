import orjson

from drc.recorder.polymarket import PolymarketSource


def test_channel_routing():
    p = PolymarketSource()
    assert p.channel_of("PONG") is None
    assert p.channel_of('[{"asset_id":"1","bids":[],"asks":[]}]') == "book"
    assert p.channel_of('{"market":"0x1","price_changes":[]}') == "price_change"
    assert p.channel_of('{"event_type":"last_trade_price"}') == "last_trade_price"
    assert p.channel_of('{"foo":1}') == "other"


def test_token_ids_flattens_stringified_json():
    markets = [{"clobTokenIds": orjson.dumps(["a", "b"]).decode()}, {"clobTokenIds": '["c"]'}]
    assert PolymarketSource.token_ids(markets) == ["a", "b", "c"]


async def test_deadline_mode_queries_scheduled_window_with_volume_floor_and_pages(tmp_path):
    import httpx

    from drc.recorder.writer import RawWriter, read_lines

    seen = []
    pages = {
        None: {
            "markets": [
                {"clobTokenIds": '["a1","a2"]', "enableOrderBook": True, "volumeNum": 9e4},
                {"clobTokenIds": '["no-book"]', "enableOrderBook": False},
            ],
            "next_cursor": "p2",
        },
        "p2": {
            "markets": [{"clobTokenIds": '["b1","b2"]', "enableOrderBook": True}],
            "next_cursor": "",
        },
    }

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(dict(req.url.params))
        return httpx.Response(200, json=pages[req.url.params.get("after_cursor")])

    src = PolymarketSource(deadline_max_days=7, min_volume=50_000, max_markets=10)
    src.writer = RawWriter(tmp_path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        markets = await src.discover(client)
    src.writer.close()
    assert PolymarketSource.token_ids(markets) == ["a1", "a2", "b1", "b2"]  # book-less dropped
    q = seen[0]
    assert q["closed"] == "false" and q["order"] == "endDate" and q["volume_num_min"] == "50000"
    assert q["end_date_min"] < q["end_date_max"]  # window: grace day behind .. 7 days ahead
    (cat,) = [r["m"] for p in (tmp_path / "polymarket" / "_catalog").rglob("*.jsonl*")
              for r in read_lines(p)]  # fmt: skip
    assert len(cat) == 2 and len(seen) == 2  # only order-book markets are stored


async def test_deadline_mode_caps_markets(tmp_path):
    import httpx

    from drc.recorder.writer import RawWriter

    def handler(req: httpx.Request) -> httpx.Response:
        ms = [{"clobTokenIds": f'["t{i}"]', "enableOrderBook": True} for i in range(100)]
        return httpx.Response(200, json={"markets": ms, "next_cursor": "more"})

    src = PolymarketSource(deadline_max_days=7, max_markets=150)
    src.writer = RawWriter(tmp_path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        markets = await src.discover(client)
    src.writer.close()
    assert len(markets) == 150


def _markets(tokens):
    return [{"clobTokenIds": orjson.dumps([t]).decode()} for t in tokens]


async def _wait_for(cond, within_secs=5.0):
    import asyncio

    for _ in range(int(within_secs / 0.02)):
        if cond():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met in time")


async def test_shards_reconnect_independently_and_refresh_fills_then_adds_shards(
    tmp_path, monkeypatch
):
    import asyncio

    import pytest
    from websockets.asyncio.server import serve

    from drc.recorder import base, polymarket
    from drc.recorder.writer import RawWriter, read_lines

    monkeypatch.setattr(base, "MAX_BACKOFF", 0.05)
    conns: list[dict] = []  # one per connection: initial assets, later subscribe ops, live ws

    async def handler(ws):
        sub = orjson.loads(await ws.recv())
        conn = {"assets": sub["assets_ids"], "adds": [], "ws": ws}
        conns.append(conn)
        await ws.send(
            orjson.dumps([{"asset_id": a, "bids": [], "asks": []} for a in conn["assets"]])
        )
        if "A" in conn["assets"] and sum("A" in c["assets"] for c in conns) == 1:
            await ws.close(code=1013, reason="slow consumer: send buffer full")
            return
        async for msg in ws:
            if msg != "PING":
                conn["adds"].append(orjson.loads(msg)["assets_ids"])

    async def fake_discover(client=None):
        return _markets(["A", "B", "C"])  # initial universe: shards [A, B] and [C]

    writer = RawWriter(tmp_path)
    src = polymarket.PolymarketSource(refresh_secs=3600, shard_size=2)
    monkeypatch.setattr(src, "discover", fake_discover)
    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(polymarket.PolymarketShard, "url", f"ws://127.0.0.1:{port}")
        task = asyncio.create_task(src.run(writer))
        # shard 0's first connection is closed with 1013; it comes back, shard 1 is untouched
        await _wait_for(lambda: [c["assets"] for c in conns].count(["A", "B"]) == 2)
        assert [c["assets"] for c in conns].count(["C"]) == 1

        # refresh: shard 0 is full, so D goes to shard 1 live and E opens shard 2
        await src.apply_universe(["A", "B", "C", "D", "E"])
        await _wait_for(lambda: any(c["assets"] == ["E"] for c in conns))
        (shard1,) = [c for c in conns if c["assets"] == ["C"]]
        await _wait_for(lambda: shard1["adds"] == [["D"]])

        # refresh: B leaves; shard 0 stays connected until it next reconnects, without B
        await src.apply_universe(["A", "C", "D", "E"])
        live0 = [c for c in conns if c["assets"] == ["A", "B"]][-1]
        await live0["ws"].close(code=1013)
        await _wait_for(lambda: any(c["assets"] == ["A"] for c in conns))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    writer.close()

    assert sorted(tuple(c["assets"]) for c in conns) == [
        ("A",), ("A", "B"), ("A", "B"), ("C",), ("E",)
    ]  # fmt: skip
    assert [len(s.tokens) for s in src.shards] == [1, 2, 1]

    meta = [r for p in sorted((tmp_path / "polymarket" / "_meta").rglob("*.jsonl*"))
            for r in read_lines(p)]  # fmt: skip
    connects = [(r["shard"], r["assets"]) for r in meta if r["ev"] == "connect"]
    assert sorted(connects) == [
        (0, ["A"]),
        (0, ["A", "B"]),
        (0, ["A", "B"]),
        (1, ["C"]),
        (2, ["E"]),
    ]
    assert [(r["shard"], r["assets"]) for r in meta if r["ev"] == "subscribe"] == [(1, ["D"])]
    disconnects = [r for r in meta if r["ev"] == "disconnect"]
    assert [r["shard"] for r in disconnects] == [0, 0]
    assert all("1013" in r["reason"] for r in disconnects)
    assert sorted(r["shard"] for r in meta if r["ev"] == "shutdown") == [0, 1, 2]
    books = [r for p in (tmp_path / "polymarket" / "book").rglob("*.jsonl*") for r in read_lines(p)]
    assert len(books) >= len(conns) - 1  # the last snapshot may race the cancel


async def test_failed_or_empty_refresh_keeps_subscriptions(monkeypatch):
    import asyncio

    from drc.recorder import polymarket

    results = iter([RuntimeError("gamma down"), [], ["A", "B", "Z"]])
    applied = []

    async def fake_discover(client=None):
        r = next(results, None)
        if r is None:
            await asyncio.Event().wait()
        if isinstance(r, Exception):
            raise r
        return _markets(r)

    src = polymarket.PolymarketSource(refresh_secs=0, shard_size=2)
    src.subscribed = {"A", "B"}
    monkeypatch.setattr(src, "discover", fake_discover)
    monkeypatch.setattr(src, "_start_shard", lambda toks: applied.append(toks))
    task = asyncio.create_task(src._refresh_loop())
    await _wait_for(lambda: applied)
    task.cancel()
    assert src.subscribed == {"A", "B", "Z"} and applied == [["Z"]]


async def test_token_back_in_the_universe_returns_to_the_connection_still_streaming_it(tmp_path):
    from drc.recorder import polymarket
    from drc.recorder.writer import RawWriter

    class FakeWs:
        sent: list = []

        async def send(self, msg):
            self.sent.append(orjson.loads(msg)["assets_ids"])

    src = polymarket.PolymarketSource(shard_size=3)
    src.writer = RawWriter(tmp_path)
    shard = polymarket.PolymarketShard(0, ["A", "B"], src.writer)
    await shard.subscriptions()
    await shard.on_connected(FakeWs())
    src.shards, src.subscribed = [shard], {"A", "B"}

    await src.apply_universe(["A"])  # B leaves but is still streamed on the live connection
    assert shard.tokens == ["A"]
    await src.apply_universe(["A", "B", "C"])  # B returns: re-adopted, not subscribed again
    src.writer.close()
    assert shard.tokens == ["A", "B", "C"] and FakeWs.sent == [["C"]]
