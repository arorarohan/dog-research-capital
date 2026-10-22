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
