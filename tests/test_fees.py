import orjson
import polars as pl
import pytest

from drc.backfill.cli import save_markets
from drc.backfill.polymarket import fee_columns, general_market_row
from drc.backfill.schemas import MARKETS_SCHEMA
from drc.backtest.fee_lookup import catalog_market, fee_model_for, table_market
from drc.backtest.fees import (
    VENUE_FEES,
    CoinbaseFee,
    KrakenFee,
    PolymarketFee,
    resolve_polymarket_fee,
)

# Fee blocks exactly as Gamma returned them on 2026-10-23.
ZERO = {
    "feeType": "zero_fees",
    "feesEnabled": True,
    "feeSchedule": {"exponent": 1, "rate": 0, "rebateRate": 0, "takerOnly": True},
}
SPORTS_V2 = {
    "feeType": "sports_fees_v2",
    "feesEnabled": True,
    "feeSchedule": {"exponent": 1, "rate": 0.03, "rebateRate": 0.25, "takerOnly": True},
}
CRYPTO = {
    "feeType": "crypto_fees_v2",
    "feesEnabled": True,
    "feeSchedule": {"exponent": 1, "rate": 0.07, "rebateRate": 0.2, "takerOnly": True},
}
PRE_FEE_ERA = {"feeType": None, "feesEnabled": False, "feeSchedule": None}


def test_tiered_spot_fees_match_published_tables():
    assert VENUE_FEES["coinbase"].taker_bps == 90 and VENUE_FEES["coinbase"].maker_bps == 50
    assert VENUE_FEES["kraken"].taker_bps == 80 and VENUE_FEES["kraken"].maker_bps == 40
    assert VENUE_FEES["kraken"].fee(2, 5_000.0) == pytest.approx(80.0)  # 0.80% of $10,000
    assert CoinbaseFee(10_000).taker_bps == 75  # tier starts at its threshold
    assert CoinbaseFee(9_999).taker_bps == 90
    assert CoinbaseFee(2e9).taker_bps == 2
    assert KrakenFee(125_000).taker_bps == 25  # Kraken's own worked example: 0.12% / 0.25%
    assert KrakenFee(125_000).maker_bps == 12
    assert KrakenFee(500e6).taker_bps == 5


def test_polymarket_fee_matches_published_table():
    # docs.polymarket.com/trading/fees, 100 shares
    assert PolymarketFee.for_category("crypto").fee(100, 0.5) == pytest.approx(1.75)
    assert PolymarketFee.for_category("Crypto").fee(100, 0.3) == pytest.approx(1.47)
    assert PolymarketFee.for_category("politics").fee(100, 0.5) == pytest.approx(1.00)
    assert PolymarketFee().fee(100, 0.1) == pytest.approx(0.45)  # default = "other", 5%
    assert PolymarketFee.for_category("geopolitics").fee(100, 0.5) == 0.0
    assert PolymarketFee.for_category("crypto").fee(1, 0.01) == pytest.approx(0.00069)
    assert PolymarketFee().fee(-100, 0.5) == PolymarketFee().fee(100, 0.5)


def test_market_fee_data_beats_the_category_table():
    assert PolymarketFee.from_gamma(SPORTS_V2) == PolymarketFee(0.03)  # docs table says 5%
    assert PolymarketFee.from_gamma(CRYPTO).fee(100, 0.5) == pytest.approx(1.75)
    assert PolymarketFee.from_gamma(ZERO).fee(100, 0.5) == 0.0
    assert PolymarketFee.from_gamma(PRE_FEE_ERA) == PolymarketFee(0.0)
    assert PolymarketFee.from_gamma({"feeSchedule": {"rate": 0.1, "exponent": 2}}) == (
        PolymarketFee(0.1, 2.0)
    )
    assert PolymarketFee(0.1, 2.0).fee(100, 0.5) == pytest.approx(0.625)  # 10 x 0.25^2
    assert PolymarketFee.from_gamma({}) is None  # states nothing
    assert PolymarketFee.from_gamma({"feesEnabled": True}) is None  # enabled, no schedule


def test_resolution_order_and_source_labels():
    m, src = resolve_polymarket_fee(SPORTS_V2 | {"category": "crypto"})
    assert m.rate == 0.03 and src == "market fee data"
    m, src = resolve_polymarket_fee({"category": "Politics"})
    assert m.rate == 0.04 and "politics" in src
    m, src = resolve_polymarket_fee({"feesEnabled": True}, category="crypto")
    assert m.rate == 0.07 and "crypto" in src
    m, src = resolve_polymarket_fee(None)
    assert m.rate == 0.05 and "default" in src
    m, src = resolve_polymarket_fee({"category": "not-a-category"})
    assert "default" in src


def test_from_row_reads_table_columns_and_treats_null_as_unknown():
    assert PolymarketFee.from_row({"fee_rate": 0.07, "fee_exponent": 1.0}) == PolymarketFee(0.07)
    assert PolymarketFee.from_row({"fee_rate": None, "fees_enabled": False}) == PolymarketFee(0.0)
    assert PolymarketFee.from_row({"fee_rate": None, "fees_enabled": None}) is None  # old rows


def test_backfill_captures_fee_fields():
    assert fee_columns(SPORTS_V2) == {
        "category": None,
        "fee_type": "sports_fees_v2",
        "fees_enabled": True,
        "fee_rate": 0.03,
        "fee_exponent": 1,
    }
    assert fee_columns(PRE_FEE_ERA)["fee_rate"] is None
    assert fee_columns(PRE_FEE_ERA)["fees_enabled"] is False
    m = {
        "closed": True, "slug": "x", "question": "q?", "conditionId": "0xc",
        "outcomes": orjson.dumps(["Yes", "No"]).decode(),
        "clobTokenIds": orjson.dumps(["tokY", "tokN"]).decode(),
        "outcomePrices": orjson.dumps(["1", "0"]).decode(),
        "closedTime": "2026-05-01 12:00:00+00", "endDate": "2026-05-01T12:00:00Z",
        "startDate": "2026-04-01T12:00:00Z", **CRYPTO,
    }  # fmt: skip
    row = general_market_row(m)
    assert row["fee_rate"] == 0.07 and row["fee_type"] == "crypto_fees_v2"
    df = pl.DataFrame([row], schema=MARKETS_SCHEMA)  # row keys line up with the schema
    assert df["fees_enabled"][0] is True


def _catalog_record(markets):
    return orjson.dumps({"t": 1, "m": markets}) + b"\n"


def _gamma(token, **fee):
    return {
        "conditionId": "0x" + token,
        "clobTokenIds": orjson.dumps([token, token + "n"]).decode(),
    } | fee


def test_catalog_lookup_takes_the_newest_record(tmp_path):
    d = tmp_path / "polymarket" / "_catalog" / "2026-10-22"
    d.mkdir(parents=True)
    (d / "18.jsonl").write_bytes(_catalog_record([_gamma("A", **SPORTS_V2)]))
    (d / "19.jsonl").write_bytes(_catalog_record([_gamma("A", **CRYPTO), _gamma("B", **ZERO)]))
    assert catalog_market(tmp_path, "A")["feeType"] == "crypto_fees_v2"
    assert catalog_market(tmp_path, "An") is not None  # the No token of the same market
    assert catalog_market(tmp_path, "nope") is None
    model, src = fee_model_for("polymarket", "B", raw_root=tmp_path)
    assert model.rate == 0.0 and "recorded catalog" in src


def test_table_lookup_and_fallthrough_to_category_then_default(tmp_path):
    rows = [
        {"venue": "polymarket", "market_id": "m1", "token_id": "T1", "close_ts": 1}
        | fee_columns(SPORTS_V2),
        {"venue": "polymarket", "market_id": "m2", "token_id": "T2", "close_ts": 2}
        | {"category": "Crypto"},  # stored before fee capture, but we know the category
        {"venue": "polymarket", "market_id": "m3", "token_id": "T3", "close_ts": 3},
    ]
    save_markets(tmp_path, pl.DataFrame(rows, schema=MARKETS_SCHEMA), "polymarket", "s")
    assert table_market(tmp_path, "T1")["fee_rate"] == 0.03
    model, src = fee_model_for("polymarket", "T1", hist_root=tmp_path)
    assert model.rate == 0.03 and "markets table" in src
    model, src = fee_model_for("polymarket", "T2", hist_root=tmp_path)
    assert model.rate == 0.07 and "category" in src
    model, src = fee_model_for("polymarket", "T3", hist_root=tmp_path)
    assert model.rate == 0.05 and "default" in src
    model, src = fee_model_for("polymarket", "unknown", raw_root=tmp_path, hist_root=tmp_path)
    assert model.rate == 0.05 and "default" in src


def test_catalog_without_fee_data_falls_through_to_the_table(tmp_path):
    d = tmp_path / "raw" / "polymarket" / "_catalog" / "2026-10-22"
    d.mkdir(parents=True)
    (d / "19.jsonl").write_bytes(_catalog_record([_gamma("T1")]))  # old recording: no fee keys
    hist = tmp_path / "hist"
    rows = [{"venue": "polymarket", "market_id": "m1", "token_id": "T1", "close_ts": 1}
            | fee_columns(CRYPTO)]  # fmt: skip
    save_markets(hist, pl.DataFrame(rows, schema=MARKETS_SCHEMA), "polymarket", "s")
    model, src = fee_model_for("polymarket", "T1", tmp_path / "raw", hist)
    assert model.rate == 0.07 and "markets table" in src


def test_other_venues_use_their_tier_tables(tmp_path):
    model, src = fee_model_for("kraken", "BTC/USD", tmp_path, tmp_path)
    assert model.taker_bps == 80 and "tier" in src
