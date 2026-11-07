"""Hypothesis registry: one TOML file per hypothesis under `hypotheses/`.

The registry is the lab notebook: what we believed, the rule we froze before looking, what
would kill it, every test we ran on it, and how many other cells we looked at without
recording them. Counting looks honestly is the point: it is what makes the multiple-testing
correction (`drc.research.multitest`) possible.

Schema (see `hypotheses/README.md` for the long form):

    id = "pm-sched-gap"                 # must equal the file stem
    title = "..."
    kind = "edge"                       # edge | measurement
    status = "testing"                  # proposed | testing | killed | confirmed | inconclusive
    proposed = 2026-10-23               # TOML dates
    updated = 2026-10-25
    origin = "docs/findings/....md"     # where the idea was written down
    findings = ["docs/findings/....md"] # results (may be empty for proposals)
    rule = "..."                        # the trade / claim
    rule_frozen = true                  # written down before the deciding data were seen
    kill_criterion = "..."
    aliases = ["H1"]                    # optional
    notes = "..."                       # optional

    [search]                            # optional: looks NOT recorded as [[tests]]
    unrecorded_looks = 157
    unrecorded_primary = 0              # of which pre-declared primary tests
    note = "6 horizons x 9 bins x 3 tiers ..."

    [[tests]]
    id = "holdout-3d-80-95"
    date = 2026-10-23
    role = "primary"                    # primary | secondary | exploratory | diagnostic
    data = "holdout, scheduled end 2026-02-09 .. 09-01"
    metric = "gap, pts"
    direction = "negative"              # sign the hypothesis predicts: positive|negative|either
    estimate = -0.8                     # optional
    ci = [-3.4, 1.7]                    # optional, needs estimate
    ci_level = 0.95                     # optional, default 0.95
    p_value = 0.01                      # optional: ONLY if the finding reports one
    n = 649                             # optional
    clusters = 577                      # optional
    result = "fail"                     # pass | fail | descriptive
    reported = { brier = 0.1 }          # optional free-form numbers the finding gives
    note = "..."
"""

from __future__ import annotations

import datetime as dt
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from drc.research import multitest

STATUSES = ("proposed", "testing", "killed", "confirmed", "inconclusive")
KINDS = ("edge", "measurement")
ROLES = ("primary", "secondary", "exploratory", "diagnostic")
DIRECTIONS = ("positive", "negative", "either")
RESULTS = ("pass", "fail", "descriptive")

HYP_REQUIRED = {
    "id": str,
    "title": str,
    "kind": str,
    "status": str,
    "proposed": dt.date,
    "updated": dt.date,
    "origin": str,
    "findings": list,
    "rule": str,
    "rule_frozen": bool,
    "kill_criterion": str,
}
HYP_OPTIONAL = {"aliases": list, "notes": str, "search": dict, "tests": list}
TEST_REQUIRED = {
    "id": str,
    "date": dt.date,
    "role": str,
    "data": str,
    "metric": str,
    "direction": str,
    "result": str,
}
TEST_OPTIONAL = {
    "estimate": (int, float),
    "ci": list,
    "ci_level": float,
    "p_value": (int, float),
    "n": int,
    "clusters": int,
    "reported": dict,
    "note": str,
}
SEARCH_KEYS = {"unrecorded_looks": int, "unrecorded_primary": int, "note": str}


@dataclass
class Test:
    hypothesis: str
    id: str
    date: dt.date
    role: str
    data: str
    metric: str
    direction: str
    result: str
    estimate: float | None = None
    ci: tuple[float, float] | None = None
    ci_level: float = 0.95
    p_value: float | None = None
    n: int | None = None
    clusters: int | None = None
    reported: dict = field(default_factory=dict)
    note: str = ""

    @property
    def p_source(self) -> str:
        if self.p_value is not None:
            return "reported"
        if self.estimate is not None and self.ci is not None:
            return "from CI"
        return "absent"

    def z(self) -> float | None:
        """z-statistic (signed like the estimate), derived from the CI when no p is reported."""
        if self.estimate is not None and self.ci is not None:
            return multitest.z_from_ci(self.estimate, self.ci[0], self.ci[1], self.ci_level)
        return None

    def p(self) -> float | None:
        """Two-sided p: the reported one if any, else derived from estimate and CI, else None."""
        if self.p_value is not None:
            return float(self.p_value)
        z = self.z()
        return None if z is None else multitest.p_from_z(z)

    def sign_ok(self) -> bool | None:
        if self.estimate is None:
            return None
        if self.direction == "either":
            return True
        return self.estimate > 0 if self.direction == "positive" else self.estimate < 0


@dataclass
class Hypothesis:
    id: str
    title: str
    kind: str
    status: str
    proposed: dt.date
    updated: dt.date
    origin: str
    findings: list[str]
    rule: str
    rule_frozen: bool
    kill_criterion: str
    aliases: list[str] = field(default_factory=list)
    notes: str = ""
    unrecorded_looks: int = 0
    unrecorded_primary: int = 0
    search_note: str = ""
    tests: list[Test] = field(default_factory=list)
    path: Path | None = None

    @property
    def looks(self) -> int:
        """Every test we ran or cell we looked at, recorded or not."""
        return len(self.tests) + self.unrecorded_looks

    @property
    def primary_tests(self) -> int:
        return sum(t.role == "primary" for t in self.tests) + self.unrecorded_primary


def _check_types(d: dict, required: dict, optional: dict, where: str) -> list[str]:
    errs = []
    for k, typ in required.items():
        if k not in d:
            errs.append(f"{where}: missing required key '{k}'")
        elif not isinstance(d[k], typ) or (typ is int and isinstance(d[k], bool)):
            errs.append(f"{where}: '{k}' should be {_tname(typ)}, got {type(d[k]).__name__}")
    for k, v in d.items():
        if k in required:
            continue
        if k not in optional:
            errs.append(f"{where}: unknown key '{k}'")
        elif not isinstance(v, optional[k]) or (isinstance(v, bool) and optional[k] is not bool):
            errs.append(f"{where}: '{k}' should be {_tname(optional[k])}, got {type(v).__name__}")
    return errs


def _tname(typ) -> str:
    if isinstance(typ, tuple):
        return " or ".join(t.__name__ for t in typ)
    return typ.__name__


def _validate_test(t: dict, where: str) -> list[str]:
    errs = _check_types(t, TEST_REQUIRED, TEST_OPTIONAL, where)
    if errs:
        return errs
    for key, allowed in (("role", ROLES), ("direction", DIRECTIONS), ("result", RESULTS)):
        if t[key] not in allowed:
            errs.append(f"{where}: {key} '{t[key]}' not in {allowed}")
    if "ci" in t:
        ci = t["ci"]
        if "estimate" not in t:
            errs.append(f"{where}: ci given without estimate")
        elif len(ci) != 2 or not all(isinstance(x, int | float) for x in ci):
            errs.append(f"{where}: ci must be [lo, hi]")
        else:
            try:
                multitest.z_from_ci(t["estimate"], ci[0], ci[1], t.get("ci_level", 0.95))
            except ValueError as e:
                errs.append(f"{where}: {e}")
    if "ci_level" in t and not 0 < t["ci_level"] < 1:
        errs.append(f"{where}: ci_level must be in (0, 1)")
    if "p_value" in t and not 0 <= t["p_value"] <= 1:
        errs.append(f"{where}: p_value must be in [0, 1]")
    for k in ("n", "clusters"):
        if k in t and t[k] < 0:
            errs.append(f"{where}: {k} must be >= 0")
    return errs


def validate_dict(d: dict, stem: str, root: Path | None = None) -> list[str]:
    """Schema errors for one hypothesis file's parsed TOML (empty list = valid)."""
    where = f"{stem}.toml"
    errs = _check_types(d, HYP_REQUIRED, HYP_OPTIONAL, where)
    if errs:
        return errs
    if d["id"] != stem:
        errs.append(f"{where}: id '{d['id']}' must equal the file name")
    if d["kind"] not in KINDS:
        errs.append(f"{where}: kind '{d['kind']}' not in {KINDS}")
    if d["status"] not in STATUSES:
        errs.append(f"{where}: status '{d['status']}' not in {STATUSES}")
    if d["updated"] < d["proposed"]:
        errs.append(f"{where}: updated is before proposed")
    for k in ("title", "rule", "kill_criterion", "origin"):
        if not d[k].strip():
            errs.append(f"{where}: '{k}' is empty")
    tests = d.get("tests", [])
    if d["status"] != "proposed" and not tests:
        errs.append(f"{where}: status '{d['status']}' needs at least one [[tests]] entry")
    if d["status"] in ("killed", "confirmed", "inconclusive") and not d["findings"]:
        errs.append(f"{where}: status '{d['status']}' needs a findings link")
    if root is not None:
        for link in [d["origin"], *d["findings"]]:
            if not (root / link.split("#")[0]).exists():
                errs.append(f"{where}: linked file '{link}' does not exist")
    search = d.get("search", {})
    for k, v in search.items():
        if k not in SEARCH_KEYS:
            errs.append(f"{where}: unknown [search] key '{k}'")
        elif not isinstance(v, SEARCH_KEYS[k]) or isinstance(v, bool):
            errs.append(f"{where}: [search] '{k}' has the wrong type")
        elif isinstance(v, int) and v < 0:
            errs.append(f"{where}: [search] '{k}' must be >= 0")
    if search.get("unrecorded_primary", 0) > search.get("unrecorded_looks", 0):
        errs.append(f"{where}: unrecorded_primary exceeds unrecorded_looks")
    seen = set()
    for i, t in enumerate(tests):
        if not isinstance(t, dict):
            errs.append(f"{where}: tests[{i}] is not a table")
            continue
        tid = t.get("id", f"#{i}")
        if tid in seen:
            errs.append(f"{where}: duplicate test id '{tid}'")
        seen.add(tid)
        errs += _validate_test(t, f"{where} test '{tid}'")
    return errs


def _build(d: dict, path: Path) -> Hypothesis:
    search = d.get("search", {})
    h = Hypothesis(
        id=d["id"],
        title=d["title"],
        kind=d["kind"],
        status=d["status"],
        proposed=d["proposed"],
        updated=d["updated"],
        origin=d["origin"],
        findings=list(d["findings"]),
        rule=d["rule"],
        rule_frozen=d["rule_frozen"],
        kill_criterion=d["kill_criterion"],
        aliases=list(d.get("aliases", [])),
        notes=d.get("notes", ""),
        unrecorded_looks=search.get("unrecorded_looks", 0),
        unrecorded_primary=search.get("unrecorded_primary", 0),
        search_note=search.get("note", ""),
        path=path,
    )
    for t in d.get("tests", []):
        ci = t.get("ci")
        h.tests.append(
            Test(
                hypothesis=h.id,
                id=t["id"],
                date=t["date"],
                role=t["role"],
                data=t["data"],
                metric=t["metric"],
                direction=t["direction"],
                result=t["result"],
                estimate=None if "estimate" not in t else float(t["estimate"]),
                ci=None if ci is None else (float(ci[0]), float(ci[1])),
                ci_level=float(t.get("ci_level", 0.95)),
                p_value=t.get("p_value"),
                n=t.get("n"),
                clusters=t.get("clusters"),
                reported=dict(t.get("reported", {})),
                note=t.get("note", ""),
            )
        )
    return h


class RegistryError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("\n".join(errors))
        self.errors = errors


def validate_dir(directory: Path, root: Path | None = None) -> list[str]:
    """All schema errors in a registry directory (empty list = valid)."""
    errs = []
    files = sorted(directory.glob("*.toml"))
    if not files:
        return [f"no hypothesis files in {directory}"]
    for f in files:
        try:
            d = tomllib.loads(f.read_text())
        except tomllib.TOMLDecodeError as e:
            errs.append(f"{f.name}: invalid TOML: {e}")
            continue
        errs += validate_dict(d, f.stem, root)
    return errs


def load(directory: Path, root: Path | None = None) -> list[Hypothesis]:
    """Load and validate every hypothesis; raises RegistryError listing all problems."""
    errs = validate_dir(directory, root)
    if errs:
        raise RegistryError(errs)
    return [_build(tomllib.loads(f.read_text()), f) for f in sorted(directory.glob("*.toml"))]


# --- counting and correction -----------------------------------------------------------------


@dataclass
class Counts:
    hypotheses: int
    by_status: dict[str, int]
    recorded_tests: int
    tests_with_p: int
    p_reported: int
    unrecorded_looks: int
    total_looks: int
    primary_tests: int
    primary_with_p: int


def counts(hyps: list[Hypothesis]) -> Counts:
    """Test counts. Only `edge` hypotheses enter the multiple-testing family: a measurement
    (like a price basis) is not a claim of alpha, so it neither needs nor dilutes correction."""
    edges = [h for h in hyps if h.kind == "edge"]
    tests = [t for h in edges for t in h.tests]
    return Counts(
        hypotheses=len(hyps),
        by_status={s: sum(h.status == s for h in hyps) for s in STATUSES},
        recorded_tests=len(tests),
        tests_with_p=sum(t.p() is not None for t in tests),
        p_reported=sum(t.p_value is not None for t in tests),
        unrecorded_looks=sum(h.unrecorded_looks for h in edges),
        total_looks=sum(h.looks for h in edges),
        primary_tests=sum(h.primary_tests for h in edges),
        primary_with_p=sum(t.role == "primary" and t.p() is not None for t in tests),
    )


@dataclass
class Row:
    test: Test
    status: str
    z: float | None
    p: float
    holm: float
    bh: float
    bonf_all_looks: float
    bonf_primary: float | None
    dsr: float | None

    def survives(self, adjusted: float | None, alpha: float) -> bool:
        return adjusted is not None and adjusted <= alpha and bool(self.test.sign_ok())


def corrected_table(hyps: list[Hypothesis]) -> tuple[list[Row], Counts]:
    """Every test that has (or can derive) a p-value, with four corrected views:

    - holm / bh: over all tests with a p-value (m = number of such tests).
    - bonf_all_looks: Bonferroni with m = every look in the registry, recorded or not (the
      pessimistic view: we cannot Holm-adjust looks whose p we never wrote down).
    - bonf_primary: Bonferroni over the pre-declared primary tests only, m = all primary tests
      run (including primaries without a recorded p, whose p we cannot rank, hence Bonferroni
      rather than Holm). This is the confirmatory family.
    - dsr: deflated-Sharpe-style haircut, Phi(z_toward_prediction - E[max of N normals]) with
      N = every look in the registry. Probability-like; >= 0.95 clears the luck bar.
    """
    c = counts(hyps)
    status = {h.id: h.status for h in hyps}
    tests = [t for h in hyps if h.kind == "edge" for t in h.tests if t.p() is not None]
    if not tests:
        return [], c
    ps = [t.p() for t in tests]
    holm = multitest.holm(ps)
    bh = multitest.benjamini_hochberg(ps)
    bonf = multitest.bonferroni(ps, m=max(c.total_looks, len(ps)))
    prim_idx = [i for i, t in enumerate(tests) if t.role == "primary"]
    bonf_prim = {}
    if prim_idx:
        adj = multitest.bonferroni([ps[i] for i in prim_idx], m=max(c.primary_tests, len(prim_idx)))
        bonf_prim = dict(zip(prim_idx, adj, strict=True))
    rows = []
    for i, t in enumerate(tests):
        z = t.z()
        dsr = None
        if z is not None:
            toward = {"positive": z, "negative": -z, "either": abs(z)}[t.direction]
            dsr = multitest.deflated_z(toward, max(c.total_looks, 1))
        rows.append(
            Row(
                test=t,
                status=status[t.hypothesis],
                z=z,
                p=ps[i],
                holm=float(holm[i]),
                bh=float(bh[i]),
                bonf_all_looks=float(bonf[i]),
                bonf_primary=None if i not in bonf_prim else float(bonf_prim[i]),
                dsr=dsr,
            )
        )
    return rows, c


# --- rendering -------------------------------------------------------------------------------


def _fmt_p(p: float | None) -> str:
    if p is None:
        return "-"
    if p < 1e-4:
        return "<1e-4"
    return f"{p:.4f}" if p < 0.01 else f"{p:.3f}"


def _fmt_ci(t: Test) -> str:
    if t.estimate is None:
        return "-"
    if t.ci is None:
        return f"{t.estimate:+.1f}"
    return f"{t.estimate:+.1f} [{t.ci[0]:+.1f}, {t.ci[1]:+.1f}]"


def _yes(b: bool) -> str:
    return "yes" if b else "no"


def table_markdown(rows: list[Row], c: Counts, alpha: float = 0.05) -> str:
    head = (
        "| Hypothesis (status) | Test | Role | Estimate [95% CI] | p | Holm | BH q | "
        f"Bonf. (m={c.total_looks}) | Bonf. primary (m={c.primary_tests}) | DSR | "
        "Survives Holm | Survives BH |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|---|\n"
    )
    lines = []
    for r in sorted(rows, key=lambda r: (r.p, r.test.hypothesis, r.test.id)):
        t = r.test
        lines.append(
            f"| {t.hypothesis} ({r.status}) | {t.id} | {t.role} | {_fmt_ci(t)} | {_fmt_p(r.p)} | "
            f"{_fmt_p(r.holm)} | {_fmt_p(r.bh)} | {_fmt_p(r.bonf_all_looks)} | "
            f"{_fmt_p(r.bonf_primary)} | {'-' if r.dsr is None else f'{r.dsr:.2f}'} | "
            f"{_yes(r.survives(r.holm, alpha))} | {_yes(r.survives(r.bh, alpha))} |"
        )
    return head + "\n".join(lines) + "\n"


def counts_text(c: Counts) -> str:
    st = ", ".join(f"{k} {v}" for k, v in c.by_status.items())
    return (
        f"{c.hypotheses} hypotheses ({st}).\n"
        f"Edge hypotheses only: {c.recorded_tests} recorded tests, {c.tests_with_p} with a p-value "
        f"({c.p_reported} reported, {c.tests_with_p - c.p_reported} derived from a CI); "
        f"{c.unrecorded_looks} further looks not recorded individually; "
        f"{c.total_looks} looks in total.\n"
        f"{c.primary_tests} pre-declared primary tests, {c.primary_with_p} with a p-value."
    )


def summary_markdown(hyps: list[Hypothesis], alpha: float = 0.05) -> str:
    """The generated registry overview (committed as hypotheses/SUMMARY.md)."""
    rows, c = corrected_table(hyps)
    out = [
        "# Hypothesis registry summary",
        "",
        "Generated by `uv run drc-registry summary --write`; do not edit by hand. Source of truth:",
        "the TOML files in this directory. Method and caveats: `hypotheses/README.md`.",
        "",
        "## Counts",
        "",
        counts_text(c).replace("\n", "\n\n"),
        "",
        "## Hypotheses",
        "",
        "| ID | Status | Kind | Title | Tests | Looks | Rule frozen | Updated | Findings |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    order = {s: i for i, s in enumerate(("testing", "confirmed", "inconclusive", "killed"))}
    for h in sorted(hyps, key=lambda h: (order.get(h.status, 9), h.id)):
        links = ", ".join(f"[{Path(f).stem}](../{f})" for f in h.findings) or "-"
        alias = f" ({', '.join(h.aliases)})" if h.aliases else ""
        out.append(
            f"| {h.id}{alias} | {h.status} | {h.kind} | {h.title} | {len(h.tests)} | {h.looks} | "
            f"{_yes(h.rule_frozen)} | {h.updated} | {links} |"
        )
    out += [
        "",
        f"## Multiple-testing-corrected significance (alpha = {alpha})",
        "",
        "p is two-sided; where a finding gave only an estimate and 95% CI, p is derived from the",
        "CI (normal approximation, side facing zero). 'Survives' also requires the estimate to",
        "have the sign the hypothesis predicts. DSR = deflated-Sharpe-style probability with",
        f"N = {c.total_looks} trials (every look in the registry). Statistically surviving is",
        "not the same as being a tradeable edge: check status and the findings.",
        "",
        table_markdown(rows, c, alpha),
    ]
    return "\n".join(out)
