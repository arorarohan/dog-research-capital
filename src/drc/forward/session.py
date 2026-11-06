"""`drc-session`: one daily forward-test session for a fund (design doc sections 5-6).

A session, in order, inside ONE event-log transaction (all of its events or none):
1. create the fund from its charter on first run (charter hash + git sha) and allocate capital;
2. log every scheduled day since the last session that has no session as `session_missed`
   (never backfilled: a missed day's markets are simply not traded);
3. settle open positions the venue has finally resolved (explicit resolution only);
4. mark every open position at its best bid (0 if there is no book or no bid);
5. enumerate candidate markets, screen them ex ante, fetch each survivor's book, decide;
   every candidate is logged as `market_observed` with its verdict and reason;
6. wait the charter's latency, re-fetch each trade's book and submit through `Fund.submit`
   (risk checks, then `SimBroker`); mark new positions at the fill book's bid;
7. drawdown check, reconciliation, `session_finished` with a summary.

Re-running a finished session is a no-op. A crash rolls the whole session back, so a re-run
starts clean. `--dry-run` runs everything, prints the summary, then rolls back.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import logging
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from drc.backtest.fees import FeeModel, PolymarketFee, resolve_polymarket_fee
from drc.forward.broker import SimBroker
from drc.forward.charter import Charter, CharterError
from drc.forward.eventlog import EventLog
from drc.forward.fund import Fund
from drc.forward.polymarket import (
    MarketData,
    PolymarketHttp,
    RawBook,
    parse_book,
    resolution,
    tokens_of,
)
from drc.forward.sched_gap import decide, screen
from drc.forward.state import MissingMark
from drc.forward.types import BookSnapshot, Order

log = logging.getLogger(__name__)

DEFAULT_LEDGER = Path("data/ledger/events.sqlite")
DEFAULT_FUNDS = Path("funds")
WALL_MARGIN_NS = 500_000_000  # wait this far past decision + latency on the wall clock
MAX_WALL_WAIT_SECS = 30.0  # cap on that extra wait (a big clock step must not hang a session)


class _DryRun(Exception):
    pass


# The code a session runs; its hash goes into every session_started, so a report can show
# exactly which sessions ran on which code (the charter hash alone pins only the rules file).
_SRC = Path(__file__).resolve().parent.parent  # src/drc
CODE_FILES = ("forward/*.py", "backtest/fees.py", "backtest/ledger.py")


def code_fingerprint(root: Path = _SRC) -> str:
    """SHA-256 over (relative path, bytes) of the session's code, as imported."""
    h = hashlib.sha256()
    for f in sorted({p for pat in CODE_FILES for p in root.glob(pat)}):
        h.update(f.relative_to(root).as_posix().encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()


@dataclass
class SessionResult:
    fund_id: str
    session_id: str
    status: str  # finished | already_ran
    dry_run: bool = False
    missed: list[str] = field(default_factory=list)
    observed: Counter = field(default_factory=Counter)  # reason ("trade" for trades) -> count
    orders: list[dict] = field(default_factory=list)
    settlements: list[dict] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)  # closed, awaiting final resolution
    marks: int = 0
    reconciliation: list[str] = field(default_factory=list)
    halted: bool = False
    nav: float | None = None
    cash: float = 0.0
    deployed: float = 0.0
    allocated: float = 0.0
    open_positions: int = 0


def _ns(t: dt.datetime) -> int:
    return int(t.timestamp() * 1e9)


def _from_ns(ns: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(ns / 1e9, dt.UTC)


def session_id_for(fund_id: str, day: dt.date) -> str:
    return f"{fund_id}:{day.isoformat()}"


def _book_payload(raw: RawBook, purpose: str) -> dict:
    """Venue response verbatim plus our receive time; kept inline so the hash chain covers it."""
    return {"token": raw.token, "purpose": purpose, "http_status": raw.status,
            "recv_ns": raw.recv_ns, "raw": raw.body}  # fmt: skip


def _market_brief(m: dict) -> dict:
    keys = ("id", "question", "slug", "endDate", "volumeNum", "volume24hr", "liquidityNum",
            "bestBid", "bestAsk", "lastTradePrice", "negRisk", "feeType", "feeSchedule",
            "feesEnabled", "orderPriceMinTickSize", "orderMinSize", "conditionId")  # fmt: skip
    return {k: m.get(k) for k in keys}


def run_session(
    charter: Charter,
    log_: EventLog,
    md: MarketData,
    *,
    clock: Callable[[], int] = time.time_ns,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], int] = time.monotonic_ns,
    dry_run: bool = False,
    git_sha: str = "",
    code_tree: str = "",
) -> SessionResult:
    """`code_tree`: git tree sha of src/drc/forward at HEAD (the CLI refuses a real run with
    uncommitted code, so it describes what runs); logged with our own `code_fingerprint`."""
    charter.validate()
    if not charter.frozen and not dry_run:
        raise CharterError(
            f"{charter.path} is status={charter.status!r}: review and freeze it before a real "
            "session (only --dry-run is allowed on a draft)"
        )
    fid = charter.fund_id
    now_ns = clock()
    now = _from_ns(now_ns)
    today = now.date()
    sid = session_id_for(fid, today)
    res = SessionResult(fid, sid, "finished", dry_run)

    fees: dict[str, FeeModel] = {}
    fund = Fund(
        log_,
        fid,
        SimBroker(charter.broker_config()),
        charter.risk_limits(),
        lambda tok: fees.get(tok, PolymarketFee()),
        clock=clock,
    )
    done = {e.session_id for e in log_.events(fund_id=fid, types=["session_finished"])}
    if sid in done:
        res.status = "already_ran"
        _fill_totals(res, fund)
        return res
    if fund.state.charter_hash and fund.state.charter_hash != charter.sha256:
        raise CharterError(
            f"fund {fid} was created under charter {fund.state.charter_hash[:12]}, this file "
            f"hashes to {charter.sha256[:12]}: changed rules need a new fund id"
        )

    try:
        with log_.transaction():
            prov = {"git_sha": git_sha, "forward_tree_sha": code_tree,
                    "code_sha256": code_fingerprint()}  # fmt: skip
            _session_body(
                charter, fund, md, res, now, today, sid, clock, sleep, monotonic, fees, prov
            )
            if dry_run:
                raise _DryRun
    except _DryRun:
        pass
    return res


def _session_body(
    charter, fund, md, res, now, today, sid, clock, sleep, monotonic, fees, prov
) -> None:
    fid = charter.fund_id
    # 1. create + allocate
    if "fund_created" not in fund.state.seen_types:
        fund.create(charter.name, charter.sha256, prov["git_sha"])
        fund.allocate(float(charter["capital"]["initial"]), note="initial allocation")

    # 2. missed sessions since the last one
    days = [
        dt.date.fromisoformat(e.payload["date"])
        for e in fund.log.events(fund_id=fid, types=["session_started", "session_missed"])
        if "date" in e.payload
    ]
    if days:
        d = max(days) + dt.timedelta(days=1)
        while d < today:
            fund.miss_session(
                session_id_for(fid, d),
                f"no session ran on {d.isoformat()} (detected by session {sid})",
                date=d.isoformat(),
            )
            res.missed.append(d.isoformat())
            d += dt.timedelta(days=1)

    sched = charter["schedule"].get("time_utc", "00:00")
    hh, mm = (int(x) for x in sched.split(":"))
    scheduled = dt.datetime.combine(today, dt.time(hh, mm), dt.UTC)
    fund.begin_session(
        sid, date=today.isoformat(), scheduled_utc=scheduled.isoformat(),
        late_secs=round((now - scheduled).total_seconds()), charter_hash=charter.sha256,
        dry_run=res.dry_run, **prov,
    )  # fmt: skip

    # 3. settle resolved positions
    open_tokens = sorted(fund.state.positions())
    if open_tokens:
        for m in md.markets_by_token(open_tokens, closed=True):
            for tok in set(open_tokens) & set(tokens_of(m)):
                r = resolution(m, tok)
                if r.kind == "pending":
                    res.pending.append(tok)
                    continue
                ts = _ns(r.ts) if r.ts else clock()
                fund.settle(tok, r.kind, ts, sid, venue=r.detail)
                res.settlements.append({"token": tok, "kind": r.kind, "value": r.value})

    # 4. mark the rest at the bid
    for tok in sorted(fund.state.positions()):
        raw = md.book(tok)
        _log_book(fund, raw, "mark", sid)
        b = parse_book(raw)
        bid = b.best_bid if b else None
        info = {} if bid is not None else {"reason": "no_book" if b is None else "no_bid"}
        fund.mark(tok, bid if bid is not None else 0.0, raw.recv_ns, sid, **info)
        res.marks += 1
    fund.check_drawdown(sid)

    # 5. enumerate, screen, decide
    uni, entry, sizing = charter["universe"], charter["entry"], charter["sizing"]
    end_min = now + dt.timedelta(hours=uni["min_hours_to_end"])
    end_max = now + dt.timedelta(hours=uni["max_hours_to_end"])
    markets = md.candidates(end_min, end_max, float(uni["min_volume_to_date"]))
    traded = _traded_tokens(fund)
    trades: list[tuple] = []  # (token, event id, Decision, decision BookSnapshot)
    for m in markets:
        s = screen(m, uni, now, traded)
        brief = _market_brief(m)
        common = {"market": brief, "token": s.token, "event_id": s.event_id,
                  "hours_to_end": s.hours_to_end, "volume_to_date": s.volume}  # fmt: skip
        if not s.ok:
            fund.observe("market_observed", {**common, "verdict": "skip", "reason": s.reason}, sid)
            res.observed[s.reason] += 1
            continue
        fee, fee_src = resolve_polymarket_fee(m)
        fees[s.token] = fee
        raw = md.book(s.token)
        _log_book(fund, raw, "decision", sid)
        db = parse_book(raw)
        d = decide(db, entry, sizing)
        verdict = {
            **common, "verdict": d.action, "reason": d.reason, "qty": d.qty, "limit": d.limit,
            "best_bid": d.best_bid, "best_ask": d.best_ask, "ask_depth": d.ask_depth,
            "eligible_depth": d.eligible_depth, "fee_source": fee_src,
            "fee_rate": getattr(fee, "rate", None), "decision_ns": raw.recv_ns,
        }  # fmt: skip
        fund.observe("market_observed", verdict, sid, raw.recv_ns)
        res.observed[d.reason or "trade"] += 1
        if d.action == "trade":
            trades.append((s.token, s.event_id, d, db))
            traded.add(s.token)

    # 6. latency, fresh book, submit. The sleep runs on the monotonic clock, but the broker's
    # gate compares wall-clock times (book.ts vs decision_ts), and the wall clock can step
    # backwards (docs/gotchas.md). So also wait until the WALL clock has passed decision +
    # latency (+ a margin), capped; both clocks are logged with the fill book.
    latency_ns = charter.broker_config().latency_ns
    mono0 = monotonic()
    if trades:
        sleep(latency_ns / 1e9)
    for tok, ev, d, db in trades:
        decision_ns = db.ts
        target = decision_ns + latency_ns + WALL_MARGIN_NS
        wall_now, ready, waits = _await_wall(target, clock, sleep, MAX_WALL_WAIT_SECS)
        wait_info = {"decision_ns": decision_ns, "target_ns": target, "wall_ready_ns": wall_now,
                     "wall_ready": ready, "wall_waits": waits,
                     "mono_waited_ns": monotonic() - mono0}  # fmt: skip
        if not ready:
            log.warning("wall clock still %.1fs short of the latency target after %ss; "
                        "fetching anyway (broker will judge)", (target - wall_now) / 1e9,
                        MAX_WALL_WAIT_SECS)  # fmt: skip
        raw = md.book(tok)
        _log_book(fund, raw, "fill", sid, latency_wait=wait_info)
        # no book now (404): an empty book with the decision book's tick, so the broker
        # rejects it as one-sided rather than for an irrelevant reason
        b = parse_book(raw) or BookSnapshot(tok, raw.recv_ns, (), (), db.tick, db.min_size)
        order = Order(f"{sid}:{tok}", fid, tok, ev, "buy", d.qty, d.limit, decision_ns,
                      tag=charter["hypothesis"]["id"])  # fmt: skip
        ex = fund.submit(order, b, now=clock(), session_id=sid)
        res.orders.append({"token": tok, "event_id": ev, "qty": d.qty, "limit": d.limit,
                           "status": ex.status, "reason": ex.reason, "filled": ex.qty,
                           "avg_price": ex.avg_price, "fee": ex.fee})  # fmt: skip
        if ex.qty > 0:
            bid = b.best_bid
            info = {} if bid is not None else {"reason": "no_bid"}
            fund.mark(tok, bid if bid is not None else 0.0, raw.recv_ns, sid, **info)
            res.marks += 1

    # 7. controls and summary
    res.halted = fund.check_drawdown(sid)
    res.reconciliation = fund.reconcile(sid)
    res.halted = res.halted or fund.state.status == "halted"
    _fill_totals(res, fund)
    fund.finish_session(
        sid, observed=dict(res.observed), orders=len(res.orders),
        fills=sum(1 for o in res.orders if o["filled"] > 0), settlements=len(res.settlements),
        pending_resolution=res.pending, marks=res.marks, missed=res.missed,
        reconciliation_ok=not res.reconciliation, nav=res.nav, cash=res.cash,
        deployed=res.deployed, status=fund.state.status,
    )  # fmt: skip


def _log_book(fund: Fund, raw: RawBook, purpose: str, sid: str, **extra) -> None:
    fund.observe("book_snapshot", {**_book_payload(raw, purpose), **extra}, sid, raw.recv_ns)


def _await_wall(
    target_ns: int, clock: Callable[[], int], sleep: Callable[[float], None], max_secs: float
) -> tuple[int, bool, int]:
    """Sleep until the wall clock reads >= target_ns, for at most `max_secs` of requested
    sleep in total (a clock stepped back by hours must not hang the session). Returns
    (wall clock at return, reached?, number of sleeps)."""
    waited, n = 0.0, 0
    while True:
        now = clock()
        if now >= target_ns:
            return now, True, n
        if waited >= max_secs - 1e-9:
            return now, False, n
        step = min((target_ns - now) / 1e9, max_secs - waited)
        sleep(step)
        waited += step
        n += 1


def _traded_tokens(fund: Fund) -> set[str]:
    return {
        e.payload["token"]
        for e in fund.log.events(fund_id=fund.fund_id, types=["order_submitted", "order_rejected"])
        if "token" in e.payload
    }


def _fill_totals(res: SessionResult, fund: Fund) -> None:
    st = fund.state
    try:
        res.nav = st.nav()
    except MissingMark:
        res.nav = None
    res.cash, res.deployed, res.allocated = st.cash, st.deployed(), st.allocated
    res.open_positions = len(st.positions())
    res.halted = res.halted or st.status == "halted"


def format_summary(r: SessionResult) -> str:
    lines = [
        f"{'DRY RUN (nothing written) ' if r.dry_run else ''}session {r.session_id}: {r.status}",
    ]
    if r.status == "already_ran":
        lines.append("  this session already finished; nothing to do (sessions are idempotent)")
    if r.missed:
        lines.append(f"  missed sessions logged: {', '.join(r.missed)}")
    if r.observed:
        n = sum(r.observed.values())
        parts = ", ".join(f"{k} {v}" for k, v in sorted(r.observed.items(), key=lambda x: -x[1]))
        lines.append(f"  candidates {n}: {parts}")
    for o in r.orders:
        px = "-" if o["avg_price"] is None else f"{o['avg_price']:.4f}"
        lines.append(
            f"  order {o['token'][:12]}.. qty {o['qty']:.2f} @ <= {o['limit']}: {o['status']}"
            f"{' (' + o['reason'] + ')' if o['reason'] else ''}, filled {o['filled']:.2f}"
            f" avg {px} fee {o['fee']:.4f}"
        )
    for s in r.settlements:
        lines.append(f"  settled {s['token'][:12]}..: {s['kind']} ({s['value']})")
    if r.pending:
        lines.append(f"  closed, awaiting final resolution: {len(r.pending)}")
    nav = "n/a (unmarked positions)" if r.nav is None else f"{r.nav:,.2f}"
    lines.append(
        f"  allocated {r.allocated:,.2f}  NAV {nav}  cash {r.cash:,.2f}  deployed "
        f"{r.deployed:,.2f}  open positions {r.open_positions}  marks {r.marks}"
    )
    lines.append(
        "  reconciliation: " + ("ok" if not r.reconciliation else "; ".join(r.reconciliation))
    )
    if r.halted:
        lines.append("  FUND HALTED: no new entries until a person reviews it")
    return "\n".join(lines)


# ---- CLI ------------------------------------------------------------------------------------


def _git(*args: str) -> str:
    try:
        out = subprocess.run(["git", *args], capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return ""
    return out.stdout.strip()


def check_committed(path: Path) -> str | None:
    """Reason the charter file is not safely committed, or None."""
    if not _git("ls-files", "--", str(path)):
        return f"{path} is not tracked by git; commit the charter before the first session"
    if _git("status", "--porcelain", "--", str(path)):
        return f"{path} has uncommitted changes; the hashed rules must be the committed ones"
    return None


# what a frozen fund's behaviour depends on: its code, charters, and the locked dependencies
FROZEN_PATHS = (":(top)src", ":(top)funds", ":(top)pyproject.toml", ":(top)uv.lock")


def dirty_paths(git: Callable[..., str] | None = None) -> list[str]:
    """`git status --porcelain` lines (modified, staged or untracked) under FROZEN_PATHS."""
    out = (git or _git)("status", "--porcelain", "--", *FROZEN_PATHS)
    return [ln for ln in out.splitlines() if ln.strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="drc-session", description=__doc__.splitlines()[0])
    ap.add_argument("--fund", required=True, help="fund id; reads funds/<fund>.toml")
    ap.add_argument("--dry-run", action="store_true", help="run everything, write nothing")
    ap.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    ap.add_argument("--funds-dir", type=Path, default=DEFAULT_FUNDS)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    charter = Charter.load(args.funds_dir / f"{args.fund}.toml")
    if charter.fund_id != args.fund:
        print(f"charter fund_id {charter.fund_id!r} != --fund {args.fund!r}", file=sys.stderr)
        return 2
    if not args.dry_run:
        if not charter.frozen:
            msg = (
                f"refusing: {charter.path} is a {charter.status.upper()} charter; review it, set "
                'status = "frozen" and commit it first (or use --dry-run)'
            )
            print(msg, file=sys.stderr)
            return 2
        if _git("rev-parse", "--is-inside-work-tree") != "true":
            print("refusing: not a git checkout, so the code cannot be pinned", file=sys.stderr)
            return 2
        if dirty := dirty_paths():
            print(
                "refusing: uncommitted changes to code, charters or dependencies; a real session "
                "must run committed code (commit or stash them, or use --dry-run):\n  "
                + "\n  ".join(dirty),
                file=sys.stderr,
            )
            return 2
        if problem := check_committed(charter.path):
            print(f"refusing: {problem}", file=sys.stderr)
            return 2
    ledger = EventLog(args.ledger) if not args.dry_run or args.ledger.exists() else None
    ledger = ledger or EventLog(":memory:")
    try:
        sha = _git("rev-parse", "HEAD")
        tree = _git("rev-parse", "HEAD:src/drc/forward")
        res = run_session(
            charter, ledger, PolymarketHttp(), dry_run=args.dry_run, git_sha=sha, code_tree=tree
        )
    except CharterError as e:
        print(f"refusing: {e}", file=sys.stderr)
        return 2
    finally:
        ledger.close()
    print(format_summary(res))
    if not args.dry_run:
        ok = ledger_ok(args.ledger)
        print(f"  event log chain: {'verified' if ok else 'BROKEN'}")
        if not ok:
            return 1
    return 1 if res.reconciliation else 0


def ledger_ok(path: Path) -> bool:
    lg = EventLog(path)
    try:
        return lg.verify().ok
    finally:
        lg.close()


if __name__ == "__main__":
    sys.exit(main())
