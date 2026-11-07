"""Restore drill and selective pull, offline: a fake rclone serves a local folder as the archive."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import polars as pl
import pytest

from drc.normalize.pipeline import normalize_all, out_path
from drc.recorder.writer import NS_PER_HOUR, RawWriter
from drc.sync import drill, plan
from drc.sync.cli import Archive, main
from drc.sync.rclone import Rclone

REMOTE = "gdrive:drc"
DAY0, DAY1 = "2025-10-08", "2025-10-09"
T1 = 1_760_000_000 * 1_000_000_000  # 2025-10-09 08:53 UTC
T1 -= T1 % NS_PER_HOUR  # 08:00
T0 = T1 - 20 * NS_PER_HOUR  # the day before: the connect that sets DAY1's epoch is in DAY0's _meta


def _glob_re(pattern: str) -> re.Pattern:
    """rclone filter glob (anchored with a leading /) -> regex over a path relative to the root."""
    out, i = "", 0
    body = pattern.lstrip("/")
    while i < len(body):
        if body.startswith("**", i):
            out, i = out + ".*", i + 2
        elif body[i] == "*":
            out, i = out + "[^/]*", i + 1
        else:
            out, i = out + re.escape(body[i]), i + 1
    return re.compile(out + "$")


class FakeRclone:
    """Stands in for the rclone binary, serving `root` as `gdrive:drc`. Supports what pull uses:
    `lsjson --dirs-only` (exit 3 if absent, like rclone) and `copy` with `--include` filters."""

    def __init__(self, root: Path, remote: str = REMOTE):
        self.root, self.remote, self.cmds = root, remote, []

    def local(self, remote: str) -> Path:
        assert remote.startswith(self.remote), remote
        return self.root / remote[len(self.remote) :].lstrip("/")

    def __call__(self, cmd, capture_output, text):
        self.cmds.append(cmd)
        verb = cmd[1]
        if verb == "lsjson":
            d = self.local(cmd[2])
            if not d.is_dir():
                return subprocess.CompletedProcess(cmd, 3, "", "directory not found")
            dirs = [{"Path": p.name, "IsDir": True} for p in sorted(d.iterdir()) if p.is_dir()]
            return subprocess.CompletedProcess(cmd, 0, json.dumps(dirs), "")
        if verb == "copy":
            src, dst = self.local(cmd[2]), Path(cmd[3])
            pats = [_glob_re(cmd[i + 1]) for i, a in enumerate(cmd) if a == "--include"]
            for f in sorted(src.rglob("*")):
                rel = f.relative_to(src).as_posix()
                if f.is_file() and (not pats or any(p.match(rel) for p in pats)):
                    (dst / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dst / rel)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(f"unexpected rclone {verb}")


def _book(price):
    return (
        '{"channel":"book","type":"snapshot","data":[{"symbol":"BTC/USD",'
        f'"bids":[{{"price":{price},"qty":1.0}}],"asks":[]}}]}}'
    )


def _trade(price):
    return (
        '{"channel":"trade","type":"update","data":[{"symbol":"BTC/USD","side":"buy",'
        f'"price":{price},"qty":0.5,"trade_id":1,"timestamp":"2025-10-09T08:00:02Z"}}]}}'
    )


@pytest.fixture
def archive_root(tmp_path):
    """A fake archive for host vm-london: raw_forward/kraken with a connect on DAY0 and data in
    two hours of DAY1, normalized into norm_forward. No `norm/norm`, no hist."""
    remote = tmp_path / "remote"
    raw = remote / "vm-london" / "raw" / "raw_forward"
    w = RawWriter(raw)
    w.event("kraken", "connect", ts_ns=T0)
    for h in (0, 1):
        t = T1 + h * NS_PER_HOUR
        w.write("kraken", "book", _book(10.0 + h), ts_ns=t + 10)
        w.write("kraken", "trade", _trade(10.0 + h), ts_ns=t + 20)
    w.close()
    RawWriter(raw).compress_closed()
    normalize_all(raw, remote / "vm-london" / "norm" / "norm_forward", ["kraken"])
    (remote / "vm-london" / "raw" / "raw").mkdir()  # an empty root
    return remote


def arc(tmp_path, root):
    return Archive(Rclone("rclone", FakeRclone(root)), REMOTE, "vm-london", tmp_path / "data", 12)


def test_pull_filters_select_one_hour_and_the_venues_meta():
    assert plan.pull_filters("raw") == []
    assert plan.pull_filters("raw", "kalshi", "2026-11-03", 7) == [
        "/kalshi/*/2026-11-03/07.jsonl.zst",
        "/kalshi/_meta/**",
    ]
    assert plan.pull_filters("norm", "kalshi", "2026-11-03", 7) == [
        "/*/venue=kalshi/date=2026-11-03/hour=07.*"
    ]
    assert plan.pull_filters("norm", "kalshi", "2026-11-03") == [
        "/*/venue=kalshi/date=2026-11-03/hour=*.*"
    ]
    with pytest.raises(ValueError):
        plan.pull_filters("hist", "kalshi")


def test_selective_pull_copies_only_the_hour_and_skips_absent_roots(tmp_path, archive_root):
    a = arc(tmp_path, archive_root)
    into = tmp_path / "pulled"
    done = a.pull(("raw", "norm", "hist"), into, venue="kraken", date=DAY1, hour=8)
    assert [(k, r) for k, r, _ in done] == [("raw", "raw"), ("raw", "raw_forward"),
                                            ("norm", "norm_forward")]  # fmt: skip
    got = sorted(p.relative_to(into).as_posix() for p in into.rglob("*") if p.is_file())
    assert got == [
        # the hour's accounting sidecar (#20) comes along, so a local normalize won't redo it
        f"norm/norm_forward/_accounting/venue=kraken/date={DAY1}/hour=08.json",
        f"norm/norm_forward/book_events/venue=kraken/date={DAY1}/hour=08.parquet",
        f"norm/norm_forward/trades/venue=kraken/date={DAY1}/hour=08.parquet",
        f"raw/raw_forward/kraken/_meta/{DAY0}/12.jsonl.zst",  # the earlier connect comes along
        f"raw/raw_forward/kraken/book/{DAY1}/08.jsonl.zst",
        f"raw/raw_forward/kraken/trade/{DAY1}/08.jsonl.zst",
    ]
    # never a copy from a root the archive does not have (rclone would fail the whole pull)
    assert not any(c[1] == "copy" and c[2].endswith("/norm/norm") for c in a.rc.calls)


def test_unfiltered_pull_still_copies_everything_present(tmp_path, archive_root):
    into = tmp_path / "pulled"
    arc(tmp_path, archive_root).pull(("raw", "norm"), into)
    assert (into / "raw/raw_forward/kraken/book" / DAY1 / "09.jsonl.zst").exists()


def test_restore_drill_passes_when_rebuilt_parquet_equals_the_archive(
    tmp_path, archive_root, capsys
):
    rc = drill.run(arc(tmp_path, archive_root), "raw_forward", "kraken", DAY1, 8)
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "trades       archived parquet/1 rows, rebuilt parquet/1 rows: IDENTICAL" in out
    assert "PASS" in out
    work = Path(out.splitlines()[0].rsplit(" in ", 1)[1])
    assert work.name.startswith("drc-restore-drill-") and not work.exists()  # temp dir cleaned


def test_restore_drill_fails_when_the_archived_parquet_differs(tmp_path, archive_root, capsys):
    p = out_path(archive_root / "vm-london/norm/norm_forward", "trades", "kraken", DAY1, 8)
    df = pl.read_parquet(p)
    df.with_columns(pl.col("price") * 2).write_parquet(p)
    into = tmp_path / "drill"
    rc = drill.run(arc(tmp_path, archive_root), "raw_forward", "kraken", DAY1, 8, into)
    out = capsys.readouterr().out
    assert rc == 1
    assert "trades       archived parquet/1 rows, rebuilt parquet/1 rows: DIFFERENT 1 of 1" in out
    assert "book_events" in out and "FAIL" in out and into.exists()  # kept for inspection


def test_restore_drill_reports_an_hour_the_archive_does_not_have(tmp_path, archive_root, capsys):
    rc = drill.run(arc(tmp_path, archive_root), "raw_forward", "kraken", DAY1, 15)
    assert rc == 1 and "nothing pulled" in capsys.readouterr().err


def test_restore_drill_refuses_the_data_dir_and_non_empty_dirs(tmp_path, archive_root, capsys):
    a = arc(tmp_path, archive_root)
    assert drill.run(a, "raw_forward", "kraken", DAY1, 8, tmp_path / "data" / "x") == 1
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "f").write_text("x")
    assert drill.run(a, "raw_forward", "kraken", DAY1, 8, busy) == 1
    err = capsys.readouterr().err
    assert "inside the data directory" in err and "not empty" in err
    assert a.rc.calls == []  # refused before touching the archive


def test_cli_restore_drill_and_selection_validation(tmp_path, archive_root, monkeypatch, capsys):
    fake = FakeRclone(archive_root)
    monkeypatch.setattr("drc.sync.cli.Rclone", lambda: Rclone("rclone", fake))
    monkeypatch.setenv("DRC_AUTH_STAMP", str(tmp_path / "none"))
    base = ["--config", str(tmp_path / "none.toml"), "--data", str(tmp_path / "data"),
            "--host", "vm-london"]  # fmt: skip
    sel = ["--venue", "kraken", "--date", DAY1, "--hour", "8"]
    assert main([*base, "restore-drill", *sel]) == 0
    assert "PASS" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main([*base, "restore-drill", "--venue", "kraken", "--date", DAY1, "--hour", "24"])
    with pytest.raises(SystemExit):
        main([*base, "pull", "--date", "09-10-2025"])


def test_drc_remote_env_points_at_a_shared_folder_remote(tmp_path, archive_root, monkeypatch):
    """Chris's setup: his own remote whose root_folder_id is the shared `drc` folder, so the
    archive is at `drc:` rather than `gdrive:drc`."""
    fake = FakeRclone(archive_root, remote="drc:")
    monkeypatch.setattr("drc.sync.cli.Rclone", lambda: Rclone("rclone", fake))
    monkeypatch.setenv("DRC_AUTH_STAMP", str(tmp_path / "none"))
    monkeypatch.setenv("DRC_REMOTE", "drc:")
    into = tmp_path / "pulled"
    assert main(["--config", "config/sync.toml", "--host", "vm-london", "pull", "--what", "norm",
                 "--into", str(into)]) == 0  # fmt: skip
    assert {c[2] for c in fake.cmds if c[1] == "copy"} == {"drc:/vm-london/norm/norm_forward"}
    assert len(list((into / "norm/norm_forward").rglob("*.parquet"))) == 4
