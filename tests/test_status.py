"""drc-status: every probe goes through a fake runner with canned outputs (shapes copied from the
real VM on 2026-11-06), so these tests are offline."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from drc.status import checks as C
from drc.status import cli, runner
from drc.status.runner import Result

NOW = 1793960400  # 2026-11-06 10:20:00 UTC
CONFIG = Path(__file__).resolve().parents[1] / "config" / "status.toml"


def meta(t: float, ev: str, **kw) -> str:
    return json.dumps({"t": int(t * 1e9), "ev": ev, **kw})


def healthy() -> dict[str, Result]:
    """Canned probe outputs of a healthy VM, keyed by a substring of the command."""
    pm = "data/raw_forward/polymarket/_meta/2026-11-06"
    return {
        "date +%s": Result(0, f"{NOW}\n"),
        "_meta": Result(
            0,
            "\n".join(
                [
                    f"## {pm}/03.jsonl.zst",
                    meta(
                        NOW - 6 * 3600,
                        "disconnect",
                        reason="ConnectionClosedError(Close(code=1013))",
                    ),
                    meta(NOW - 6 * 3600 + 1.5, "connect", url="wss://x"),
                    f"## {pm}/10.jsonl",
                    meta(NOW - 1200, "shutdown"),
                    meta(NOW - 1199, "connect", url="wss://x", shard=0),
                    meta(NOW - 1199, "connect", url="wss://x", shard=1),
                    "## data/raw_forward/kalshi/_meta/2026-11-06/02.jsonl.zst",
                    meta(NOW - 8 * 3600, "poll_errors", n=1, first="ReadError('')"),
                ]
            )
            + "\n",
        ),
        "systemctl show drc-record": Result(
            0,
            "NRestarts=0\nActiveState=active\n"
            "SubState=running\nActiveEnterTimestamp=@"
            f"{NOW - 1140}\n",
        ),
        "-printf": Result(
            0,
            "\n".join(
                [
                    f"{NOW - 2}.5 polymarket/price_change/2026-11-06/10.jsonl",
                    f"{NOW - 3000}.0 polymarket/book/2026-11-06/09.jsonl.zst",
                    f"{NOW - 11}.0 kalshi/orderbook/2026-11-06/10.jsonl",
                    f"{NOW - 1}.0 kalshi/_meta/2026-11-06/10.jsonl",
                ]
            )
            + "\n",
        ),
        "code=1013": Result(0, f"{NOW - 3 * 86400}.1\n{NOW - 3 * 86400 + 60}.2\n{NOW - 7200}.3\n"),
        "systemctl show drc-sync": Result(
            0,
            (
                "NextElapseUSecRealtime=@1793963400\nId=drc-sync.timer\nActiveState=active\n"
                "UnitFileState=enabled\nInactiveExitTimestamp=@1793873785\n\n"
                "Id=drc-sync.service\nActiveState=inactive\nUnitFileState=static\n"
                "InactiveExitTimestamp=@1793956233\n"
            ),
        ),
        "journalctl -u drc-sync": Result(
            0,
            "\n".join(
                [
                    f"{NOW - 4167}.03 drc-vm bash[1]: === cycle 2026-11-06T09:10:33Z host=v",
                    f"{NOW - 3410}.61 drc-vm bash[2]: raw    match=1611 missing=0 differ=0 error=0",
                    f"{NOW - 3410}.61 drc-vm bash[2]: norm   match=879 missing=0 differ=0 error=0",
                    f"{NOW - 3409}.90 drc-vm bash[1]: === cycle ok 2026-11-06T09:23:10Z",
                ]
            )
            + "\n",
        ),
        "cat ~/.config/rclone": Result(0, f"{NOW - int(1.8 * 86400)}\n"),
        "df -P": Result(
            0,
            "Filesystem 1-blocks Used Available Capacity Mounted on\n"
            "/dev/sda1 102888095744 48168701952 54702616576 47% /\n",
        ),
        "du -s": Result(0, "36500000000\tdata/raw_forward\n"),
    }


class FakeRunner:
    def __init__(self, canned: dict[str, Result]):
        self.canned = canned
        self.calls: list[str] = []

    def __call__(self, cmd: str, timeout: float) -> Result:
        self.calls.append(cmd)
        for key, res in self.canned.items():
            if key in cmd:
                return res
        return Result(127, "", f"no canned output for: {cmd}")


def vm_target(**kw) -> C.Target:
    base = dict(
        name="vm",
        repo="~/dog-research-capital",
        raw_dir="data/raw_forward",
        venues=["polymarket", "kalshi"],
        recorder_service="drc-record",
        sync_service="drc-sync",
        raw_cap_gb=50,
    )
    return C.Target(**(base | kw))


def run_vm(canned=None, th=None, **kw):
    fr = FakeRunner(canned if canned is not None else healthy())
    checks, now, probe = C.collect(fr, vm_target(**kw), th or C.Thresholds())
    return {c.name: c for c in checks}, fr


def test_healthy_vm_is_all_ok():
    res, fr = run_vm()
    assert all(c.status == C.OK for c in res.values()), {n: c.summary for n, c in res.items()}
    assert set(res) == {
        "recorder service",
        "raw freshness",
        "feed gaps (24h)",
        "1013 closes",
        "sync timer",
        "last sync",
        "archive verify",
        "drive auth",
        "disk",
    }
    assert "polymarket 2s" in res["raw freshness"].summary
    assert res["raw freshness"].details["venues"]["kalshi"]["age_secs"] == 11
    assert "_meta" not in res["raw freshness"].details["venues"]["kalshi"]["channels"]


def test_cli_json_and_exit_code(capsys):
    rc = cli.main(["--config", str(CONFIG), "--json", "--ssh", "x"], runner=FakeRunner(healthy()))
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["status"] == "ok" and out["checked_at"] == NOW
    assert {c["name"] for c in out["checks"]} >= {"recorder service", "drive auth"}


def test_unreachable_vm_is_one_clear_error(capsys):
    dead = FakeRunner({"": Result(255, "", "ssh: connect to host x port 22: Connection timed out")})
    rc = cli.main(["--config", str(CONFIG), "--ssh", "x"], runner=dead)
    out = capsys.readouterr().out
    assert rc == 2
    assert "UNREACHABLE" in out and "Connection timed out" in out
    assert dead.calls == ["date +%s"]  # gives up after the first probe


def test_recorder_down_is_error():
    canned = healthy()
    canned["systemctl show drc-record"] = Result(0, "ActiveState=failed\nSubState=failed\n")
    res, _ = run_vm(canned)
    assert res["recorder service"].status == C.ERROR
    assert "failed" in res["recorder service"].summary


def test_crash_restarts_warn():
    canned = healthy()
    canned["systemctl show drc-record"] = Result(
        0, "ActiveState=active\nSubState=running\nNRestarts=2\n"
    )
    res, _ = run_vm(canned)
    assert res["recorder service"].status == C.WARN


def test_stale_and_missing_venue():
    canned = healthy()
    canned["-printf"] = Result(0, f"{NOW - 15 * 60} polymarket/book/2026-11-06/10.jsonl\n")
    res, _ = run_vm(canned)
    c = res["raw freshness"]
    assert c.status == C.ERROR  # kalshi wrote nothing at all
    assert "polymarket 15 min" in c.summary and "kalshi NOTHING" in c.summary
    canned["-printf"] = Result(
        0, f"{NOW - 15 * 60} polymarket/book/x/10.jsonl\n{NOW - 31 * 60} kalshi/trades/x/10.jsonl\n"
    )
    res, _ = run_vm(canned)
    assert res["raw freshness"].status == C.ERROR
    canned["-printf"] = Result(
        0, f"{NOW - 15 * 60} polymarket/book/x/10.jsonl\n{NOW - 60} kalshi/trades/x/10.jsonl\n"
    )
    res, _ = run_vm(canned)
    assert res["raw freshness"].status == C.WARN


def test_gap_durations_and_counts():
    since = int((NOW - 86400) * 1e9)
    events = C.parse_meta(
        "\n".join(
            [
                "## data/raw_forward/polymarket/_meta/d/01.jsonl.zst",
                meta(NOW - 90000, "disconnect", reason="old"),  # before the window: not counted
                meta(NOW - 89990, "connect"),
                meta(NOW - 500, "disconnect", reason="Close(code=1013)", shard=0),
                meta(NOW - 499, "disconnect", reason="other", shard=1),
                meta(NOW - 497, "connect", shard=0),  # 3 s gap on shard 0
                meta(NOW - 489, "connect", shard=1),  # 10 s gap on shard 1
                meta(NOW - 100, "disconnect", reason="x", shard=1),  # never reconnected: still open
                "not json",
                "## data/raw_forward/kalshi/_meta/d/02.jsonl",
                meta(NOW - 50, "poll_errors", n=4, first="x"),
                meta(NOW - 40, "throttled", n=2),
                meta(NOW - 30, "catalog_error", reason="x"),
            ]
        ),
        "data/raw_forward",
    )
    s = C.summarize_gaps(events, since)
    pm, k = s["polymarket"], s["kalshi"]
    assert pm["disconnects"] == 3 and pm["code1013"] == 1
    assert pm["gap_secs_max"] == 10 and pm["gap_secs_total"] == 13 and pm["open"] == 1
    assert (k["poll_errors"], k["throttled"], k["catalog_errors"]) == (4, 2, 1)


def test_restart_across_untagged_version_closes_gap():
    events = C.parse_meta(
        "\n".join(
            [
                "## data/raw_forward/polymarket/_meta/d/10.jsonl",
                meta(NOW - 20, "shutdown"),  # old process: no shard field
                meta(NOW - 18, "connect", shard=0),
            ]
        ),
        "data/raw_forward",
    )
    pm = C.summarize_gaps(events, int((NOW - 86400) * 1e9))["polymarket"]
    assert pm["restarts"] == 1 and pm["disconnects"] == 0 and pm["gap_secs_max"] == 2


def test_many_gaps_and_poll_errors_escalate():
    canned = healthy()
    lines = ["## data/raw_forward/polymarket/_meta/d/05.jsonl.zst"]
    for i in range(12):
        lines += [
            meta(NOW - 5000 + 10 * i, "disconnect", reason="x"),
            meta(NOW - 4999 + 10 * i, "connect"),
        ]
    canned["_meta"] = Result(0, "\n".join(lines))
    res, _ = run_vm(canned)
    assert res["feed gaps (24h)"].status == C.WARN and "12 gaps" in res["feed gaps (24h)"].summary
    canned["_meta"] = Result(
        0, "## data/raw_forward/kalshi/_meta/d/05.jsonl\n" + meta(NOW - 60, "poll_errors", n=250)
    )
    res, _ = run_vm(canned)
    assert res["feed gaps (24h)"].status == C.ERROR


def test_long_single_gap_warns():
    canned = healthy()
    canned["_meta"] = Result(
        0,
        "\n".join(
            [
                "## data/raw_forward/polymarket/_meta/d/05.jsonl",
                meta(NOW - 900, "disconnect", reason="x"),
                meta(NOW - 600, "connect"),
            ]
        ),
    )
    res, _ = run_vm(canned)
    assert res["feed gaps (24h)"].status == C.WARN


def test_1013_counts_per_day_and_since_start():
    res, _ = run_vm()
    c = res["1013 closes"]
    assert c.status == C.OK  # one in the last 24h
    assert c.details["last_24h"] == 1
    assert c.details["since_recorder_start"] == 0  # the one 2 h ago predates the restart
    assert c.details["per_day_utc"]["2026-11-03"] == 2
    assert len(c.details["per_day_utc"]) == 7
    canned = healthy()
    canned["code=1013"] = Result(0, "".join(f"{NOW - 60 * i}.0\n" for i in range(25)))
    res, _ = run_vm(canned)
    assert res["1013 closes"].status == C.ERROR


def test_sync_running_cycle_is_shown():
    canned = healthy()
    canned["systemctl show drc-sync"] = Result(
        0,
        (
            "NextElapseUSecRealtime=\nId=drc-sync.timer\nActiveState=active\nUnitFileState=enabled\n\n"
            f"Id=drc-sync.service\nActiveState=activating\nInactiveExitTimestamp=@{NOW - 600}\n"
        ),
    )
    res, _ = run_vm(canned)
    assert res["sync timer"].status == C.OK
    assert "a cycle is running (started 10 min ago)" in res["sync timer"].summary


def test_sync_timer_disabled_is_error():
    canned = healthy()
    canned["systemctl show drc-sync"] = Result(
        0, "Id=drc-sync.timer\nActiveState=inactive\nUnitFileState=disabled\n"
    )
    res, _ = run_vm(canned)
    assert res["sync timer"].status == C.ERROR


def j(ts: float, msg: str) -> tuple[float, str]:
    return (ts, msg)


def test_sync_journal_states():
    th = C.Thresholds()
    ok = [
        j(NOW - 3 * 3600, "=== cycle 2026-11-06T07:10:00Z host=v"),
        j(NOW - 3 * 3600 + 5, "=== cycle ok 2026-11-06T07:10:05Z"),
    ]
    last, verify = C.sync_from_journal(ok, th, NOW, 7)
    assert last.status == C.WARN and "3.0 h" in last.summary  # 3 h > 2.5 h warn
    assert verify.status == C.WARN  # no verify lines at all
    failed = ok + [
        j(NOW - 1800, "=== cycle 2026-11-06T09:50:00Z host=v"),
        j(NOW - 1700, "drc-sync.service: Failed with result 'exit-code'."),
    ]
    last, _ = C.sync_from_journal(failed, th, NOW, 7)
    assert last.status == C.WARN and "FAILED" in last.summary
    auth = failed + [j(NOW - 1750, "AUTH EXPIRED: Google Drive login expired")]
    last, _ = C.sync_from_journal(auth, th, NOW, 7)
    assert last.status == C.ERROR and "AUTH EXPIRED" in last.summary
    last, _ = C.sync_from_journal([], th, NOW, 7)
    assert last.status == C.ERROR and "no successful cycle" in last.summary
    stale = [j(NOW - 7 * 3600, "=== cycle ok x")]
    assert C.sync_from_journal(stale, th, NOW, 7)[0].status == C.ERROR


def test_verify_mismatch_is_error():
    lines = [
        j(NOW - 100, "raw    match=10 missing=0 differ=0 error=0"),
        j(NOW - 99, "norm   match=5 missing=2 differ=0 error=0"),
        j(NOW - 10, "=== cycle ok x"),
    ]
    _, verify = C.sync_from_journal(lines, C.Thresholds(), NOW, 7)
    assert verify.status == C.ERROR and "2 missing" in verify.summary


def test_latest_verify_wins():
    lines = [
        j(NOW - 5000, "raw    match=10 missing=3 differ=0 error=0"),
        j(NOW - 100, "raw    match=13 missing=0 differ=0 error=0"),
    ]
    _, verify = C.sync_from_journal(lines, C.Thresholds(), NOW, 7)
    assert verify.status == C.OK and verify.details["raw"]["match"] == 13


@pytest.mark.parametrize(
    ("days", "status"), [(1.8, C.OK), (5.0, C.WARN), (6.9, C.WARN), (7.0, C.ERROR), (9, C.ERROR)]
)
def test_auth_age(days, status):
    canned = healthy()
    canned["cat ~/.config/rclone"] = Result(0, f"{NOW - days * 86400}\n")
    res, _ = run_vm(canned)
    assert res["drive auth"].status == status
    if status == C.ERROR:
        assert "EXPIRED" in res["drive auth"].summary


def test_auth_stamp_missing_warns():
    canned = healthy()
    canned["cat ~/.config/rclone"] = Result(1, "", "cat: No such file or directory")
    res, _ = run_vm(canned)
    assert res["drive auth"].status == C.WARN and "unknown" in res["drive auth"].summary


def test_disk_and_raw_cap():
    canned = healthy()
    res, _ = run_vm(canned)
    assert res["disk"].status == C.OK and "73% of the recorder's 50 GB cap" in res["disk"].summary
    canned["du -s"] = Result(0, "44000000000\tdata/raw_forward\n")
    res, _ = run_vm(canned)
    assert res["disk"].status == C.WARN and "deletes oldest days" in res["disk"].summary
    canned["df -P"] = Result(0, "h\n/dev/sda1 100 92 8 92% /\n")
    res, _ = run_vm(canned)
    assert res["disk"].status == C.ERROR


def test_broken_probe_is_contained():
    canned = healthy()
    canned["df -P"] = Result(0, "garbage")
    canned["systemctl show drc-sync"] = Result(1, "", "Failed to connect to bus")
    res, _ = run_vm(canned)
    assert res["disk"].status == C.ERROR and "could not check" in res["disk"].summary
    assert res["sync"].status == C.ERROR and "Failed to connect to bus" in res["sync"].summary
    assert res["recorder service"].status == C.OK  # the rest still ran


READ_ONLY_HEADS = {"date", "systemctl show", "journalctl", "cd", "find", "cat", "df", "du", "PATH="}
FORBIDDEN = re.compile(
    r"\b(restart|start|stop|enable|disable|kill|rm|mv|cp|delete|purge|sync|"
    r"push|prune|expire|tee|truncate|chmod)\b"
)


def test_every_probe_is_read_only():
    fr = FakeRunner(healthy())
    C.collect(fr, vm_target(probe_drive=True), C.Thresholds())
    assert len(fr.calls) >= 10
    for cmd in fr.calls:
        assert any(cmd.startswith(h) for h in READ_ONLY_HEADS), cmd
        assert ">" not in cmd.replace("2>/dev/null", ""), cmd  # no file writes
        assert not FORBIDDEN.search(cmd.replace("drc-sync", "").replace("drc-record", "")), cmd


def test_local_mode(capsys, tmp_path):
    canned = {
        "date +%s": Result(0, f"{NOW}\n"),
        "cat ~/.config/rclone": Result(0, f"{NOW - 86400}\n"),
        "df -P": Result(0, "h\n/dev/sdc 1000000000000 300000000000 700000000000 30% /\n"),
        "rclone about": Result(0, json.dumps({"total": 429e9, "used": 100e9, "free": 329e9})),
    }
    fr = FakeRunner(canned)
    rc = cli.main(["--config", str(CONFIG), "--local"], runner=fr)
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "drive auth" in out and "329 GB free" in out
    assert not any("systemctl" in c or "journalctl" in c for c in fr.calls)


def test_drive_probe_auth_failure():
    canned = healthy()
    canned["rclone about"] = Result(
        1, "", "Failed to create file system: couldn't fetch token: invalid_grant"
    )
    res, _ = run_vm(canned, probe_drive=True)
    assert res["drive"].status == C.ERROR and "AUTH EXPIRED" in res["drive"].summary


def test_local_verify_parses_drc_sync_output():
    canned = {
        "drc-sync verify": Result(
            1,
            "raw    match=70 missing=1 differ=0 error=0\n"
            "norm   match=20 missing=0 differ=0 error=0\n",
        )
    }
    p = C.Probe(FakeRunner(canned))
    c = C.check_local_verify(
        p, C.Target(name="local", verify_host="rohan-laptop"), C.Thresholds(), NOW, {}
    )
    assert c.status == C.ERROR and "1 missing" in c.summary
    canned["drc-sync verify"] = Result(3, "", "AUTH EXPIRED: x")
    c = C.check_local_verify(
        C.Probe(FakeRunner(canned)),
        C.Target(name="local", verify_host="h"),
        C.Thresholds(),
        NOW,
        {},
    )
    assert "AUTH EXPIRED" in c.summary


def test_config_errors_exit_64(tmp_path, capsys):
    bad = tmp_path / "s.toml"
    bad.write_text("[thresholds]\nraw_age_warn_mins = 3\n")
    assert cli.main(["--config", str(bad), "--ssh", "x"], runner=FakeRunner({})) == cli.USAGE
    assert "raw_age_warn_mins" in capsys.readouterr().err
    assert cli.main(["--config", str(tmp_path / "missing.toml")]) == cli.USAGE


def test_shipped_config_loads():
    cfg = __import__("tomllib").loads(CONFIG.read_text())
    C.Thresholds.from_dict(cfg["thresholds"])
    vm, extra = cli.load_target(cfg, "vm")
    assert vm.venues == ["polymarket", "kalshi"] and extra["ssh"] == "drc-vm"
    local, _ = cli.load_target(cfg, "local")
    assert local.recorder_service is None and local.raw_dir is None


def test_render_puts_problems_first():
    checks = [
        C.Check("a", C.OK, "fine"),
        C.Check("b", C.ERROR, "broken"),
        C.Check("c", C.WARN, "meh"),
    ]
    text = cli.render("vm", "ssh x", checks, NOW)
    lines = text.splitlines()
    assert lines[0].endswith(": ERROR")
    assert "broken" in lines[1] and "meh" in lines[2] and "fine" in lines[3]


def test_ssh_runner_argv_and_timeout(monkeypatch):
    seen = {}

    def fake_run(argv, capture_output, text, timeout):
        seen["argv"], seen["timeout"] = argv, timeout
        raise subprocess.TimeoutExpired(argv, timeout)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    r = runner.ssh_runner("drc-vm", key="~/.ssh/k")("date +%s", 5)
    assert r.rc == runner.TIMEOUT_RC and "timed out" in r.err
    argv = seen["argv"]
    assert argv[:5] == ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    assert argv[-3:] == ["drc-vm", "--", "date +%s"] and not argv[6].startswith("~")


def test_missing_ssh_binary_is_a_result(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("ssh")

    monkeypatch.setattr(runner.subprocess, "run", boom)
    assert runner.ssh_runner("x")("true", 1).rc == 127
