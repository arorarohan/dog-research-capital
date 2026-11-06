import datetime as dt
from pathlib import Path

import pytest

from drc.research import registry
from drc.research.registry_cli import main

REPO = Path(__file__).resolve().parents[1]

GOOD = """
id = "h-good"
title = "A tested idea"
kind = "edge"
status = "killed"
proposed = 2026-10-01
updated = 2026-10-02
origin = "docs/x.md"
findings = ["docs/x.md"]
rule = "buy it"
rule_frozen = true
kill_criterion = "CI includes 0"

[search]
unrecorded_looks = 8
unrecorded_primary = 1
note = "bins"

[[tests]]
id = "primary"
date = 2026-10-02
role = "primary"
data = "holdout"
metric = "net, c"
direction = "positive"
estimate = 1.2
ci = [0.5, 1.9]
n = 100
clusters = 50
result = "pass"

[[tests]]
id = "explore"
date = 2026-10-01
role = "exploratory"
data = "discovery"
metric = "gap, pts"
direction = "negative"
estimate = -0.8
ci = [-3.4, 1.7]
result = "fail"

[[tests]]
id = "no-ci"
date = 2026-10-01
role = "primary"
data = "discovery"
metric = "net, c"
direction = "positive"
estimate = -4.7
result = "fail"
reported = { note_value = 1.0 }
"""


def write(tmp_path: Path, name: str, text: str) -> Path:
    d = tmp_path / "hypotheses"
    d.mkdir(exist_ok=True)
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / "docs" / "x.md").write_text("x")
    (d / f"{name}.toml").write_text(text)
    return d


def test_good_file_loads_and_counts(tmp_path):
    d = write(tmp_path, "h-good", GOOD)
    hyps = registry.load(d, root=tmp_path)
    (h,) = hyps
    assert h.proposed == dt.date(2026, 10, 1)
    assert h.looks == 3 + 8 and h.primary_tests == 2 + 1
    c = registry.counts(hyps)
    assert c.recorded_tests == 3 and c.tests_with_p == 2 and c.p_reported == 0
    assert c.total_looks == 11 and c.primary_tests == 3
    t = h.tests[2]
    assert t.p() is None and t.p_source == "absent"  # no CI: no p-value is invented


def test_corrected_table_views(tmp_path):
    d = write(tmp_path, "h-good", GOOD)
    rows, c = registry.corrected_table(registry.load(d, root=tmp_path))
    by = {r.test.id: r for r in rows}
    prim = by["primary"]
    assert prim.p < 0.001 and prim.test.p_source == "from CI"
    assert abs(prim.bonf_all_looks - min(1, prim.p * 11)) < 1e-12
    assert abs(prim.bonf_primary - prim.p * 3) < 1e-12
    assert prim.survives(prim.holm, 0.05)
    assert by["explore"].bonf_primary is None
    assert not by["explore"].survives(by["explore"].bh, 0.05)
    md = registry.table_markdown(rows, c)
    assert "h-good (killed)" in md and "Bonf. (m=11)" in md


def test_wrong_sign_never_survives(tmp_path):
    old = 'direction = "positive"\nestimate = 1.2'
    text = GOOD.replace(old, 'direction = "negative"\nestimate = 1.2')
    d = write(tmp_path, "h-good", text)
    rows, _ = registry.corrected_table(registry.load(d, root=tmp_path))
    prim = next(r for r in rows if r.test.id == "primary")
    assert prim.holm < 0.05 and not prim.survives(prim.holm, 0.05)
    assert prim.dsr < 0.01  # evidence against the predicted sign


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ('status = "killed"', 'status = "dead"', "status 'dead'"),
        ('id = "h-good"', 'id = "other"', "must equal the file name"),
        ("ci = [0.5, 1.9]", "ci = [1.5, 1.9]", "not inside"),
        ('role = "primary"\ndata = "holdout"', 'role = "main"\ndata = "holdout"', "role 'main'"),
        ("rule_frozen = true", 'rule_frozen = "yes"', "'rule_frozen' should be bool"),
        ('findings = ["docs/x.md"]', 'findings = ["docs/missing.md"]', "does not exist"),
        ('findings = ["docs/x.md"]', "findings = []", "needs a findings link"),
        ("unrecorded_primary = 1", "unrecorded_primary = 9", "exceeds"),
        ("n = 100", "n = 100\np_value = 1.5", "p_value must be"),
        ('kill_criterion = "CI includes 0"', 'kill_criterion = "x"\nsurprise = 1', "unknown key"),
    ],
)
def test_validation_errors(tmp_path, old, new, message):
    assert old in GOOD
    d = write(tmp_path, "h-good", GOOD.replace(old, new, 1))
    errs = registry.validate_dir(d, root=tmp_path)
    assert any(message in e for e in errs), errs
    with pytest.raises(registry.RegistryError):
        registry.load(d, root=tmp_path)


def test_testing_status_needs_tests(tmp_path):
    text = GOOD.split("[search]")[0].replace('status = "killed"', 'status = "testing"')
    d = write(tmp_path, "h-good", text)
    assert any("needs at least one" in e for e in registry.validate_dir(d, root=tmp_path))


def test_cli_commands(tmp_path, capsys):
    d = write(tmp_path, "h-good", GOOD)
    assert main(["--dir", str(d), "validate"]) == 0
    assert main(["--dir", str(d), "list", "--status", "killed"]) == 0
    assert "h-good" in capsys.readouterr().out
    assert main(["--dir", str(d), "count"]) == 0
    assert "11 looks in total" in capsys.readouterr().out
    assert main(["--dir", str(d), "table"]) == 0
    assert main(["--dir", str(d), "summary", "--check"]) == 1  # not written yet
    assert main(["--dir", str(d), "summary", "--write"]) == 0
    assert main(["--dir", str(d), "summary", "--check"]) == 0
    (d / "bad.toml").write_text('id = "bad"')
    assert main(["--dir", str(d), "validate"]) == 1
    assert main(["--dir", str(d), "count"]) == 1


def test_repo_registry_is_valid_and_summary_is_current():
    d = REPO / "hypotheses"
    assert registry.validate_dir(d, root=REPO) == []
    hyps = registry.load(d, root=REPO)
    assert len({h.id for h in hyps}) == len(hyps)
    assert (d / "SUMMARY.md").read_text() == registry.summary_markdown(hyps), (
        "hypotheses/SUMMARY.md is stale: run `uv run drc-registry summary --write`"
    )
