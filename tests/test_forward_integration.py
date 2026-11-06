"""drc-session -> drc-report, end to end and offline.

Several simulated days of REAL session code (fake venue built from the recorded Polymarket
fixtures, temp ledger), including a missed day, a re-run, a win, a loss, a 50/50 split and
skipped candidates; then the real `drc-report` CLI on that ledger. This is the contract test
between Slice 1 (writes the log) and Slice 2 (reads it): if either side changes a field the
other relies on, this fails.
"""

import datetime as dt
import json
import re
from collections import Counter
from pathlib import Path

import pytest

from drc.forward.charter import Charter
from drc.forward.eventlog import EventLog
from drc.forward.polymarket import RawBook
from drc.forward.session import run_session
from drc.report import cli as report_cli
from drc.report.model import build_firm, load_prediction

ROOT = Path(__file__).parent.parent
CHARTER = ROOT / "funds" / "pm-sched-gap-v1.toml"
FIX = Path(__file__).parent / "fixtures" / "polymarket"
REAL_MARKET = json.loads((FIX / "gamma_btc_dip_74k.json").read_text())
S = 1_000_000_000
T0 = dt.datetime(2026, 11, 6, 12, 0, tzinfo=dt.UTC)
FID = "pm-sched-gap-v1"


class Clock:
    def __init__(self, t: dt.datetime = T0):
        self.ns = int(t.timestamp() * S)

    def __call__(self) -> int:
        self.ns += 10_000_000
        return self.ns

    def sleep(self, secs: float) -> None:
        self.ns += int(secs * S)

    def set(self, t: dt.datetime) -> None:
        self.ns = int(t.timestamp() * S)


def clob_book(token, bids, asks):
    lv = lambda xs: [{"price": str(p), "size": str(s)} for p, s in xs]  # noqa: E731
    return {"market": "0xm", "asset_id": token, "timestamp": "1", "hash": "h",
            "bids": lv(sorted(bids)), "asks": lv(sorted(asks, reverse=True)),
            "min_order_size": "5", "tick_size": "0.01", "neg_risk": False}  # fmt: skip


def market(token, now, volume=100_000.0, event=None):
    end = now + dt.timedelta(hours=66)
    return {**REAL_MARKET, "id": f"m-{token}", "clobTokenIds": json.dumps([token, token + "-no"]),
            "endDate": end.strftime("%Y-%m-%dT%H:%M:%SZ"), "volumeNum": volume,
            "events": [{"id": event or f"E{token}"}], "question": f"Q {token}?"}  # fmt: skip


def resolved(token, prices, when):
    return {**REAL_MARKET, "clobTokenIds": json.dumps([token, token + "-no"]), "closed": True,
            "umaResolutionStatus": "resolved", "outcomePrices": json.dumps(prices),
            "closedTime": when}  # fmt: skip


class FakeVenue:
    def __init__(self, clock):
        self.clock, self.markets, self.books, self.closed = clock, [], {}, {}

    def candidates(self, end_min, end_max, min_volume):
        return list(self.markets)

    def book(self, token):
        body = self.books.get(token)
        return RawBook(token, self.clock(), body, 200 if body else 404)

    def markets_by_token(self, tokens, closed):
        return [m for t, m in self.closed.items() if t in tokens]


@pytest.fixture
def world(tmp_path):
    funds = tmp_path / "funds"
    funds.mkdir()
    p = funds / f"{FID}.toml"
    p.write_text(re.sub(r'(?m)^status = "draft"', 'status = "frozen"', CHARTER.read_text()))
    clock = Clock()
    ledger = tmp_path / "ledger" / "events.sqlite"
    return Charter.load(p), clock, FakeVenue(clock), ledger, funds


def simulate(charter, clock, venue, ledger) -> list:
    """Four calendar days, three sessions: 11-06, 11-07, (11-08 missed), 11-09 twice."""
    log = EventLog(ledger, clock=clock)
    results = []

    def session(day: dt.datetime):
        clock.set(day)
        results.append(run_session(charter, log, venue, clock=clock, sleep=clock.sleep))

    # day 1: two trades (A, Z) and four skips
    d1 = T0
    venue.markets = [market("A", d1), market("Z", d1), market("B", d1), market("C", d1),
                     market("D", d1, volume=10.0), market("N", d1)]  # fmt: skip
    venue.books = {
        "A": clob_book("A", [(0.28, 500)], [(0.30, 400)]),
        "Z": clob_book("Z", [(0.10, 500)], [(0.12, 400)]),
        "B": clob_book("B", [(0.70, 500)], [(0.72, 400)]),  # above the 60c band
        "C": clob_book("C", [], [(0.20, 400)]),  # one-sided
    }
    session(d1)

    # day 2: A resolves Yes; Z still open (marked at its bid); one new trade F, one skip
    d2 = T0 + dt.timedelta(days=1)
    venue.closed = {"A": resolved("A", ["1", "0"], "2026-11-07 06:00:00+00")}
    venue.markets = [market("F", d2), market("L", d2)]
    venue.books = {
        "Z": clob_book("Z", [(0.08, 500)], [(0.10, 400)]),
        "F": clob_book("F", [(0.38, 500)], [(0.40, 400)]),
        "L": clob_book("L", [(0.01, 500)], [(0.02, 400)]),  # below the 5c band
    }
    session(d2)

    # day 3 (11-08): the job did not run
    # day 4: Z resolves No, F resolves 50/50; nothing new. Then the timer fires again.
    d4 = T0 + dt.timedelta(days=3)
    venue.closed = {
        "Z": resolved("Z", ["0", "1"], "2026-11-08 18:00:00+00"),
        "F": resolved("F", ["0.5", "0.5"], "2026-11-09 03:00:00+00"),
    }
    venue.markets, venue.books = [], {}
    session(d4)
    session(d4 + dt.timedelta(minutes=5))  # same day: must be a no-op
    log.close()
    return results


def test_session_output_drives_the_report_end_to_end(world, tmp_path):
    charter, clock, venue, ledger, funds = world
    results = simulate(charter, clock, venue, ledger)
    assert [r.status for r in results] == ["finished", "finished", "finished", "already_ran"]
    assert results[2].missed == ["2026-11-08"]
    assert {s["kind"] for s in results[1].settlements + results[2].settlements} == {
        "win", "lose", "split"
    }  # fmt: skip
    assert all(not r.reconciliation for r in results)

    out = tmp_path / "reports"
    argv = ["--ledger", str(ledger), "--out", str(out), "--charters", str(funds)]
    assert report_cli.main(argv) == 0
    page = (out / f"fund-{FID}.html").read_text()
    index = (out / "index.html").read_text()

    log = EventLog(ledger)
    try:
        view = build_firm(log, charters=funds)
        fills = {e.payload["token"]: e.payload for e in log.events(types=["fill"])}
        settled = {e.payload["token"]: e.payload["value"] for e in log.events(types=["settlement"])}
    finally:
        log.close()
    (fv,) = view.funds

    # chain badge
    assert view.verify.ok
    assert "hash chain verified" in page and "hash chain verified" in index

    # skip tally: the report's candidate table equals what the sessions counted
    seen = Counter()
    for r in results:
        seen.update(r.observed)
    tally = {(r or "trade"): n for v, r, n in fv.candidates}
    assert tally == dict(seen)
    assert {(v, r) for v, r, _ in fv.candidates} >= {
        ("trade", ""), ("skip", "out_of_band_high"), ("skip", "out_of_band_low"),
        ("skip", "one_sided_book"), ("skip", "below_volume_floor"), ("skip", "no_book"),
    }  # fmt: skip
    for reason in ("out_of_band_high", "out_of_band_low", "one_sided_book", "no_book"):
        assert reason in page

    # predicted vs realized: the prediction came from the chain-protected fund_created event
    pr = fv.prediction
    assert pr is not None and pr.source == "event log"
    assert pr.edge == pytest.approx(0.012) and (pr.ci_low, pr.ci_high) == pytest.approx(
        (0.005, 0.019)
    )
    assert pr.sd == pytest.approx(0.464) and pr.sd_source == "charter"
    assert "Predicted net edge: <b>+1.20c</b>" in page

    # realized edge recomputed independently from the raw fill and settlement events
    assert set(fills) == set(settled) == {"A", "Z", "F"}
    net = sum(f["qty"] * (settled[t] - f["price"]) - f["fee"] for t, f in fills.items())
    contracts = sum(f["qty"] for f in fills.values())
    ed = fv.edge
    assert ed.n_positions == 3 and ed.n_events == 3 and ed.n_voids == 0
    assert ed.realized == pytest.approx(net / contracts)
    assert "Realized minus predicted (the headline)" in page

    # minimum detectable edge at n = 3 events: (1.96 + 0.84) x 0.464 / sqrt(3)
    assert ed.mde == pytest.approx((1.959964 + 0.841621) * 0.464 / 3**0.5, rel=1e-4)
    assert "Minimum detectable edge at this sample" in page
    assert f"{ed.mde * 100:.2f}c" in page
    assert ed.verdict.startswith("inconclusive by design")

    # missed day, sessions, reconciliations, money
    assert [m.session_id for m in fv.sessions.missed] == [f"{FID}:2026-11-08"]
    assert len(fv.sessions.finished) == 3 and not fv.sessions.unfinished
    assert fv.reconciliations_ok == 3 and not fv.reconciliation_failures
    assert fv.summary.nav == pytest.approx(10_000 + net)
    assert not fv.positions

    # the same log renders byte-identical pages
    out2 = tmp_path / "reports2"
    assert (
        report_cli.main(["--ledger", str(ledger), "--out", str(out2), "--charters", str(funds)])
        == 0
    )
    assert (out2 / f"fund-{FID}.html").read_bytes() == page.encode()


def test_report_accepts_the_charter_file_only_if_its_hash_matches(world):
    charter, *_, funds = world
    created = {"charter_hash": charter.sha256}  # an older log without the prediction inline
    pr, note = load_prediction(FID, created, funds)
    assert pr is not None and pr.source == f"{FID}.toml" and note == ""
    assert pr.edge == pytest.approx(0.012) and pr.sd == pytest.approx(0.464)

    path = funds / f"{FID}.toml"
    path.write_text(
        path.read_text().replace("net_per_contract_cents = 1.2", "net_per_contract_cents = 3.0")
    )
    pr, note = load_prediction(FID, created, funds)
    assert pr is None and "does not match" in note
