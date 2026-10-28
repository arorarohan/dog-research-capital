import calendar
import json
import subprocess
import time

import pytest

from drc.sync import plan
from drc.sync.cli import Archive, main
from drc.sync.rclone import Rclone, RcloneError

NOW = calendar.timegm(time.strptime("2026-10-27 12:00", "%Y-%m-%d %H:%M"))


class FakeRunner:
    """Stands in for the rclone binary: records commands, simulates `check` and `lsjson`."""

    def __init__(self, missing=(), differ=(), listing=(), rc=0):
        self.cmds = []
        self.missing, self.differ, self.listing, self.rc = set(missing), set(differ), listing, rc
        self.files_from = {}

    def __call__(self, cmd, capture_output, text):
        self.cmds.append(cmd)
        verb = cmd[1]
        out = ""
        if "--files-from" in cmd:
            lst = cmd[cmd.index("--files-from") + 1]
            self.files_from[len(self.cmds) - 1] = open(lst).read().split()
        if verb == "check":
            rels = self.files_from[len(self.cmds) - 1]
            rep = cmd[cmd.index("--combined") + 1]
            lines = []
            for r in rels:
                sym = "-" if r in self.missing else "*" if r in self.differ else "="
                lines.append(f"{sym} {r}")
            open(rep, "w").write("\n".join(lines) + "\n")
            return subprocess.CompletedProcess(
                cmd, 1 if (self.missing or self.differ) else 0, "", ""
            )
        if verb == "lsjson":
            out = json.dumps(self.listing)
        return subprocess.CompletedProcess(cmd, self.rc, out, "boom\n" if self.rc else "")


def touch(path, size=10):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


def archive(tmp_path, runner, host="vm-london"):
    return Archive(Rclone("rclone", runner), "gdrive:drc", host, tmp_path / "data", 12)


def day(n):
    """Date string n days before NOW."""
    return time.strftime("%Y-%m-%d", time.gmtime(NOW - n * 86400))


# ---- upload selection -------------------------------------------------------------------


def test_push_sends_only_closed_hours_and_namespaces_by_host(tmp_path):
    d = tmp_path / "data" / "raw_forward"
    touch(d / "polymarket/book/2026-10-27/21.jsonl.zst")
    touch(d / "polymarket/book/2026-10-27/22.jsonl")  # open hour, plain
    touch(d / "polymarket/book/2026-10-27/22.jsonl.zst.tmp")  # half-written
    touch(d / "polymarket/_catalog/2026-10-27/21.jsonl.zst")
    fake = FakeRunner()
    assert archive(tmp_path, fake).push(("raw",), dry_run=False) == 0
    copy = [i for i, c in enumerate(fake.cmds) if c[1] == "copy" and "/raw/" in c[3]]
    assert len(copy) == 1
    cmd = fake.cmds[copy[0]]
    assert cmd[3] == "gdrive:drc/vm-london/raw/raw_forward" and "--immutable" in cmd
    assert sorted(fake.files_from[copy[0]]) == [
        "polymarket/_catalog/2026-10-27/21.jsonl.zst",
        "polymarket/book/2026-10-27/21.jsonl.zst",
    ]


def test_sample_hour_copied_to_sample_tree_but_not_catalogs(tmp_path):
    d = tmp_path / "data" / "raw"
    touch(d / "kalshi/trades/2026-10-26/12.jsonl.zst")
    touch(d / "kalshi/trades/2026-10-26/13.jsonl.zst")
    touch(d / "kalshi/_catalog/2026-10-26/12.jsonl.zst")
    fake = FakeRunner()
    archive(tmp_path, fake).push(("raw",), dry_run=False)
    smp = [i for i, c in enumerate(fake.cmds) if c[3] == "gdrive:drc/vm-london/sample/raw"]
    assert len(smp) == 1 and fake.files_from[smp[0]] == ["kalshi/trades/2026-10-26/12.jsonl.zst"]


def test_norm_uploads_hour_files_only_without_immutable(tmp_path):
    n = tmp_path / "data" / "norm_forward"
    touch(n / "book_events/venue=polymarket/date=2026-10-27/hour=21.parquet")
    touch(n / "bars_1s/venue=polymarket.parquet")
    fake = FakeRunner()
    archive(tmp_path, fake).push(("norm",), dry_run=False)
    assert len(fake.cmds) == 1 and "--immutable" not in fake.cmds[0]
    assert fake.files_from[0] == ["book_events/venue=polymarket/date=2026-10-27/hour=21.parquet"]


def test_hist_skips_old_scratch_dirs(tmp_path):
    h = tmp_path / "data" / "hist"
    touch(h / "markets/part-0.parquet")
    touch(h / "_old_samples_lookback120/x.parquet")
    assert plan.local_tree_rels(h) == ["markets/part-0.parquet"]


def test_push_reports_failure_when_rclone_fails(tmp_path):
    touch(tmp_path / "data/raw/kraken/trade/2026-10-27/21.jsonl.zst")
    assert archive(tmp_path, FakeRunner(rc=6)).push(("raw",), dry_run=False) == 1


def test_dry_run_passes_the_flag(tmp_path):
    touch(tmp_path / "data/raw/kraken/trade/2026-10-27/21.jsonl.zst")
    fake = FakeRunner()
    archive(tmp_path, fake).push(("raw",), dry_run=True)
    assert all("--dry-run" in c for c in fake.cmds)


# ---- safety rails -----------------------------------------------------------------------


@pytest.mark.parametrize("verb", ["sync", "move", "purge", "bisync", "mount", "deletefile"])
def test_destructive_or_two_way_verbs_are_refused(verb):
    with pytest.raises(ValueError, match="not allowed"):
        Rclone("rclone", FakeRunner()).run(verb, "a", "b")


def test_cli_requires_a_host(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("DRC_HOST", raising=False)
    with pytest.raises(SystemExit):
        main(["--data", str(tmp_path), "status"])
    assert "DRC_HOST" in capsys.readouterr().err


# ---- verification-gated local pruning ---------------------------------------------------


def test_prune_local_deletes_only_old_verified_files(tmp_path):
    d = tmp_path / "data" / "raw"
    old_ok = d / f"kalshi/trades/{day(10)}/05.jsonl.zst"
    old_missing = d / f"kalshi/trades/{day(10)}/06.jsonl.zst"
    old_differs = d / f"kalshi/trades/{day(10)}/07.jsonl.zst"
    recent = d / f"kalshi/trades/{day(2)}/05.jsonl.zst"
    for f in (old_ok, old_missing, old_differs, recent):
        touch(f)
    fake = FakeRunner(
        missing={f"kalshi/trades/{day(10)}/06.jsonl.zst"},
        differ={f"kalshi/trades/{day(10)}/07.jsonl.zst"},
    )
    arc = archive(tmp_path, fake)
    n, kept = arc.prune_local(7, apply=False, now=NOW)
    assert (n, kept) == (1, 2) and old_ok.exists()  # dry run deletes nothing
    n, kept = arc.prune_local(7, apply=True, now=NOW)
    assert (n, kept) == (1, 2)
    assert not old_ok.exists() and old_missing.exists() and old_differs.exists()
    assert recent.exists()  # too young to prune, never even checked


# ---- expiry -----------------------------------------------------------------------------


def rf(date, hour=0, size=10, channel="book"):
    return plan.RawFile("raw", "polymarket", channel, date, hour, size)


def test_age_rule_uses_the_end_of_the_day():
    f16, f15, f14 = rf(day(16)), rf(day(15)), rf(day(14))
    assert plan.older_than(f16, 15, NOW)
    # NOW is mid-day, so the day that started 15 calendar days ago ended 14.5 days ago
    assert not plan.older_than(f15, 15, NOW) and not plan.older_than(f14, 15, NOW)
    assert plan.older_than(rf(day(17)), 15, NOW)


def test_catalogs_and_metadata_never_expire():
    files = [rf(day(100), channel="_catalog"), rf(day(100), channel="_meta"), rf(day(100))]
    ex = plan.plan_expiry(files, NOW, 15, cap_bytes=10**9)
    assert ex.by_age == [files[2]] and ex.by_cap == []


def test_cap_expires_oldest_days_first_and_stops_when_it_fits():
    files = [rf(day(n), h, size=100) for n in (1, 2, 3) for h in (0, 1)]  # 600 bytes
    ex = plan.plan_expiry(files, NOW, max_age_days=15, cap_bytes=350)
    assert ex.by_age == []
    assert [(f.date, f.hour) for f in ex.by_cap] == [(day(3), 0), (day(3), 1), (day(2), 0)]
    assert ex.kept_bytes == 300 <= 350


def test_exempt_files_count_toward_the_cap_but_are_not_deleted():
    files = [rf(day(1), channel="_catalog", size=300), rf(day(2), size=100), rf(day(3), size=100)]
    ex = plan.plan_expiry(files, NOW, 15, cap_bytes=350)
    assert [f.date for f in ex.by_cap] == [day(3), day(2)]  # still over 350 after one, so two
    assert files[0] not in ex.by_cap


def test_expire_remote_is_a_dry_run_unless_applied(tmp_path):
    listing = [
        {"Path": f"vm-london/raw/raw_forward/polymarket/book/{day(20)}/05.jsonl.zst", "Size": 5},
        {"Path": f"vm-london/raw/raw_forward/polymarket/book/{day(1)}/05.jsonl.zst", "Size": 5},
        {"Path": f"vm-london/sample/raw_forward/polymarket/book/{day(40)}/12.jsonl.zst", "Size": 5},
    ]
    fake = FakeRunner(listing=listing)
    arc = archive(tmp_path, fake)
    ex, n = arc.expire_remote(15, 300, apply=False, now=NOW)
    assert n == 1 and not any(c[1] == "delete" for c in fake.cmds)
    ex, n = arc.expire_remote(15, 300, apply=True, now=NOW)
    deletes = [i for i, c in enumerate(fake.cmds) if c[1] == "delete"]
    assert n == 1 and len(deletes) == 1 and "--drive-use-trash=false" in fake.cmds[deletes[0]]
    assert fake.files_from[deletes[0]] == [
        f"vm-london/raw/raw_forward/polymarket/book/{day(20)}/05.jsonl.zst"
    ]  # the sample tree is never listed, so it can never be deleted


def test_remote_listing_ignores_non_raw_and_bad_paths():
    text = json.dumps(
        [
            {"Path": "h/raw/raw/v/c/2026-10-01/05.jsonl.zst", "Size": 7},
            {"Path": "h/norm/norm/book_events/venue=v/date=2026-10-01/hour=05.parquet", "Size": 7},
            {"Path": "h/raw/raw/v/c/notadate/05.jsonl.zst", "Size": 7},
        ]
    )
    assert [p for p, _ in plan.parse_remote_listing(text)] == [
        "h/raw/raw/v/c/2026-10-01/05.jsonl.zst"
    ]


def test_rclone_error_surfaces_stderr():
    with pytest.raises(RcloneError, match="boom"):
        Rclone("rclone", FakeRunner(rc=3)).run("mkdir", "x")


def test_push_compresses_finished_plain_hours_first_and_uploads_them(tmp_path):
    import os

    from drc.recorder.writer import read_lines

    d = tmp_path / "data" / "raw"
    stale = d / "kalshi/trades/2026-10-25/11.jsonl"  # session ended mid-hour, never compressed
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b'{"t":1,"m":{"a":1}}\n{"t":2,"m":{"a":2}}\n')
    os.utime(stale, (NOW - 7200, NOW - 7200))
    fake = FakeRunner()
    arc = archive(tmp_path, fake)
    arc.push(("raw",), dry_run=False)
    zst = stale.with_name("11.jsonl.zst")
    assert zst.exists() and not stale.exists()
    assert [r["t"] for r in read_lines(zst)] == [1, 2]  # content survives intact
    uploaded = [v for v in fake.files_from.values() if "kalshi/trades/2026-10-25/11.jsonl.zst" in v]
    assert uploaded


def test_dry_run_does_not_compress_anything(tmp_path, capsys):
    import os

    d = tmp_path / "data" / "raw"
    stale = d / "kalshi/trades/2026-10-25/11.jsonl"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b'{"t":1,"m":{"a":1}}\n')
    os.utime(stale, (NOW - 7200, NOW - 7200))
    archive(tmp_path, FakeRunner()).push(("raw",), dry_run=True)
    assert stale.exists() and "would compress 1" in capsys.readouterr().out


def test_sample_is_one_hour_on_one_weekday():
    files = [
        plan.RawFile("raw", "v", "book", d, h, 1)
        for d in ("2026-10-26", "2026-10-27")  # Monday, Tuesday
        for h in (11, 12)
    ]
    assert [(f.date, f.hour) for f in plan.sample_files(files, 12, 0)] == [("2026-10-26", 12)]
    assert len(plan.sample_files(files, 12)) == 2  # no weekday filter: every day
