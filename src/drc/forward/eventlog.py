"""Append-only, hash-chained event log: the source of truth for forward-test funds.

Every fact about a fund (allocation, session, observed market, order, fill, mark, settlement,
halt) is one row. Rows are never updated or deleted (SQLite triggers refuse), and each row's
hash covers the previous row's hash, so editing or removing history later is detectable by
`verify()`. Positions, NAV and PnL are projections of this log (see state.py), never stored
state. Same philosophy as the recorder: keep what happened verbatim, derive views later.

Timestamps are integer nanoseconds since the Unix epoch (UTC). `ts_event` is when the thing
happened in the (simulated) world, `ts_recorded` is the local clock when we wrote the row; see
docs/gotchas.md on why the two can differ.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import orjson

GENESIS = "0" * 64

EVENT_TYPES = frozenset(
    {
        "fund_created",
        "capital_allocated",
        "capital_withdrawn",
        "session_started",
        "session_finished",
        "session_missed",
        "market_observed",
        "book_snapshot",
        "risk_check",
        "order_submitted",
        "order_rejected",
        "order_unfilled",
        "fill",
        "mark",
        "settlement",
        "fund_paused",
        "fund_resumed",
        "fund_halted",
        "fund_closed",
        "reconciliation",
    }
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq         INTEGER PRIMARY KEY,
    ts_event    INTEGER NOT NULL,
    ts_recorded INTEGER NOT NULL,
    fund_id     TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    type        TEXT NOT NULL,
    payload     TEXT NOT NULL,
    prev_hash   TEXT NOT NULL,
    hash        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_fund ON events (fund_id, seq);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'event log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'event log is append-only'); END;
"""


@dataclass(frozen=True)
class Event:
    seq: int
    ts_event: int
    ts_recorded: int
    fund_id: str
    session_id: str
    type: str
    payload: dict
    prev_hash: str
    hash: str


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    n_events: int
    bad_seq: int | None = None
    reason: str = ""


def dumps(payload: dict) -> str:
    """Canonical JSON: sorted keys, so the same payload always hashes the same."""
    return orjson.dumps(payload, option=orjson.OPT_SORT_KEYS).decode()


def row_hash(
    prev_hash: str,
    seq: int,
    ts_event: int,
    ts_recorded: int,
    fund_id: str,
    session_id: str,
    type_: str,
    payload_json: str,
) -> str:
    body = orjson.dumps(
        [prev_hash, seq, ts_event, ts_recorded, fund_id, session_id, type_, payload_json]
    )
    return hashlib.sha256(body).hexdigest()


class EventLog:
    def __init__(self, path: str | Path, clock: Callable[[], int] = time.time_ns):
        self.clock = clock
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: we issue BEGIN/COMMIT ourselves so sessions are atomic.
        self._db = sqlite3.connect(str(path), isolation_level=None)
        self._db.executescript(_SCHEMA)
        self._depth = 0

    def close(self) -> None:
        self._db.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """All appends inside commit together or not at all. Nested use joins the outer one."""
        if self._depth:
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
            return
        self._db.execute("BEGIN IMMEDIATE")
        self._depth = 1
        try:
            yield
        except BaseException:
            self._depth = 0
            self._db.execute("ROLLBACK")
            raise
        else:
            self._depth = 0
            self._db.execute("COMMIT")

    def append(
        self,
        type_: str,
        payload: dict,
        *,
        fund_id: str = "",
        session_id: str = "",
        ts_event: int | None = None,
    ) -> Event:
        if type_ not in EVENT_TYPES:
            raise ValueError(f"unknown event type {type_!r}")
        with self.transaction():
            last = self._db.execute(
                "SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            seq, prev = (last[0] + 1, last[1]) if last else (1, GENESIS)
            ts_rec = self.clock()
            ts_ev = ts_rec if ts_event is None else int(ts_event)
            pj = dumps(payload)
            h = row_hash(prev, seq, ts_ev, ts_rec, fund_id, session_id, type_, pj)
            self._db.execute(
                "INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?)",
                (seq, ts_ev, ts_rec, fund_id, session_id, type_, pj, prev, h),
            )
        return Event(seq, ts_ev, ts_rec, fund_id, session_id, type_, orjson.loads(pj), prev, h)

    def events(
        self,
        fund_id: str | None = None,
        types: Iterable[str] | None = None,
        session_id: str | None = None,
    ) -> list[Event]:
        sql, args = "SELECT * FROM events WHERE 1=1", []
        if fund_id is not None:
            sql += " AND fund_id = ?"
            args.append(fund_id)
        if session_id is not None:
            sql += " AND session_id = ?"
            args.append(session_id)
        if types is not None:
            ts = list(types)
            sql += f" AND type IN ({','.join('?' * len(ts))})"
            args += ts
        sql += " ORDER BY seq"
        return [self._event(r) for r in self._db.execute(sql, args)]

    @staticmethod
    def _event(r: tuple) -> Event:
        return Event(r[0], r[1], r[2], r[3], r[4], r[5], orjson.loads(r[6]), r[7], r[8])

    def fund_ids(self) -> list[str]:
        rows = self._db.execute(
            "SELECT fund_id FROM events WHERE type = 'fund_created' ORDER BY seq"
        )
        return [r[0] for r in rows]

    def verify(self) -> VerifyResult:
        """Re-walk the chain. Detects edited rows, removed middle rows and a broken link.

        Limit: deleting rows from the very end leaves a valid shorter chain. The triggers stop
        that in normal use; the private ledger repo's git history is the backstop.
        """
        prev, expect_seq, n = GENESIS, 1, 0
        for r in self._db.execute("SELECT * FROM events ORDER BY seq"):
            seq, ts_ev, ts_rec, fund, sess, typ, pj, prev_hash, h = r
            n += 1
            if seq != expect_seq:
                return VerifyResult(False, n, seq, f"expected seq {expect_seq}, found {seq}")
            if prev_hash != prev:
                return VerifyResult(False, n, seq, "prev_hash does not match previous row")
            if row_hash(prev_hash, seq, ts_ev, ts_rec, fund, sess, typ, pj) != h:
                return VerifyResult(False, n, seq, "row hash mismatch (row was altered)")
            prev, expect_seq = h, expect_seq + 1
        return VerifyResult(True, n)
