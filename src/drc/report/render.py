"""Render report data (model.py) to self-contained static HTML: no server, no network, no
external assets. Pages read on a phone (fluid layout, scrolling tables) and follow the OS
light/dark setting. All text from the log is escaped; chart labels are inserted with
textContent.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from html import escape

from drc.report.model import (
    BLIP_NS,
    DEFAULT_SD,
    MARK_STALE_NS,
    FirmView,
    FundView,
    NavPoint,
    avg_deployed,
)

CAVEATS = (
    "Simulated fills only. Nothing here traded real money.",
    "Market impact beyond the displayed order book depth is not modelled.",
    "Adverse selection against our own visible orders is not modelled.",
    "API failures, rate limits and outages of a live connection are not tested.",
    "Our own operational mistakes in a live setup are not tested.",
    "A forward test with simulated fills is stronger evidence than a backtest, not proof of a "
    "live edge.",
)

# ---- formatting ----------------------------------------------------------------------------
NA = '<span class="na">n/a</span>'


def e(x: object) -> str:
    return escape(str(x), quote=True)


def money(x: float | None, signed: bool = False) -> str:
    if x is None:
        return NA
    if abs(x) < 0.005:
        x = 0.0
    sign = "−" if x < 0 else ("+" if signed and x > 0 else "")
    return f"{sign}${abs(x):,.2f}"


def num(x: float | None, nd: int = 2) -> str:
    return NA if x is None else f"{x:,.{nd}f}"


def pct(x: float | None) -> str:
    if x is None:
        return NA
    return f"{x * 100:+.2f}%".replace("-", "−")


def per_day(x: float | None) -> str:
    """Return per dollar-day of deployed capital, as a percent per day."""
    return NA if x is None else pct(x) + "/day"


def cents(x: float | None, signed: bool = True) -> str:
    if x is None:
        return NA
    return (f"{x * 100:+.2f}c" if signed else f"{x * 100:.2f}c").replace("-", "−")


def price(x: float | None) -> str:
    return NA if x is None else f"{x:.3f}"


def when(ns: int | None) -> str:
    if ns is None:
        return NA
    return datetime.fromtimestamp(ns / 1e9, UTC).strftime("%Y-%m-%d %H:%M UTC")


def fund_file(fund_id: str) -> str:
    return "fund-" + re.sub(r"[^A-Za-z0-9._-]", "_", fund_id) + ".html"


# ---- page chrome ---------------------------------------------------------------------------
CSS = """
:root{color-scheme:light dark;
--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
--grid:#e1e0d9;--axis:#c3c2b7;--ring:rgba(11,11,11,.10);--series:#2a78d6;
--good:#0ca30c;--good-ink:#006300;--warn:#fab219;--serious:#ec835a;--crit:#d03b3b;--bad-ink:#b42828}
@media (prefers-color-scheme:dark){:root{
--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--muted:#898781;
--grid:#2c2c2a;--axis:#383835;--ring:rgba(255,255,255,.10);--series:#3987e5;
--good-ink:#0ca30c;--bad-ink:#e66767}}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);
font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:980px;margin:0 auto;padding:16px}
h1{font-size:1.5rem;margin:.2rem 0}h2{font-size:1.1rem;margin:1.6rem 0 .5rem}
h3{font-size:.95rem;margin:1rem 0 .4rem;color:var(--ink2)}
a{color:var(--series)}
.sub{color:var(--ink2);font-size:.9rem}
.card{background:var(--surface);border:1px solid var(--ring);border-radius:10px;padding:14px;
margin:10px 0}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.tile{background:var(--surface);border:1px solid var(--ring);border-radius:10px;padding:12px}
.tile .l{color:var(--ink2);font-size:.85rem}.tile .v{font-size:1.5rem;font-weight:600}
.tile .d{color:var(--muted);font-size:.8rem}
.pos{color:var(--good-ink)}.neg{color:var(--bad-ink)}.na{color:var(--muted)}
.badge{display:inline-flex;gap:6px;align-items:center;border:1px solid var(--ring);
border-radius:999px;padding:2px 10px;font-size:.85rem;background:var(--surface)}
.dot{width:10px;height:10px;border-radius:50%;display:inline-block}
.s-good{background:var(--good)}.s-warn{background:var(--warn)}
.s-serious{background:var(--serious)}.s-crit{background:var(--crit)}
.flag{border-left:4px solid var(--warn);padding:8px 12px;margin:8px 0;background:var(--surface);
border-radius:6px}
.flag.crit{border-left-color:var(--crit)}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:.88rem}
th,td{padding:5px 8px;border-bottom:1px solid var(--grid);text-align:left;white-space:nowrap}
th{color:var(--ink2);font-weight:600}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
details summary{cursor:pointer;color:var(--ink2);margin:6px 0}
.chart{position:relative}
.chart{overflow-x:auto}.chart svg{width:100%;min-width:560px;height:auto;display:block;
touch-action:pan-y}
.chart svg:focus{outline:2px solid var(--series);outline-offset:2px}
.tip{position:absolute;pointer-events:none;background:var(--surface);border:1px solid var(--ring);
border-radius:6px;padding:6px 8px;font-size:.8rem;display:none;white-space:nowrap;
box-shadow:0 2px 8px rgba(0,0,0,.12)}
.tip b{display:block;font-size:.95rem}
.key{display:inline-block;width:14px;height:2px;vertical-align:middle;margin-right:6px}
ul.caveats{margin:.3rem 0 0 1.1rem;padding:0}
footer{color:var(--muted);font-size:.8rem;margin:2rem 0 1rem}
"""

CHART_JS = """
document.querySelectorAll('.chart').forEach(function(box){
  var svg=box.querySelector('svg'),tip=box.querySelector('.tip'),
      cross=svg.querySelector('.cross'),pts=JSON.parse(box.dataset.points),i=-1;
  if(!pts.length)return;
  function show(k){
    i=Math.max(0,Math.min(pts.length-1,k));var p=pts[i];
    cross.setAttribute('x1',p.x);cross.setAttribute('x2',p.x);cross.style.display='';
    tip.replaceChildren();var b=document.createElement('b');
    b.textContent=p.nav;tip.appendChild(b);
    [p.when,'net allocated '+p.alloc,'after: '+p.cause].forEach(function(t){
      var d=document.createElement('div');d.textContent=t;tip.appendChild(d);});
    tip.style.display='block';
    var r=svg.getBoundingClientRect(),sx=r.width/svg.viewBox.baseVal.width,
        left=p.x*sx+12;
    if(left+tip.offsetWidth>r.width)left=p.x*sx-tip.offsetWidth-12;
    tip.style.left=Math.max(0,left)+'px';tip.style.top='8px';
  }
  function hide(){tip.style.display='none';cross.style.display='none';}
  svg.addEventListener('pointermove',function(ev){
    var r=svg.getBoundingClientRect(),x=(ev.clientX-r.left)*svg.viewBox.baseVal.width/r.width,
        best=0;
    pts.forEach(function(p,k){if(Math.abs(p.x-x)<Math.abs(pts[best].x-x))best=k;});
    show(best);
  });
  svg.addEventListener('pointerleave',hide);svg.addEventListener('blur',hide);
  svg.addEventListener('focus',function(){show(i<0?pts.length-1:i);});
  svg.addEventListener('keydown',function(ev){
    if(ev.key==='ArrowLeft'){show(i-1);ev.preventDefault();}
    if(ev.key==='ArrowRight'){show(i+1);ev.preventDefault();}
  });
});
"""


def page(title: str, body: str, script: str = "") -> str:
    js = f"<script>{script}</script>" if script else ""
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{e(title)}</title><style>{CSS}</style></head>"
        f"<body><main>{body}</main>{js}</body></html>\n"
    )


def tile(label: str, value: str, detail: str = "") -> str:
    d = f'<div class="d">{detail}</div>' if detail else ""
    return f'<div class="tile"><div class="l">{e(label)}</div><div class="v">{value}</div>{d}</div>'


def signed_cls(x: float | None) -> str:
    return "" if x is None or abs(x) < 0.005 else ("pos" if x > 0 else "neg")


def colored(x: float | None, text: str) -> str:
    c = signed_cls(x)
    return f'<span class="{c}">{text}</span>' if c else text


def table(headers: list[str], rows: list[list[str]], numeric: set[int] | None = None) -> str:
    numeric = numeric or set()
    th = "".join(
        f'<th class="{"n" if i in numeric else ""}">{h}</th>' for i, h in enumerate(headers)
    )
    trs = "".join(
        "<tr>"
        + "".join(f'<td class="{"n" if i in numeric else ""}">{c}</td>' for i, c in enumerate(r))
        + "</tr>"
        for r in rows
    )
    return (
        f'<div class="scroll"><table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table></div>'
    )


STATUS = {
    "active": ("s-good", "✓", "active"),
    "paused": ("s-warn", "‖", "paused"),
    "halted": ("s-crit", "!", "halted"),
    "closed": ("s-serious", "✕", "closed"),
    "new": ("s-warn", "…", "new"),
}


def status_badge(status: str) -> str:
    cls, icon, label = STATUS.get(status, ("s-warn", "?", status))
    return f'<span class="badge"><span class="dot {cls}"></span>{icon} {e(label)}</span>'


def chain_badge(view: FirmView) -> str:
    v = view.verify
    if v.ok:
        return (
            '<span class="badge"><span class="dot s-good"></span>'
            f"✓ hash chain verified: {v.n_events:,} events</span>"
        )
    return (
        '<span class="badge"><span class="dot s-crit"></span>'
        f"✕ hash chain BROKEN at event {e(v.bad_seq)}: {e(v.reason)}</span>"
    )


def caveats_block() -> str:
    items = "".join(f"<li>{e(c)}</li>" for c in CAVEATS)
    return (
        '<div class="card"><h2 style="margin-top:0">What this cannot test</h2>'
        f'<ul class="caveats">{items}</ul></div>'
    )


def header(view: FirmView, title: str, crumb: str = "") -> str:
    return (
        f'<p class="sub">{crumb}Dog Research Capital · paper funds · as of {when(view.as_of)}</p>'
        f"<h1>{e(title)}</h1><p>{chain_badge(view)} "
        '<span class="badge">valued at liquidation (best bid), never mid</span></p>'
    )


def footer(view: FirmView) -> str:
    src = f" from {e(view.ledger)}" if view.ledger else ""
    return (
        f"<footer>Generated by drc-report{src}: {view.n_events:,} events. Every number is a "
        "projection of the append-only event log; regenerate rather than edit.</footer>"
    )


# ---- NAV chart -----------------------------------------------------------------------------
def _ticks(lo: float, hi: float, n: int = 4) -> list[float]:
    span = hi - lo
    raw = span / n
    mag = 10 ** int(f"{raw:e}".split("e")[1])
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start = int(lo // step) * step
    out, t = [], start
    while t <= hi + 1e-9:
        if t >= lo - 1e-9:
            out.append(round(t, 10))
        t += step
    return out


def nav_chart(points: tuple[NavPoint, ...], as_of: int) -> str:
    known = [p for p in points if p.nav is not None]
    if not known:
        return '<p class="sub">No liquidation-marked NAV yet.</p>'
    w, h, ml, mr, mt, mb = 720, 260, 72, 92, 14, 30
    x0 = points[0].ts
    x1 = max(as_of, points[-1].ts)
    if x1 <= x0:
        x1 = x0 + 1
    vals = [p.nav for p in known] + [p.allocated for p in points]
    lo, hi = min(vals), max(vals)
    pad = max((hi - lo) * 0.1, abs(hi) * 0.002, 1.0)
    lo, hi = lo - pad, hi + pad
    ticks = _ticks(lo, hi)
    lo, hi = min(lo, ticks[0]), max(hi, ticks[-1])

    def sx(t: int) -> float:
        return ml + (t - x0) / (x1 - x0) * (w - ml - mr)

    def sy(v: float) -> float:
        return mt + (hi - v) / (hi - lo) * (h - mt - mb)

    def step_path(vals: list[tuple[int, float | None]]) -> str:
        """Step line: each value holds until the next event; None breaks the line."""
        d, pen = [], False
        for k, (t, v) in enumerate(vals):
            t_next = vals[k + 1][0] if k + 1 < len(vals) else x1
            if v is None:
                pen = False
                continue
            d.append(f"{'L' if pen else 'M'}{sx(t):.1f},{sy(v):.1f}")
            d.append(f"L{sx(t_next):.1f},{sy(v):.1f}")
            pen = True
        return "".join(d)

    grid = "".join(
        f'<line x1="{ml}" x2="{w - mr}" y1="{sy(t):.1f}" y2="{sy(t):.1f}" stroke="var(--grid)"/>'
        f'<text x="{ml - 8}" y="{sy(t) + 4:.1f}" text-anchor="end" font-size="11" '
        f'fill="var(--muted)">{e(money(t).replace(".00", ""))}</text>'
        for t in ticks
    )
    xlab = (
        f'<text x="{ml}" y="{h - 8}" font-size="11" fill="var(--muted)">{e(when(x0))}</text>'
        f'<text x="{w - mr}" y="{h - 8}" font-size="11" text-anchor="end" '
        f'fill="var(--muted)">{e(when(x1))}</text>'
        f'<line x1="{ml}" x2="{w - mr}" y1="{h - mb}" y2="{h - mb}" stroke="var(--axis)"/>'
    )
    alloc = step_path([(p.ts, p.allocated) for p in points])
    nav = step_path([(p.ts, p.nav) for p in points])
    last = known[-1]
    end_x, end_y = sx(x1), sy(last.nav)
    alloc_y = sy(points[-1].allocated)
    gap_y = abs(end_y - alloc_y) < 14
    lab_alloc_y = alloc_y + (12 if gap_y and alloc_y >= end_y else -6 if not gap_y else -10)
    labels = (
        f'<text x="{end_x + 10}" y="{end_y + 4:.1f}" font-size="12" font-weight="600" '
        f'fill="var(--ink)">{e(money(last.nav))}</text>'
        f'<text x="{end_x + 10}" y="{lab_alloc_y + 4:.1f}" font-size="11" '
        f'fill="var(--muted)">allocated</text>'
    )
    gaps = sum(1 for p in points if p.nav is None)
    data = [
        {
            "x": round(sx(p.ts), 1),
            "nav": money(p.nav) if p.nav is not None else "NAV unknown (unmarked position)",
            "alloc": money(p.allocated),
            "when": when(p.ts),
            "cause": p.cause.replace("_", " "),
        }
        for p in points
    ]
    svg = (
        f'<svg viewBox="0 0 {w} {h}" role="img" tabindex="0" '
        'aria-label="Liquidation-marked NAV over time; use arrow keys to step through points">'
        f"{grid}{xlab}"
        f'<path d="{alloc}" fill="none" stroke="var(--muted)" stroke-width="1"/>'
        f'<path d="{nav}" fill="none" stroke="var(--series)" stroke-width="2" '
        'stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{end_x:.1f}" cy="{end_y:.1f}" r="4" fill="var(--series)" '
        'stroke="var(--surface)" stroke-width="2"/>'
        f"{labels}"
        f'<line class="cross" y1="{mt}" y2="{h - mb}" stroke="var(--axis)" style="display:none"/>'
        "</svg>"
    )
    payload = e(json.dumps(data))
    legend = (
        '<p class="sub"><span class="key" style="background:var(--series)"></span>NAV at '
        'liquidation value &nbsp; <span class="key" style="background:var(--muted)"></span>'
        "net allocated capital"
        + (
            f" &nbsp; · line breaks mark {gaps} stretch(es) with an unmarked position"
            if gaps
            else ""
        )
        + "</p>"
    )
    rows = [
        [e(when(p.ts)), money(p.nav), money(p.allocated), e(p.cause.replace("_", " "))]
        for p in points
    ]
    tbl = table(["time", "NAV (bid)", "net allocated", "after"], rows, {1, 2})
    return (
        f'<div class="chart" data-points="{payload}">{svg}<div class="tip"></div></div>{legend}'
        f"<details><summary>NAV data table ({len(points)} points)</summary>{tbl}</details>"
    )


# ---- firm page -----------------------------------------------------------------------------
def render_firm(view: FirmView) -> str:
    f = view.firm
    parts = [header(view, "Firm report")]
    if not f.funds:
        parts.append(
            '<div class="card"><h2 style="margin-top:0">No funds yet</h2><p>The event log has '
            f"{view.n_events:,} events and no <code>fund_created</code> event. Nothing has been "
            "allocated, so there is nothing to value.</p></div>"
        )
        parts += [caveats_block(), footer(view)]
        return page("DRC firm report", "".join(parts))

    unmarked = [s.fund_id for s in f.funds if s.nav is None]
    if unmarked:
        parts.append(
            '<div class="flag crit"><b>Incomplete totals.</b> NAV and PnL below exclude '
            f"{len(unmarked)} fund(s) with an unmarked open position ("
            + ", ".join(e(x) for x in unmarked)
            + "). We do not guess a value.</div>"
        )
    n_closed = sum(1 for s in f.funds if s.status in ("closed", "halted"))
    parts.append(
        '<div class="tiles">'
        + tile("Allocated, all funds ever", money(f.allocated), "net of withdrawals")
        + tile("NAV (liquidation)", money(f.nav), "cash + positions at best bid")
        + tile(
            "PnL to date",
            colored(f.pnl, money(f.pnl, signed=True)),
            f"includes {n_closed} closed/halted fund(s)",
        )
        + tile("Hypotheses tested", f"{f.n_hypotheses:,}", "one fund per hypothesis")
        + "</div>"
    )
    parts.append(
        f'<p class="sub">Read the PnL next to the hypothesis count: the best of {f.n_hypotheses} '
        "tries looks better than any single honest test. Killed funds stay in these totals: "
        "their losses are part of what finding a live edge cost.</p>"
    )
    rows = []
    for fv in view.funds:
        s = fv.summary
        nflags = len(fv.flags) + len(fv.sessions.missed) + len(fv.reconciliation_failures)
        rows.append(
            [
                f'<a href="{fund_file(s.fund_id)}">{e(s.fund_id)}</a>',
                status_badge(s.status),
                money(s.allocated),
                money(s.nav),
                colored(s.pnl, money(s.pnl, signed=True)),
                colored(s.ret, pct(s.ret)),
                colored(s.ret_per_dollar_day, per_day(s.ret_per_dollar_day)),
                f"{s.n_fills:,}",
                f"{nflags:,}" if nflags else "0",
            ]
        )
    parts.append("<h2>Funds</h2>")
    parts.append(
        table(
            ["fund", "status", "allocated", "NAV", "PnL", "return", "per $-day", "fills", "flags"],
            rows,
            {2, 3, 4, 5, 6, 7, 8},
        )
    )
    parts.append(
        '<p class="sub">return = PnL / net allocated. per $-day = PnL / dollar-days of capital '
        "deployed in positions (see a fund page). flags = manual or automatic status changes, "
        "missed sessions and failed reconciliations.</p>"
    )
    parts += [caveats_block(), footer(view)]
    return page("DRC firm report", "".join(parts))


# ---- fund page -----------------------------------------------------------------------------
def _flags_block(fv: FundView) -> str:
    out = []
    for r in fv.reconciliation_failures:
        out.append(
            f'<div class="flag crit"><b>Reconciliation failed</b> {e(when(r.ts))}: '
            f"{e(r.reason)}</div>"
        )
    for r in fv.flags:
        kind = r.type.replace("fund_", "")
        crit = " crit" if kind in ("halted", "closed") else ""
        out.append(
            f'<div class="flag{crit}"><b>Fund {e(kind)}</b> {e(when(r.ts))}: {e(r.reason)}</div>'
        )
    for r in fv.sessions.missed:
        out.append(
            f'<div class="flag"><b>Missed session</b> {e(r.session_id)}: {e(r.reason)} '
            "(never backfilled)</div>"
        )
    for sid in fv.sessions.unfinished:
        out.append(f'<div class="flag"><b>Session started, never finished</b> {e(sid)}</div>')
    for p in fv.stale_or_missing_marks:
        what = "has no mark" if p.mark is None else f"mark is from {e(when(p.mark_ts))}"
        out.append(
            f'<div class="flag"><b>Position {e(p.token)}</b> {what}; its value is not current.'
            "</div>"
        )
    return "".join(out)


def _prediction_block(fv: FundView) -> str:
    pr, ed = fv.prediction, fv.edge
    if pr is None:
        head = (
            f'<p class="sub">No pre-registered prediction available ({e(fv.prediction_note)}). '
            f"The power note below assumes a per-contract sd of {DEFAULT_SD:.2f}.</p>"
        )
    else:
        ci = (
            f" (CI {cents(pr.ci_low)} to {cents(pr.ci_high)})"
            if pr.ci_low is not None and pr.ci_high is not None
            else ""
        )
        head = (
            f"<p>Predicted net edge: <b>{cents(pr.edge)}</b> per contract{ci}, from "
            f"{e(pr.source)}. Test: two-sided, alpha {pr.alpha:g}, power {pr.power:.0%}; "
            f"per-contract sd {pr.sd:.2f} ({e(pr.sd_source)}).</p>"
        )
    diff = None if ed.realized is None or pr is None else ed.realized - pr.edge
    rows = [
        ["Settled positions (win/lose)", f"{ed.n_positions:,}"],
        ["Independent events among them", f"{ed.n_events:,}"],
        ["Contracts settled", num(ed.n_contracts, 0)],
        ["Voided (excluded, fees still in PnL)", f"{ed.n_voids:,}"],
        ["Realized net edge per contract", colored(ed.realized, cents(ed.realized))],
        [
            "Realized interval",
            NA if ed.ci_low is None else f"{cents(ed.ci_low)} to {cents(ed.ci_high)}",
        ],
        ["Realized minus predicted (the headline)", colored(diff, cents(diff))],
        ["Minimum detectable edge at this sample", cents(ed.mde, signed=False)],
        [
            "Events needed to detect the prediction",
            NA if ed.needed_events is None else f"~{ed.needed_events:,}",
        ],
    ]
    if pr and pr.min_events:
        rows.append(["Charter's minimum before judging", f"{pr.min_events:,}"])
    note = (
        f'<p><b>Verdict: {e(ed.verdict)}.</b></p><p class="sub">Each contract pays $0 or $1, '
        f"so one trade's outcome has a standard deviation near {ed.sd * 100:.0f}c while the "
        "edge we hope for is about a cent. The minimum detectable edge is the smallest true edge "
        "this many independent events would reveal with the stated power; anything smaller is "
        "noise at this sample size. Until the sample reaches the needed count, judge the cost "
        "model (fills, spreads, fees) and call the edge inconclusive.</p>"
    )
    return head + table(["", ""], rows, {1}) + note


def render_fund(view: FirmView, fv: FundView) -> str:
    s = fv.summary
    parts = [
        header(view, f"{s.fund_id}", crumb='<a href="index.html">Firm</a> › '),
        f'<p>{status_badge(s.status)} <span class="sub">{e(s.name)} · created '
        f"{e(when(fv.created_ts))} · charter {e(fv.charter_hash[:12] or 'n/a')} · git "
        f"{e(fv.git_sha or 'n/a')}</span></p>",
        _flags_block(fv),
    ]
    if s.nav is None:
        parts.append(
            '<div class="flag crit"><b>NAV unknown.</b> An open position has no mark, so NAV and '
            "PnL are not shown. We never fall back to the mid or the cost.</div>"
        )
    parts.append(
        '<div class="tiles">'
        + tile("NAV (liquidation)", money(s.nav), "cash + positions at best bid")
        + tile(
            "PnL",
            colored(s.pnl, money(s.pnl, signed=True)),
            f"on {money(s.allocated)} net allocated",
        )
        + tile("Return on allocated", colored(s.ret, pct(s.ret)))
        + tile(
            "Return per dollar-day",
            colored(s.ret_per_dollar_day, per_day(s.ret_per_dollar_day)),
            f"{num(s.dollar_days, 0)} dollar-days deployed",
        )
        + tile("Deployed / cash", f"{money(s.deployed)}", f"cash {money(s.cash)}")
        + "</div>"
    )

    parts.append("<h2>NAV over time</h2>")
    parts.append(nav_chart(fv.nav_points, view.as_of))
    parts.append(
        f'<p class="sub">A fill and the mark that follows it within {BLIP_NS // 60_000_000_000} '
        "minutes are drawn as one step; a longer unmarked stretch is a gap, never "
        "interpolated.</p>"
    )

    parts.append("<h2>Predicted vs realized</h2>")
    parts.append(_prediction_block(fv))

    c = fv.costs
    parts.append("<h2>Cost attribution</h2>")
    parts.append(
        table(
            ["", "dollars"],
            [
                [
                    "Edge vs mid (trades valued at mid, no fees)",
                    colored(c.edge_vs_mid, money(c.edge_vs_mid, True)),
                ],
                ["− spread paid crossing the book", money(-c.spread, True)],
                ["− venue fees", money(-c.fees, True)],
                ["= net PnL", colored(c.net, money(c.net, True))],
                ["of which realized (settled), before fees", money(c.realized_gross, True)],
                ["of which unrealized (open, at bid)", money(c.unrealized, True)],
            ],
            {1},
        )
    )
    parts.append(
        '<p class="sub">Spread paid = for each fill, (fill price − mid of the book it filled '
        "against) × quantity. Open positions are valued at the bid, so the half-spread to exit "
        "is already inside unrealized PnL.</p>"
    )

    ad = avg_deployed(fv)
    parts.append("<h2>Capital locked up</h2>")
    parts.append(
        table(
            ["", ""],
            [
                ["Days since creation", num(fv.days_live, 1)],
                ["Dollar-days deployed", num(s.dollar_days, 1)],
                ["Average capital deployed", money(ad)],
                [
                    "Return per contract bought",
                    colored(fv.ret_per_contract, cents(fv.ret_per_contract)),
                ],
                ["Return per dollar allocated", colored(s.ret, pct(s.ret))],
                [
                    "Return per dollar-day deployed",
                    colored(s.ret_per_dollar_day, per_day(s.ret_per_dollar_day)),
                ],
                ["Idle cash (cash drag)", money(s.cash)],
            ],
            {1},
        )
    )

    parts.append("<h2>Open positions</h2>")
    if fv.positions:
        rows = [
            [
                e(p.token),
                e(p.event_id),
                num(p.qty),
                price(p.avg_cost),
                price(p.mark),
                e(when(p.mark_ts)) + (" (stale)" if p.stale else ""),
                money(p.cost_basis),
                money(p.value),
                colored(p.unrealized, money(p.unrealized, True)),
            ]
            for p in fv.positions
        ]
        parts.append(
            table(
                [
                    "token",
                    "event",
                    "qty",
                    "avg cost",
                    "bid mark",
                    "marked",
                    "cost",
                    "value",
                    "unrealized",
                ],
                rows,
                {2, 3, 4, 6, 7, 8},
            )
        )
        parts.append(
            f'<p class="sub">A mark older than {MARK_STALE_NS // 3_600_000_000_000} hours at '
            "report time is flagged stale.</p>"
        )
    else:
        parts.append('<p class="sub">No open positions.</p>')

    parts.append("<h3>Exposure by event</h3>")
    if fv.exposure:
        nav = s.nav
        rows = [
            [
                e(x.event_id),
                f"{x.n_tokens}",
                money(x.cost_basis),
                money(x.value),
                NA if not nav else f"{x.cost_basis / nav:.1%}",
            ]
            for x in fv.exposure
        ]
        parts.append(
            table(
                ["event", "tokens", "at risk (cost)", "value (bid)", "of NAV"], rows, {1, 2, 3, 4}
            )
        )
        parts.append(
            '<p class="sub">At risk = cost basis, the most a long binary position can lose. '
            "Contracts on one real-world event move together, so exposure is grouped by event.</p>"
        )
    else:
        parts.append('<p class="sub">Nothing at risk.</p>')

    parts.append("<h2>Blotter</h2>")
    if fv.blotter:
        rows = [
            [
                e(when(b.ts)),
                e(b.order_id),
                e(b.token),
                e(b.side),
                num(b.qty),
                price(b.price),
                price(b.mid),
                money(b.spread_cost),
                money(b.fee),
                f"{b.n_levels}",
                e(b.status),
            ]
            for b in fv.blotter
        ]
        parts.append(
            table(
                [
                    "time",
                    "order",
                    "token",
                    "side",
                    "qty",
                    "price",
                    "mid",
                    "spread paid",
                    "fee",
                    "levels",
                    "status",
                ],
                rows,
                {4, 5, 6, 7, 8, 9},
            )
        )
    else:
        parts.append('<p class="sub">No fills.</p>')

    parts.append("<h3>Settlements</h3>")
    if fv.settlements:
        rows = [
            [
                e(when(x.ts)),
                e(x.token),
                e(x.event_id),
                e(x.kind),
                price(x.value),
                num(x.qty),
                price(x.avg_cost),
                money(x.fees),
                colored(x.net, money(x.net, True)),
            ]
            for x in fv.settlements
        ]
        parts.append(
            table(
                ["time", "token", "event", "outcome", "pays", "qty", "avg cost", "fees", "net PnL"],
                rows,
                {4, 5, 6, 7, 8},
            )
        )
    else:
        parts.append('<p class="sub">Nothing settled yet.</p>')

    parts.append("<h2>Orders and candidates</h2><h3>Order outcomes</h3>")
    if fv.order_outcomes:
        parts.append(
            table(
                ["outcome", "reason", "count"],
                [[e(o), e(r or "-"), f"{n:,}"] for o, r, n in fv.order_outcomes],
                {2},
            )
        )
    else:
        parts.append('<p class="sub">No orders.</p>')
    parts.append("<h3>Candidates seen and why they were skipped</h3>")
    if fv.candidates:
        parts.append(
            table(
                ["verdict", "reason", "count"],
                [[e(v or "-"), e(r or "-"), f"{n:,}"] for v, r, n in fv.candidates],
                {2},
            )
        )
    else:
        parts.append('<p class="sub">No <code>market_observed</code> events logged.</p>')

    ss = fv.sessions
    parts.append("<h2>Sessions and controls</h2>")
    parts.append(
        table(
            ["", ""],
            [
                ["Sessions started", f"{len(ss.started):,}"],
                ["Sessions finished", f"{len(ss.finished):,}"],
                ["Sessions missed (never backfilled)", f"{len(ss.missed):,}"],
                ["Started but not finished", f"{len(ss.unfinished):,}"],
                ["Reconciliations passed", f"{fv.reconciliations_ok:,}"],
                ["Reconciliations failed", f"{len(fv.reconciliation_failures):,}"],
                ["Status changes (pause, resume, halt, close)", f"{len(fv.flags):,}"],
            ],
            {1},
        )
    )
    if fv.flags:
        parts.append(
            table(
                ["time", "event", "reason"],
                [[e(when(r.ts)), e(r.type), e(r.reason)] for r in fv.flags],
            )
        )
    parts += [caveats_block(), footer(view)]
    return page(f"DRC fund {s.fund_id}", "".join(parts), CHART_JS)
