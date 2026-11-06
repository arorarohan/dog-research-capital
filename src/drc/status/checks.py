"""Health checks for a recorder machine. Each check runs read-only shell probes through a
`Runner` and turns their output into a `Check` (ok / warn / error / skip).

Every probe here only reads: `systemctl show`, `journalctl`, `find`, `cat`, `zstdcat`, `df`,
`du`, `date` (and, opt-in, `rclone about` / `drc-sync verify`). Nothing starts, stops, writes or
deletes. `tests/test_status.py` enforces that list.

"Now" is the target machine's clock (`date +%s` on it), not the laptop's: the WSL2 clock drifts
(docs/gotchas.md), and every age here is measured against timestamps the target wrote.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

from drc.status.runner import Result, Runner

OK, WARN, ERROR, SKIP = "ok", "warn", "error", "skip"
RANK = {SKIP: 0, OK: 0, WARN: 1, ERROR: 2}
DAY = 86400


@dataclass
class Check:
    name: str
    status: str
    summary: str
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Thresholds:
    """Defaults mirror config/status.toml; the file wins. Ages in the units their names say."""

    raw_age_warn_min: float = 10
    raw_age_error_min: float = 30
    gaps_warn_per_day: int = 10
    gaps_error_per_day: int = 50
    gap_long_warn_secs: float = 120
    poll_errors_warn_per_day: int = 20
    poll_errors_error_per_day: int = 200
    code1013_warn_per_day: int = 3
    code1013_error_per_day: int = 20
    sync_age_warn_hours: float = 2.5
    sync_age_error_hours: float = 6
    auth_warn_days: float = 5
    auth_expire_days: float = 7
    disk_warn_pct: float = 80
    disk_error_pct: float = 90
    raw_cap_warn_pct: float = 85
    drive_free_warn_gb: float = 50

    @classmethod
    def from_dict(cls, d: dict) -> Thresholds:
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        unknown = set(d) - set(known)
        if unknown:
            raise ValueError(f"unknown thresholds in config: {sorted(unknown)}")
        return cls(**known)


@dataclass
class Target:
    """One machine to check (a profile in config/status.toml). Empty/None disables a check."""

    name: str
    repo: str = "."
    raw_dir: str | None = None
    venues: list[str] = field(default_factory=list)
    recorder_service: str | None = None
    sync_service: str | None = None
    auth_stamp: str | None = "~/.config/rclone/drc-authorized-at"
    raw_cap_gb: float | None = None
    probe_drive: bool = False
    drive_remote: str = "gdrive:"
    verify_host: str | None = None  # run `drc-sync verify` as this host (slow; opt-in)
    history_days: int = 7


class ProbeError(RuntimeError):
    pass


class Probe:
    """Runs shell probes on one target; records every command (tests assert they only read)."""

    def __init__(self, run: Runner):
        self._run = run
        self.commands: list[str] = []

    def sh(self, cmd: str, timeout: float = 60, ok_codes: tuple[int, ...] = (0,)) -> Result:
        self.commands.append(cmd)
        r = self._run(cmd, timeout)
        if r.rc not in ok_codes:
            tail = " ".join((r.err or r.out).strip().splitlines()[-2:])
            raise ProbeError(f"exit {r.rc}: {tail or 'no output'}")
        return r


# ---------------------------------------------------------------- formatting helpers
def fmt_age(secs: float) -> str:
    secs = max(0.0, secs)
    if secs < 120:
        return f"{secs:.0f}s"
    if secs < 7200:
        return f"{secs / 60:.0f} min"
    if secs < 2 * DAY:
        return f"{secs / 3600:.1f} h"
    return f"{secs / DAY:.1f} days"


def fmt_ts(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%MZ", time.gmtime(ts))


def worst(statuses) -> str:
    return max(statuses, key=lambda s: RANK[s], default=OK)


def _level(value: float, warn: float, error: float) -> str:
    return ERROR if value >= error else WARN if value >= warn else OK


def parse_show(text: str) -> list[dict[str, str]]:
    """`systemctl show` output: blocks of KEY=VALUE separated by blank lines, one per unit."""
    blocks, cur = [], {}
    for line in text.splitlines():
        if not line.strip():
            if cur:
                blocks.append(cur)
            cur = {}
            continue
        k, _, v = line.partition("=")
        cur[k.strip()] = v.strip()
    if cur:
        blocks.append(cur)
    return blocks


def _unix(v: str | None) -> float | None:
    """`--timestamp=unix` values look like `@1793959294`; empty or `n/a` means never."""
    if v and v.startswith("@"):
        try:
            return float(v[1:])
        except ValueError:
            return None
    return None


_JOURNAL = re.compile(r"^(\d+(?:\.\d+)?) \S+ [^:]+: (.*)$")


def parse_journal(text: str) -> list[tuple[float, str]]:
    """`journalctl -o short-unix` lines -> (timestamp, message)."""
    out = []
    for line in text.splitlines():
        m = _JOURNAL.match(line)
        if m:
            out.append((float(m.group(1)), m.group(2)))
    return out


# ---------------------------------------------------------------- the checks
def check_recorder_service(p: Probe, t: Target, th: Thresholds, now: float, ctx: dict) -> Check:
    unit = t.recorder_service
    r = p.sh(
        f"systemctl show {unit} -p ActiveState -p SubState -p NRestarts -p ActiveEnterTimestamp "
        "--timestamp=unix"
    )
    info = (parse_show(r.out) or [{}])[0]
    state, sub = info.get("ActiveState", "?"), info.get("SubState", "?")
    since = _unix(info.get("ActiveEnterTimestamp"))
    restarts = int(info.get("NRestarts") or 0)
    details = {
        "unit": unit,
        "active_state": state,
        "sub_state": sub,
        "since": since,
        "restarts": restarts,
    }
    if state != "active":
        return Check("recorder service", ERROR, f"{unit} is {state} ({sub})", details)
    ctx["recorder_since"] = since
    up = f"up {fmt_age(now - since)} (since {fmt_ts(since)})" if since else "up"
    status = WARN if restarts else OK  # systemd restarted it after a crash
    extra = f", {restarts} crash restarts" if restarts else ""
    return Check("recorder service", status, f"{unit} {state}/{sub}, {up}{extra}", details)


def check_raw_freshness(p: Probe, t: Target, th: Thresholds, now: float, ctx: dict) -> Check:
    window = int(th.raw_age_error_min * 4) + 60
    r = p.sh(
        f"cd {t.repo} && find {t.raw_dir} -type f -mmin -{window} -printf '%T@ %P\\n'",
        ok_codes=(0, 1),
    )
    newest: dict[str, dict[str, float]] = defaultdict(dict)
    for line in r.out.splitlines():
        mtime, _, rel = line.partition(" ")
        parts = rel.split("/")
        if len(parts) < 3 or parts[1].startswith("_"):  # _meta / _catalog are not market data
            continue
        venue, channel = parts[0], parts[1]
        try:
            m = float(mtime)
        except ValueError:
            continue
        newest[venue][channel] = max(newest[venue].get(channel, 0.0), m)
    statuses, parts_out, details = [], [], {}
    for venue in t.venues:
        chans = newest.get(venue)
        if not chans:
            statuses.append(ERROR)
            parts_out.append(f"{venue} NOTHING in {window} min")
            details[venue] = None
            continue
        age = now - max(chans.values())
        statuses.append(_level(age / 60, th.raw_age_warn_min, th.raw_age_error_min))
        parts_out.append(f"{venue} {fmt_age(age)}")
        details[venue] = {
            "age_secs": round(age, 1),
            "channels": {c: round(now - m, 1) for c, m in sorted(chans.items())},
        }
    return Check(
        "raw freshness",
        worst(statuses),
        "newest raw file: " + ", ".join(parts_out),
        {"raw_dir": t.raw_dir, "venues": details},
    )


_META_EVENTS = "connect|disconnect|shutdown|poll_errors|throttled|catalog_error"


def meta_command(t: Target, minutes: int) -> str:
    """Today's and yesterday's `_meta` events, without the big asset lists. Each file is
    preceded by a `## <path>` line, because the venue is only in the path."""
    return (
        f"cd {t.repo} && find {t.raw_dir}/*/_meta -type f -mmin -{minutes} 2>/dev/null | sort | "
        'while read -r f; do echo "## $f"; case "$f" in '
        '*.zst) zstdcat -- "$f" 2>/dev/null ;; *) cat -- "$f" 2>/dev/null ;; esac; done | '
        f'grep -E \'^## |"ev":"({_META_EVENTS})"\' | '
        "sed -E 's/,\"assets\":\\[[^]]*\\]//'"
    )


def parse_meta(text: str, raw_dir: str) -> list[dict]:
    events, venue = [], None
    prefix = raw_dir.rstrip("/") + "/"
    for line in text.splitlines():
        if line.startswith("## "):
            path = line[3:].strip()
            rel = path[len(prefix) :] if path.startswith(prefix) else path
            venue = rel.split("/")[0]
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict) and "t" in ev and "ev" in ev and venue:
            ev["venue"] = venue
            events.append(ev)
    events.sort(key=lambda e: e["t"])
    return events


def summarize_gaps(events: list[dict], since_ns: int) -> dict[str, dict]:
    """Per venue: unplanned disconnects (gaps), restarts, gap durations (disconnect or shutdown
    until the same connection's next connect), poll errors, throttles. Gaps still open at the end
    are counted in `open`."""
    out: dict[str, dict] = defaultdict(
        lambda: {
            "disconnects": 0,
            "code1013": 0,
            "restarts": 0,
            "poll_errors": 0,
            "throttled": 0,
            "catalog_errors": 0,
            "gap_secs_total": 0.0,
            "gap_secs_max": 0.0,
            "open": 0,
        }
    )
    open_at: dict[tuple, int] = {}
    for e in events:
        v, name, t_ns = e["venue"], e["ev"], int(e["t"])
        key = (v, e.get("shard"))
        if name in ("disconnect", "shutdown"):
            open_at.setdefault(key, t_ns)
        elif name == "connect":
            start = open_at.pop(key, None)
            if start is None:  # restart across a version that did not tag shards
                start = open_at.pop((v, None), None)
            if start is not None and t_ns >= since_ns:
                s = out[v]
                d = (t_ns - start) / 1e9
                s["gap_secs_total"] += d
                s["gap_secs_max"] = max(s["gap_secs_max"], d)
        if t_ns < since_ns:
            continue
        s = out[v]
        if name == "disconnect":
            s["disconnects"] += 1
            s["code1013"] += "1013" in str(e.get("reason", ""))
        elif name == "shutdown":
            s["restarts"] += 1
        elif name == "poll_errors":
            s["poll_errors"] += int(e.get("n", 1))
        elif name == "throttled":
            s["throttled"] += int(e.get("n", 1))
        elif name == "catalog_error":
            s["catalog_errors"] += 1
    for (v, _), start in open_at.items():
        if start >= since_ns:
            out[v]["open"] += 1
    for s in out.values():
        s["gap_secs_total"] = round(s["gap_secs_total"], 1)
        s["gap_secs_max"] = round(s["gap_secs_max"], 1)
    return dict(out)


def check_gaps(p: Probe, t: Target, th: Thresholds, now: float, ctx: dict) -> Check:
    r = p.sh(meta_command(t, 24 * 60 + 90), timeout=120, ok_codes=(0, 1))
    by_venue = summarize_gaps(parse_meta(r.out, t.raw_dir), int((now - DAY) * 1e9))
    total_gaps = sum(s["disconnects"] for s in by_venue.values())
    poll = sum(s["poll_errors"] for s in by_venue.values())
    longest = max((s["gap_secs_max"] for s in by_venue.values()), default=0.0)
    status = worst(
        [
            _level(total_gaps, th.gaps_warn_per_day, th.gaps_error_per_day),
            _level(poll, th.poll_errors_warn_per_day, th.poll_errors_error_per_day),
            WARN if longest >= th.gap_long_warn_secs else OK,
        ]
    )
    parts = []
    for v in sorted(set(t.venues) | set(by_venue)):
        s = by_venue.get(v)
        if not s:
            parts.append(f"{v} 0 gaps")
            continue
        bits = [f"{s['disconnects']} gaps"]
        if s["disconnects"] or s["restarts"]:
            bits.append(f"longest {s['gap_secs_max']:.0f}s, total {s['gap_secs_total']:.0f}s")
        for k, label in (
            ("restarts", "restarts"),
            ("poll_errors", "poll errors"),
            ("throttled", "throttled"),
            ("catalog_errors", "catalog errors"),
            ("open", "still open"),
        ):
            if s[k]:
                bits.append(f"{s[k]} {label}")
        parts.append(f"{v} " + ", ".join(bits))
    return Check("feed gaps (24h)", status, "; ".join(parts), {"venues": by_venue})


def check_1013(p: Probe, t: Target, th: Thresholds, now: float, ctx: dict) -> Check:
    days = t.history_days
    r = p.sh(
        f"journalctl -u {t.recorder_service} --since '-{days} days' -o short-unix --no-pager "
        "| grep -F 'code=1013' | cut -d' ' -f1",
        timeout=120,
        ok_codes=(0, 1),
    )
    stamps = []
    for tok in r.out.split():
        try:
            stamps.append(float(tok))
        except ValueError:
            continue
    per_day = Counter(time.strftime("%Y-%m-%d", time.gmtime(s)) for s in stamps)
    first_day = now - (days - 1) * DAY
    table = {time.strftime("%Y-%m-%d", time.gmtime(first_day + i * DAY)): 0 for i in range(days)}
    for d, n in per_day.items():
        if d in table:
            table[d] = n
    last24 = sum(1 for s in stamps if s >= now - DAY)
    details = {"last_24h": last24, "per_day_utc": table}
    since = ctx.get("recorder_since")
    extra = ""
    if since:
        n_since = sum(1 for s in stamps if s >= since)
        details["since_recorder_start"] = n_since
        extra = f"; {n_since} since recorder start ({fmt_age(now - since)} ago)"
    if not stamps and r.err.strip():
        return Check("1013 closes", WARN, f"journal not readable: {r.err.strip()[:120]}", details)
    status = _level(last24, th.code1013_warn_per_day, th.code1013_error_per_day)
    daily = " ".join(f"{d[5:]}:{n}" for d, n in table.items())
    return Check("1013 closes", status, f"last 24h: {last24}{extra}; per day {daily}", details)


def check_sync(p: Probe, t: Target, th: Thresholds, now: float, ctx: dict) -> list[Check]:
    """The archive timer, its last successful cycle, and that cycle's verify result."""
    unit = t.sync_service
    r = p.sh(
        f"systemctl show {unit}.timer {unit}.service -p Id -p ActiveState -p UnitFileState "
        "-p InactiveExitTimestamp -p NextElapseUSecRealtime --timestamp=unix"
    )
    units = {b.get("Id", ""): b for b in parse_show(r.out)}
    timer, svc = units.get(f"{unit}.timer", {}), units.get(f"{unit}.service", {})
    t_state, t_file = timer.get("ActiveState", "?"), timer.get("UnitFileState", "?")
    nxt = timer.get("NextElapseUSecRealtime") or ""
    running = svc.get("ActiveState") == "activating"  # a oneshot is "activating" while it runs
    running_since = _unix(svc.get("InactiveExitTimestamp")) if running else None
    if _unix(nxt):
        nxt = fmt_ts(_unix(nxt))
    timer_ok = t_state == "active" and t_file == "enabled"
    t_summary = f"{unit}.timer {t_state}, {t_file}"
    if running:
        t_summary += f"; a cycle is running (started {fmt_age(now - (running_since or now))} ago)"
    elif nxt:
        t_summary += f"; next {nxt}"
    checks = [
        Check(
            "sync timer",
            OK if timer_ok else ERROR,
            t_summary,
            {
                "active_state": t_state,
                "unit_file_state": t_file,
                "next": nxt,
                "running_since": running_since,
            },
        )
    ]

    j = p.sh(
        f"journalctl -u {unit} --since '-{t.history_days} days' -o short-unix --no-pager | "
        "grep -E '=== cycle|match=[0-9]+ missing=|AUTH EXPIRED|Failed with result'",
        timeout=120,
        ok_codes=(0, 1),
    )
    checks += sync_from_journal(parse_journal(j.out), th, now, t.history_days)
    return checks


_VERIFY = re.compile(r"^(\w+)\s+match=(\d+) missing=(\d+) differ=(\d+) error=(\d+)")


def sync_from_journal(lines: list[tuple[float, str]], th: Thresholds, now: float, days: int):
    last_ok = max((ts for ts, m in lines if m.startswith("=== cycle ok")), default=None)
    last_start = max((ts for ts, m in lines if re.match(r"=== cycle \d", m)), default=None)
    last_fail = max((ts for ts, m in lines if "Failed with result" in m), default=None)
    last_auth = max((ts for ts, m in lines if "AUTH EXPIRED" in m), default=None)
    details = {
        "last_ok": last_ok,
        "last_start": last_start,
        "last_failure": last_fail,
        "last_auth_expired": last_auth,
    }
    if last_ok is None:
        status, summary = ERROR, f"no successful cycle in the last {days} days"
    else:
        age_h = (now - last_ok) / 3600
        status = _level(age_h, th.sync_age_warn_hours, th.sync_age_error_hours)
        summary = f"last ok {fmt_age(now - last_ok)} ago ({fmt_ts(last_ok)})"
    if last_fail and (last_ok is None or last_fail > last_ok):
        status = worst([status, WARN])
        summary += f"; latest cycle FAILED at {fmt_ts(last_fail)} (journalctl -u drc-sync)"
    if last_auth and (last_ok is None or last_auth > last_ok):
        status = ERROR
        summary += "; AUTH EXPIRED: re-authorize Drive (docs/sync.md)"
    checks = [Check("last sync", status, summary, details)]

    verify: dict[str, dict] = {}
    for ts, m in lines:
        v = _VERIFY.match(m)
        if v:  # later lines overwrite earlier ones: the latest verify per group wins
            match, missing, differ, error = map(int, v.groups()[1:])
            verify[v.group(1)] = {
                "ts": ts,
                "match": match,
                "missing": missing,
                "differ": differ,
                "error": error,
            }
    checks.append(verify_check(verify, now))
    return checks


def verify_check(verify: dict[str, dict], now: float | None) -> Check:
    if not verify:
        return Check("archive verify", WARN, "no verify result found", {})
    bad = sum(v["missing"] + v["differ"] + v["error"] for v in verify.values())
    parts = [
        f"{k} {v['match']} match/{v['missing']} missing/{v['differ']} differ/{v['error']} error"
        for k, v in verify.items()
    ]
    newest = max((v.get("ts") or 0) for v in verify.values())
    when = f" ({fmt_age(now - newest)} ago)" if now and newest else ""
    return Check("archive verify", ERROR if bad else OK, "; ".join(parts) + when, verify)


def check_auth(p: Probe, t: Target, th: Thresholds, now: float, ctx: dict) -> Check:
    r = p.sh(f"cat {t.auth_stamp}", ok_codes=(0, 1))
    try:
        stamp = float(r.out.strip())
    except ValueError:
        return Check("drive auth", WARN, f"no auth stamp at {t.auth_stamp}; age unknown", {})
    age_d = (now - stamp) / DAY
    expires = stamp + th.auth_expire_days * DAY
    details = {"authorized_at": stamp, "age_days": round(age_d, 2), "expires_at": expires}
    if age_d >= th.auth_expire_days:
        return Check(
            "drive auth",
            ERROR,
            f"EXPIRED {fmt_ts(expires)} (authorized {age_d:.1f} days ago): run "
            "deploy/reauth-drive.sh, copy the token to the VM",
            details,
        )
    status = WARN if age_d >= th.auth_warn_days else OK
    summary = (
        f"authorized {age_d:.1f} days ago; expires ~{fmt_ts(expires)} (in {fmt_age(expires - now)})"
    )
    if status == WARN:
        summary += ": re-authorize soon (docs/sync.md)"
    return Check("drive auth", status, summary, details)


def check_disk(p: Probe, t: Target, th: Thresholds, now: float, ctx: dict) -> Check:
    r = p.sh(f"df -P -B1 {t.repo}")
    lines = r.out.strip().splitlines()
    cols = lines[-1].split()
    used, avail = int(cols[2]), int(cols[3])
    pct = 100 * used / (used + avail) if used + avail else 0.0
    status = _level(pct, th.disk_warn_pct, th.disk_error_pct)
    summary = f"{pct:.0f}% used ({used / 1e9:.1f} GB used, {avail / 1e9:.1f} GB free)"
    details = {"used_bytes": used, "avail_bytes": avail, "used_pct": round(pct, 1)}
    if t.raw_dir and t.raw_cap_gb:
        du = p.sh(f"cd {t.repo} && du -s -B1 {t.raw_dir}", timeout=120, ok_codes=(0, 1))
        try:
            raw_bytes = int(du.out.split()[0])
        except (IndexError, ValueError):
            raw_bytes = None
        if raw_bytes is not None:
            cap_pct = 100 * raw_bytes / (t.raw_cap_gb * 1e9)
            details |= {
                "raw_bytes": raw_bytes,
                "raw_cap_gb": t.raw_cap_gb,
                "raw_cap_pct": round(cap_pct, 1),
            }
            summary += (
                f"; {t.raw_dir} {raw_bytes / 1e9:.1f} GB = {cap_pct:.0f}% of the "
                f"recorder's {t.raw_cap_gb:g} GB cap"
            )
            if cap_pct >= th.raw_cap_warn_pct:
                status = worst([status, WARN])
                summary += " (past it the recorder deletes oldest days)"
    return Check("disk", status, summary, details)


_AUTH_MARKERS = ("invalid_grant", "expired or revoked", "couldn't fetch token")


def check_drive(p: Probe, t: Target, th: Thresholds, now: float, ctx: dict) -> Check:
    """Opt-in live Drive check. Note rclone may refresh its access token and rewrite
    rclone.conf while doing this, which is why it is off by default on the VM."""
    r = p.sh(
        f'PATH="$HOME/.local/bin:$PATH" rclone about {t.drive_remote} --json',
        timeout=90,
        ok_codes=tuple(range(256)),
    )
    if r.rc != 0:
        err = (r.err or "").lower()
        if any(m in err for m in _AUTH_MARKERS):
            return Check("drive", ERROR, "AUTH EXPIRED: re-authorize (docs/sync.md)", {})
        tail = " ".join(r.err.strip().splitlines()[-1:])
        return Check("drive", ERROR, f"rclone about failed (exit {r.rc}): {tail}", {})
    info = json.loads(r.out)
    free = info.get("free")
    if free is None:  # some remotes report only total/used
        free = (info.get("total") or 0) - (info.get("used") or 0)
    status = WARN if free / 1e9 < th.drive_free_warn_gb else OK
    return Check("drive", status, f"reachable; {free / 1e9:.0f} GB free", info)


def check_local_verify(p: Probe, t: Target, th: Thresholds, now: float, ctx: dict) -> Check:
    r = p.sh(
        f"cd {t.repo} && DRC_HOST={t.verify_host} uv run drc-sync verify --what raw,norm",
        timeout=3600,
        ok_codes=tuple(range(256)),
    )
    verify = {}
    for line in r.out.splitlines():
        v = _VERIFY.match(line.strip())
        if v:
            match, missing, differ, error = map(int, v.groups()[1:])
            verify[v.group(1)] = {
                "ts": None,
                "match": match,
                "missing": missing,
                "differ": differ,
                "error": error,
            }
    if r.rc == 3:
        return Check("archive verify", ERROR, "AUTH EXPIRED: re-authorize (docs/sync.md)", {})
    if not verify:
        tail = " ".join(r.err.strip().splitlines()[-1:])
        return Check("archive verify", ERROR, f"drc-sync verify failed (exit {r.rc}): {tail}", {})
    return verify_check(verify, None)


# ---------------------------------------------------------------- orchestration
CheckFn = Callable[[Probe, Target, Thresholds, float, dict], "Check | list[Check]"]


def plan_checks(t: Target) -> list[tuple[str, CheckFn]]:
    """Which checks this target gets, in display order (a disabled one is simply absent)."""
    out: list[tuple[str, CheckFn]] = []
    if t.recorder_service:
        out.append(("recorder service", check_recorder_service))
    if t.raw_dir and t.venues:
        out.append(("raw freshness", check_raw_freshness))
        out.append(("feed gaps (24h)", check_gaps))
    if t.recorder_service:
        out.append(("1013 closes", check_1013))
    if t.sync_service:
        out.append(("sync", check_sync))
    if t.verify_host:
        out.append(("archive verify", check_local_verify))
    if t.auth_stamp:
        out.append(("drive auth", check_auth))
    out.append(("disk", check_disk))
    if t.probe_drive:
        out.append(("drive", check_drive))
    return out


def collect(run: Runner, t: Target, th: Thresholds) -> tuple[list[Check], float | None, Probe]:
    """Run every check for `t`. Never raises: an unreachable target is one error check, and a
    check that fails or cannot parse its probe becomes an error check of its own."""
    p = Probe(run)
    try:
        now = float(p.sh("date +%s", timeout=30).out.strip())
    except (ProbeError, ValueError) as e:
        return [Check("reachable", ERROR, f"{t.name} UNREACHABLE: {e}", {})], None, p
    checks: list[Check] = []
    ctx: dict = {}
    for name, fn in plan_checks(t):
        try:
            res = fn(p, t, th, now, ctx)
        except Exception as e:  # noqa: BLE001 - report it, keep checking the rest
            res = Check(name, ERROR, f"could not check: {type(e).__name__}: {e}", {})
        checks.extend(res if isinstance(res, list) else [res])
    return checks, now, p
