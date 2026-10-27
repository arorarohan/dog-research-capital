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
