# Hypothesis registry

One TOML file per hypothesis: what we claim, the rule we froze, what would kill it, every test
we ran on it, and how many other cells we looked at without writing them down. It is our lab
notebook and the input to the multiple-testing correction. `SUMMARY.md` is generated from it.

```
uv run drc-registry validate          # schema check (CI runs it via tests/test_registry.py)
uv run drc-registry list [--status testing]
uv run drc-registry count             # tests run, looks, primaries
uv run drc-registry table             # corrected significance table
uv run drc-registry summary --write   # regenerate SUMMARY.md (a test fails if it is stale)
```

## When to touch it
- **New idea:** add `<id>.toml` with `status = "proposed"`, `rule_frozen = false`.
- **Before a test runs:** write the rule and kill criterion, set `rule_frozen = true`, status
  `testing`, commit. That commit's date is the proof the rule came first.
- **After a test:** add a `[[tests]]` entry per number the findings doc reports, and add every
  other cell you looked at to `[search] unrecorded_looks` (with a note saying what they were).
  Then `drc-registry summary --write`.
- **Decision:** set `status` to `killed`, `confirmed` or `inconclusive`, link the findings doc,
  bump `updated`. Never delete a killed hypothesis: it is part of the test count.

## Fields
| Field | Meaning |
|---|---|
| `id` | kebab-case, equals the file name |
| `kind` | `edge` (a claim of alpha; enters the correction family) or `measurement` (a data fact) |
| `status` | `proposed` / `testing` / `killed` / `confirmed` / `inconclusive` |
| `proposed`, `updated` | dates (TOML date literals) |
| `origin` | file where the idea was written down; `findings` = result docs |
| `rule`, `rule_frozen` | the trade/claim; frozen = written before the deciding data were seen |
| `kill_criterion` | what makes us drop it |
| `[search] unrecorded_looks` | cells/tests looked at but not recorded as `[[tests]]` |
| `[search] unrecorded_primary` | of those, how many were pre-declared primary tests |
| `[[tests]] role` | `primary` (the pre-declared decision test), `secondary` (pre-declared, reported), `exploratory` (found by looking), `diagnostic` (splits/checks) |
| `[[tests]] direction` | sign the hypothesis predicts: `positive` / `negative` / `either` |
| `[[tests]] estimate`, `ci` | as the findings report them (95% unless `ci_level`) |
| `[[tests]] p_value` | **only** if the findings report one. Never invent it |
| `[[tests]] result` | `pass` / `fail` / `descriptive`, judged by the rule at the time |
| `[[tests]] reported` | any other numbers the findings give (Brier, splits without CIs) |

## How the corrected table is computed
Our studies report estimates with cluster-bootstrap 95% intervals, not p-values. Where no p is
recorded, `drc-registry` derives a two-sided p from the estimate and the interval bound facing
zero (normal approximation), so a derived p is below 0.05 exactly when the interval excludes
zero. Tests with neither p nor CI count as looks but get no p. Only `edge` hypotheses count.

Views (all at alpha = 0.05; "survives" also needs the predicted sign):
- **Holm** and **BH** over every recorded test with a p (FWER and FDR respectively).
- **Bonferroni with m = every look**, recorded or not: the pessimistic family.
- **Bonferroni over pre-declared primary tests** (m includes primaries without a p): the
  confirmatory family. This is the right family for a pre-declared test on untouched data.
- **DSR**: deflated-Sharpe-style probability Phi(z - E[max of N standard normals]) with N = all
  looks: "would this result stand out if it were the best of every look we took?".

Caveats: corrections fix *chance*, not *bias* (the lookahead artifact survives every view) and
not *wrong standard errors* (if our CIs are too narrow, every p is too small). Looks within a
study share markets, so they are positively dependent; treating them as independent trials
over-penalizes. Unrecorded-look counts are approximate and counted generously.
