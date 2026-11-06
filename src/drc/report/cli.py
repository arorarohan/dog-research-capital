"""`drc-report`: render the firm and fund pages from the forward-test event log.

    drc-report [--ledger data/ledger/events.sqlite] [--out data/reports] [--charters funds]
    drc-report --sample --out /tmp/drc-sample   # synthetic demo log, no real data

Writes `index.html` (firm) and one `fund-<id>.html` per fund. Each page is a single
self-contained file (inline CSS/JS, no network), so the directory can be copied anywhere.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from drc.forward.eventlog import EventLog
from drc.report.model import FirmView, build_firm
from drc.report.render import fund_file, render_firm, render_fund


def write_report(view: FirmView, out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    written = [out / "index.html"]
    written[0].write_text(render_firm(view), encoding="utf-8")
    for fv in view.funds:
        p = out / fund_file(fv.fund_id)
        p.write_text(render_fund(view, fv), encoding="utf-8")
        written.append(p)
    return written


def _as_of(text: str | None) -> int | None:
    if text is None:
        return None
    if text == "now":
        return time.time_ns()
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1e9)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="drc-report", description=__doc__.split("\n")[0])
    ap.add_argument("--ledger", type=Path, default=Path("data/ledger/events.sqlite"))
    ap.add_argument("--out", type=Path, default=Path("data/reports"))
    ap.add_argument(
        "--charters",
        type=Path,
        default=Path("funds"),
        help="fund charters (<fund_id>.toml); used only if the file hash matches the log",
    )
    ap.add_argument(
        "--as-of",
        help="ISO time or 'now' (default: the latest event in the log, so output is repeatable)",
    )
    ap.add_argument("--sample", action="store_true", help="render a synthetic demo log instead")
    args = ap.parse_args(argv)

    if args.sample:
        from drc.report.sample import build_sample

        tmp = Path(tempfile.mkdtemp(prefix="drc-sample-"))
        ledger, charters = tmp / "events.sqlite", tmp / "funds"
        build_sample(ledger, charters).close()
        label = "synthetic sample (drc.report.sample)"
    else:
        ledger, charters = args.ledger, args.charters
        label = str(ledger)
        if not ledger.exists():  # opening would create an empty log; a reporter must not write
            print(f"drc-report: no event log at {ledger}", file=sys.stderr)
            return 2
    try:
        log = EventLog(ledger)
    except sqlite3.DatabaseError as exc:
        print(f"drc-report: cannot read {ledger}: {exc}", file=sys.stderr)
        return 2
    try:
        view = build_firm(
            log,
            as_of=_as_of(args.as_of),
            charters=charters if charters.is_dir() else None,
            ledger=label,
        )
    finally:
        log.close()
    files = write_report(view, args.out)
    status = "verified" if view.verify.ok else f"BROKEN at event {view.verify.bad_seq}"
    print(f"{len(view.funds)} fund(s), {view.n_events} events, hash chain {status}")
    for f in files:
        print(f"  {f}")
    return 0 if view.verify.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
