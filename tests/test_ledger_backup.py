"""drc-ledger-backup, offline: real git into a temp clone whose `origin` is a local bare repo,
a fake rclone runner. Nothing leaves the machine."""

import datetime as dt
import shutil
import sqlite3
import subprocess
from pathlib import Path

import orjson
import pytest

from drc.forward import backup as bk
from drc.forward.backup import BackupError, backup, restore
from drc.forward.eventlog import EventLog
from drc.report.sample import build_sample
from drc.sync.rclone import Rclone

NOW = dt.datetime(2026, 11, 6, 12, 5, tzinfo=dt.UTC)


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


class FakeRclone:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")


@pytest.fixture
def setup(tmp_path):
    origin = tmp_path / "drc-ledger.git"  # stands in for github.com/arorarohan/drc-ledger
    git("init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    repo = tmp_path / "data" / "ledger"
    git("clone", "-q", str(origin), str(repo), cwd=tmp_path)
    ledger = repo / "events.sqlite"
    build_sample(ledger).close()
    fake = FakeRclone()
    return origin, repo, ledger, fake


def run_backup(repo, ledger, fake, now=NOW, **kw):
    return backup(ledger, repo, rclone=Rclone(runner=fake), host="vm-london", now=now, **kw)


def log_lines(origin):
    return git("log", "--format=%s", "main", cwd=origin).stdout.splitlines()


def test_backup_commits_pushes_and_copies_to_drive(setup):
    origin, repo, ledger, fake = setup
    res = run_backup(repo, ledger, fake)
    assert res.committed and res.pushed and res.n_events > 50
    (msg,) = log_lines(origin)  # the push reached the (local) origin's main
    assert msg.startswith("ledger backup 2026-11-06 12:05Z vm-london:")
    tracked = set(git("ls-files", cwd=repo).stdout.split())
    assert tracked == {"events.jsonl", "manifest.json", ".gitignore"}  # never the binary DB
    man = orjson.loads((repo / "manifest.json").read_bytes())
    assert man["n_events"] == res.n_events and man["head_hash"] == res.head_hash

    dest = "gdrive:drc/ledger/vm-london/2026-11-06T120500Z"
    copy, check = fake.calls
    assert copy[1] == "copy" and copy[-2:] == [dest, "--immutable"]
    assert check[1] == "check" and check[3] == dest
    assert res.drive_dest == dest


def test_no_new_events_means_no_new_commit_and_new_events_mean_one(setup):
    origin, repo, ledger, fake = setup
    run_backup(repo, ledger, fake)
    again = run_backup(repo, ledger, fake, now=NOW + dt.timedelta(hours=1))
    assert not again.committed and len(log_lines(origin)) == 1
    lg = EventLog(ledger)
    lg.append("session_missed", {"reason": "test", "date": "2026-11-07"}, fund_id="x")
    lg.close()
    third = run_backup(repo, ledger, fake, now=NOW + dt.timedelta(days=1))
    assert third.committed and len(log_lines(origin)) == 2


def test_snapshot_is_safe_while_a_writer_holds_the_ledger_open(setup, tmp_path):
    origin, repo, ledger, fake = setup
    lg = EventLog(ledger)
    n_before = len(lg.events())
    with lg.transaction():  # a session mid-flight: uncommitted rows
        lg.append("session_missed", {"reason": "in flight"}, fund_id="x")
        snap = tmp_path / "snap.sqlite"
        bk.snapshot(ledger, snap)
    lg.close()
    assert bk.verify_file(snap).ok
    con = sqlite3.connect(snap)
    assert con.execute("SELECT COUNT(*) FROM events").fetchone()[0] == n_before
    con.close()


def test_restore_from_the_repo_and_from_the_drive_snapshot_verifies_the_chain(setup, tmp_path):
    origin, repo, ledger, fake = setup
    res = run_backup(repo, ledger, fake)
    # a fresh clone, as on a new machine after the old disk died
    clone = tmp_path / "fresh"
    git("clone", "-q", str(origin), str(clone), cwd=tmp_path)
    rr = restore(clone, tmp_path / "restored.sqlite")
    assert rr.verify.ok and rr.manifest_ok and not rr.problems
    a, b = EventLog(ledger), EventLog(rr.path)
    assert a.events() == b.events()  # every row, byte-for-byte hashes included
    a.close(), b.close()

    snap = tmp_path / "drive" / "events.sqlite"
    snap.parent.mkdir()
    shutil.copy(repo / "manifest.json", snap.parent / "manifest.json")
    bk.snapshot(ledger, snap)
    rr2 = restore(snap, tmp_path / "restored2.sqlite")
    assert rr2.verify.ok and rr2.manifest_ok and rr2.verify.n_events == res.n_events

    assert bk.main(["restore", "--from", str(clone), "--to", str(tmp_path / "r3.sqlite")]) == 0
    with pytest.raises(BackupError, match="exists"):
        restore(clone, rr.path)


def test_restore_detects_an_edited_backup(setup, tmp_path):
    origin, repo, ledger, fake = setup
    run_backup(repo, ledger, fake)
    p = repo / "events.jsonl"
    lines = p.read_bytes().splitlines()
    row = orjson.loads(lines[5])
    row["payload"] = row["payload"].replace("1", "2", 1)
    lines[5] = orjson.dumps(row)
    p.write_bytes(b"\n".join(lines) + b"\n")
    rr = restore(repo, tmp_path / "bad.sqlite")
    assert not rr.verify.ok and rr.verify.bad_seq == 6 and "BROKEN" in rr.problems[0]
    assert bk.main(["restore", "--from", str(repo), "--to", str(tmp_path / "b2.sqlite")]) == 1


def test_refuses_a_truncated_ledger_that_verify_alone_cannot_catch(setup, tmp_path):
    origin, repo, ledger, fake = setup
    run_backup(repo, ledger, fake)
    # rebuild the ledger without its last 3 rows: still a valid chain, so verify() says ok
    rows = (repo / "events.jsonl").read_bytes().splitlines()[:-3]
    short = tmp_path / "short.jsonl"
    short.write_bytes(b"\n".join(rows) + b"\n")
    ledger.unlink()
    restore(short, ledger)
    assert bk.verify_file(ledger).ok
    with pytest.raises(BackupError, match="lost or deleted"):
        run_backup(repo, ledger, fake, now=NOW + dt.timedelta(days=1))
    assert len(log_lines(origin)) == 1 and len(fake.calls) == 2  # nothing new went anywhere


def test_refuses_a_broken_chain(setup):
    origin, repo, ledger, fake = setup
    con = sqlite3.connect(ledger)
    con.execute("DROP TRIGGER events_no_update")
    con.execute("UPDATE events SET payload = '{}' WHERE seq = 3")
    con.commit()
    con.close()
    with pytest.raises(BackupError, match="BROKEN"):
        run_backup(repo, ledger, fake)
    assert fake.calls == [] and not (repo / "events.jsonl").exists()


def test_failed_push_is_an_error_and_is_retried_next_time(setup):
    origin, repo, ledger, fake = setup
    with pytest.raises(BackupError, match="push failed"):
        run_backup(repo, ledger, fake, push_cmd="false")
    assert fake.calls == []  # Drive copy did not run after a failed push
    res = run_backup(repo, ledger, fake)
    assert not res.committed and res.pushed and len(log_lines(origin)) == 1


def test_not_a_clone_points_at_the_setup_doc(tmp_path):
    ledger = tmp_path / "events.sqlite"
    build_sample(ledger).close()
    with pytest.raises(BackupError, match="one-time setup"):
        backup(ledger, tmp_path / "nope", rclone=None)
    res = backup(ledger, None, rclone=None)  # --no-git --no-drive: snapshot + verify only
    assert res.n_events > 0 and not res.pushed and res.drive_dest == ""


def test_cli_needs_a_host_for_drive(setup, capsys):
    origin, repo, ledger, fake = setup
    rc =bk.main(["backup", "--ledger", str(ledger), "--repo", str(repo), "--host", "",
                  "--no-git"])  # fmt: skip
    assert rc == 2 and "DRC_HOST" in capsys.readouterr().err


def test_cli_backup_without_drive(setup, capsys, monkeypatch):
    origin, repo, ledger, fake = setup
    rc = bk.main(["backup", "--ledger", str(ledger), "--repo", str(repo), "--no-drive"])
    assert rc == 0 and "committed, pushed" in capsys.readouterr().out
    assert Path(repo / "manifest.json").exists()
